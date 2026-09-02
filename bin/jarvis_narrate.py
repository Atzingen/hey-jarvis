#!/usr/bin/env python
"""Narração de progresso: enquanto o modelo trabalha, uma frase curta a cada N s.

    mode, why = resolve_mode(cfg)                      # "auto" -> local | openai | templates
    warm_up(cfg)                                       # pré-carrega o modelo local (thread)
    n = Narrator(mode, lang, interval, question,
                 generate=build_generate(mode, cfg, lang))
    n.feed(kind, text)        # cada evento do CLI (tool / thinking / text), thread do modelo
    phrase = n.poll()         # loop de espera: frase pra falar, ou None
    n.spoke()                 # algo foi dito (narração, "pensando senhor", consentimento)
    n.close()                 # resposta chegou / cancelado

Cadeia de quem gera a frase: narrador (Ollama local ou OpenAI) → linha de
progresso que o próprio modelo escreveu (evento text) → template fixo pelo
tipo do último evento. Guardas: uma linha, ≤ 140 caracteres, sem marcador,
sem markdown, diferente da frase anterior.

CLI:  python jarvis_narrate.py status
      python jarvis_narrate.py replay <eventos.jsonl> <codex|claude> [modo]
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis_dictate  # noqa: E402
import jarvis_events  # noqa: E402
from jarvis_i18n import NARRATOR_PROMPT, T  # noqa: E402

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_PHRASE_CHARS = 140
MAX_EVENTS = 8
MAX_EVENT_CHARS = 160
LOCAL_TIMEOUT = 3.0
OPENAI_TIMEOUT = 4.0
MAX_BACKEND_FAILURES = 2   # falhas seguidas até o backend ser desligado nesta conversa

Event = tuple[str, str]
Generate = Callable[[str, list[Event]], "str | None"]


def log(msg: str) -> None:
    print(f"[narr] {msg}", flush=True)


# --- templates e guardas ---------------------------------------------------------

_READ_RE = re.compile(r"\b(Read|cat|sed|head|tail|less|bat)\b")
_SEARCH_RE = re.compile(r"\b(Grep|rg|grep|Glob|find|fd|ag)\b")
_WEB_RE = re.compile(r"\b(WebSearch|WebFetch|curl|wget|http)\b")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")


def _first_sentence(text: str, limit: int = MAX_PHRASE_CHARS) -> str:
    text = " ".join(text.split())
    first = _SENTENCE_END_RE.split(text, maxsplit=1)[0]
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return first


def template_for(kind: str | None, text: str, lang: str) -> str:
    """Frase fixa pelo tipo do evento; kind=None é o período seco ("ainda pensando")."""
    if kind == "text" and text.strip():
        return _first_sentence(text)
    if kind == "tool":
        if _READ_RE.search(text):
            return T(lang, "narr_read")
        if _SEARCH_RE.search(text):
            return T(lang, "narr_search")
        if _WEB_RE.search(text):
            return T(lang, "narr_web")
        return T(lang, "narr_command")
    if kind == "thinking":
        return T(lang, "narr_thinking")
    return T(lang, "narr_dry")


def clean_output(text: str | None) -> str | None:
    """Guardas da saída do narrador. None = reprovada."""
    if not text:
        return None
    if "\n" in text.strip() or "<<" in text:
        return None
    out = text.strip().strip('"\'“”‘’').replace("**", "").replace("`", "").replace("*", "").strip()
    if not out or len(out) > MAX_PHRASE_CHARS:
        return None
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


# --- narrador ---------------------------------------------------------------------

class Narrator:
    """Estado da narração de UMA pergunta. `feed` vem da thread do modelo; `poll`,
    `spoke`, `close` da thread principal. `generate(question, events)` roda em
    thread própria com timeout; enquanto gera, `poll` devolve None."""

    def __init__(self, mode: str, lang: str, interval: float, question: str,
                 generate: Generate | None = None, clock: Callable[[], float] = time.monotonic,
                 generate_timeout: float = OPENAI_TIMEOUT):
        self.mode = mode
        self.lang = lang
        self.interval = float(interval)
        self.question = question
        self.generate = generate if mode in ("local", "openai") else None
        self.generate_timeout = generate_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._last_spoken = clock()
        self._last_phrase: str | None = None
        self._dry_said = False
        self._closed = False
        # geração pendente
        self._snapshot: list[Event] = []
        self._result: str | None = None
        self._done: threading.Event | None = None
        self._started_real = 0.0

    def feed(self, kind: str, text: str) -> None:
        if kind == "result":
            # resposta final emitida: o CLI ainda leva 1-2 s pra sair; não narrar mais
            self._closed = True
            return
        text = " ".join(str(text).split())[:MAX_EVENT_CHARS]
        with self._lock:
            self._events.append((kind, text))
            del self._events[:-MAX_EVENTS]

    def spoke(self) -> None:
        self._last_spoken = self._clock()

    def close(self) -> None:
        self._closed = True
        self._done = None

    def poll(self) -> str | None:
        if self.mode == "off" or self._closed:
            return None
        if self._done is not None:
            if self._done.is_set():
                self._done = None
                return self._finish(self._result, self._snapshot)
            if time.monotonic() - self._started_real > self.generate_timeout:
                log("narrador demorou — fallback")
                self._done = None
                return self._finish(None, self._snapshot)
            return None
        now = self._clock()
        if now - self._last_spoken < self.interval:
            return None
        with self._lock:
            snapshot = self._events[:]
            self._events.clear()
        if snapshot:
            self._dry_said = False
            if self.generate is None:
                return self._finish(None, snapshot)
            self._start_generation(snapshot)
            return None
        if now - self._last_spoken >= 2 * self.interval and not self._dry_said:
            self._dry_said = True
            return self._dedupe(template_for(None, "", self.lang))
        return None

    def wait(self, timeout: float) -> str | None:
        """Bloqueia até a geração pendente terminar (testes e replay)."""
        if self._done is not None:
            self._done.wait(timeout)
        return self.poll()

    def _start_generation(self, snapshot: list[Event]) -> None:
        done = threading.Event()
        self._snapshot = snapshot
        self._result = None
        self._done = done
        self._started_real = time.monotonic()

        def run() -> None:
            try:
                out = self.generate(self.question, snapshot)  # type: ignore[misc]
            except Exception as e:  # backend nunca deve derrubar a conversa
                log(f"narrador falhou: {e}")
                out = None
            if self._done is done:
                self._result = out
            done.set()

        threading.Thread(target=run, daemon=True, name="narrator").start()

    def _fallback(self, snapshot: list[Event]) -> str:
        for kind, text in reversed(snapshot):
            if kind == "text" and text.strip():
                return template_for("text", text, self.lang)
        kind, text = snapshot[-1] if snapshot else (None, "")
        return template_for(kind, text, self.lang)

    def _finish(self, generated: str | None, snapshot: list[Event]) -> str | None:
        phrase = clean_output(generated) or self._fallback(snapshot)
        return self._dedupe(phrase)

    def _dedupe(self, phrase: str) -> str | None:
        if self._last_phrase is not None and _norm(phrase) == _norm(self._last_phrase):
            return None
        self._last_phrase = phrase
        return phrase


# --- backends ---------------------------------------------------------------------

def _events_prompt(question: str, events: list[Event], lang: str) -> str:
    lines = []
    for kind, text in events:
        label = jarvis_events.status_label(kind, text, lang=lang)
        if label:
            lines.append("- " + label)
    head = "User question" if lang == "en" else "Pergunta do usuário"
    body = "Assistant's latest actions" if lang == "en" else "Últimas ações do assistente"
    return f"{head}: {question}\n{body}:\n" + "\n".join(lines)


def _guarded(backend: Generate, name: str) -> Generate:
    """Desliga o backend após MAX_BACKEND_FAILURES falhas seguidas (não fica pagando timeout)."""
    failures = 0
    dead = False

    def generate(question: str, events: list[Event]) -> str | None:
        nonlocal failures, dead
        if dead:
            return None
        t0 = time.monotonic()
        out = backend(question, events)
        dt = time.monotonic() - t0
        if out:
            failures = 0
            log(f"{name} {dt:.1f}s: {out}")
            return out
        failures += 1
        log(f"{name} falhou ({dt:.1f}s) — fallback")
        if failures >= MAX_BACKEND_FAILURES:
            dead = True
            log(f"{name} desligado até o fim da conversa")
        return None

    return generate


def local_generate(model: str, lang: str) -> Generate:
    system = NARRATOR_PROMPT[lang]

    def generate(question: str, events: list[Event]) -> str | None:
        return jarvis_dictate.ollama_generate(_events_prompt(question, events, lang), system, model,
                                              timeout=LOCAL_TIMEOUT, keep_alive="5m", num_predict=40)

    return generate


def openai_text(payload: dict) -> str | None:
    """Texto do primeiro item `message` de uma resposta da Responses API."""
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and part.get("text"):
                return part["text"].strip()
    return None


def openai_generate(api_key: str, model: str, lang: str) -> Generate:
    system = NARRATOR_PROMPT[lang]

    def generate(question: str, events: list[Event]) -> str | None:
        body = json.dumps({"model": model, "instructions": system,
                           "input": _events_prompt(question, events, lang),
                           "max_output_tokens": 60,
                           "reasoning": {"effort": "low"}}).encode()
        req = urllib.request.Request(OPENAI_RESPONSES_URL, data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=OPENAI_TIMEOUT) as r:
                return openai_text(json.loads(r.read().decode()))
        except urllib.error.HTTPError as e:
            log(f"openai HTTP {e.code}: {e.read()[:200]!r}")
            return None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None

    return generate


def _openai_key(cfg: dict) -> str:
    return cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")


def build_generate(mode: str, cfg: dict, lang: str) -> Generate | None:
    if mode == "local":
        return _guarded(local_generate(cfg["narration_local_model"], lang), "ollama")
    if mode == "openai":
        return _guarded(openai_generate(_openai_key(cfg), cfg["narration_openai_model"], lang), "openai")
    return None


# --- detecção automática --------------------------------------------------------

def _cuda_available() -> bool:
    import jarvis_stt
    jarvis_stt.preload_cuda_libs()
    return jarvis_stt.cuda_available()


def resolve_mode(cfg: dict, ollama_models=None, cuda=None) -> tuple[str, str]:
    """(modo efetivo, motivo). `auto` verifica Ollama + modelo instalado + GPU, senão chave."""
    want = cfg.get("narration", "auto")
    if want != "auto":
        return want, "configurado"
    models = (ollama_models or jarvis_dictate.ollama_models)()
    model = cfg.get("narration_local_model", "gemma3:4b")
    key = _openai_key(cfg)
    if models is None:
        reason = "Ollama não responde"
    elif not any(m == model or m.split(":")[0] == model for m in models):
        reason = f"modelo {model} não está no Ollama"
    elif not (cuda or _cuda_available)():
        reason = "sem GPU CUDA"
    else:
        return "local", f"Ollama com {model} e GPU"
    if key:
        return "openai", f"{reason} — chave OpenAI presente"
    return "templates", f"{reason} e sem chave OpenAI"


def warm_up(cfg: dict) -> None:
    """Carrega o modelo local em background pra 1ª narração não pagar a carga."""
    model = cfg.get("narration_local_model", "gemma3:4b")

    def run() -> None:
        t0 = time.monotonic()
        ok = jarvis_dictate.ollama_generate("ok", "", model, timeout=30.0, keep_alive="5m", num_predict=1)
        log(f"warm-up {model}: {'ok' if ok is not None else 'falhou'} ({time.monotonic() - t0:.1f}s)")

    threading.Thread(target=run, daemon=True, name="narrator-warmup").start()


# --- CLI ------------------------------------------------------------------------

def _cli_status() -> None:
    import jarvis_config
    cfg = jarvis_config.load()
    mode, why = resolve_mode(cfg)
    print(f"narration={cfg.get('narration')} → modo efetivo: {mode} ({why})")


def _cli_replay(path: str, provider: str, mode: str | None) -> None:
    """Reproduz um jsonl de eventos (um evento a cada 1,5 s simulados) e imprime as narrações."""
    import jarvis_config
    cfg = jarvis_config.load()
    lang = cfg.get("language", "pt-BR")
    mode = mode or resolve_mode(cfg)[0]
    clock = [0.0]
    n = Narrator(mode, lang, cfg.get("narration_interval_quick", 8), "(replay)",
                 generate=build_generate(mode, cfg, lang), clock=lambda: clock[0])
    print(f"modo {mode}, intervalo {n.interval:.0f}s")
    for line in Path(path).read_text().splitlines():
        parsed = jarvis_events.parse_line(provider, line)
        if not parsed or parsed[0] == "result":
            continue
        n.feed(*parsed)
        clock[0] += 1.5
        print(f"  {clock[0]:5.1f}s  · {jarvis_events.status_label(*parsed, lang=lang)}")
        phrase = n.poll()
        if phrase is None and n._done is not None:
            phrase = n.wait(10.0)
        if phrase:
            print(f"  {clock[0]:5.1f}s  ▶ {phrase}")
            n.spoke()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "status":
        _cli_status()
    elif len(sys.argv) >= 4 and sys.argv[1] == "replay":
        _cli_replay(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        print(__doc__)
        sys.exit(2)
