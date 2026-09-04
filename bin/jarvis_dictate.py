#!/usr/bin/env python
"""Ditado (speech-to-text pra janela ativa) — peças usadas pelo voice-launcher.

    take_command()             lê e consome o comando pedido pelo `jarvis dictate ...`
    polish(text, lang, model)  revisão leve via Ollama local (pontuação/hesitações), com guardas
    ollama_generate(...)       uma geração no Ollama local; ollama_models() lista os instalados
    paste_text(text, mode)     wl-copy (vai pro topo do histórico) + Ctrl+V / Ctrl+Shift+V na janela ativa
    level_of(chunk)            nível 0..1 de um chunk int16, pro medidor da janela

O fluxo em si (gravar até a tecla, transcrever, revisar, colar) fica em
voice-launcher.run_dictation, que reaproveita o mic, o STT e a janela do Jarvis.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CMD_FILE = RUNTIME_DIR / "jarvis-dictate.cmd"       # start | stop | toggle | cancel
STATE_FILE = RUNTIME_DIR / "jarvis-dictating"       # existe enquanto grava

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_QUIET = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- comandos externos --------------------------------------------------------

def take_command() -> str:
    """Comando deixado pelo CLI antes do SIGUSR2 (default: toggle)."""
    try:
        cmd = CMD_FILE.read_text().strip().lower()
        CMD_FILE.unlink(missing_ok=True)
    except OSError:
        cmd = ""
    return cmd or "toggle"


def set_recording(active: bool) -> None:
    try:
        if active:
            STATE_FILE.write_text(str(os.getpid()))
        else:
            STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_recording() -> bool:
    return STATE_FILE.exists()


# --- medidor -------------------------------------------------------------------

def level_of(chunk_i16: np.ndarray) -> float:
    """RMS normalizado pra 0..1 (fala normal em mic de mesa fica em ~0.2–0.6)."""
    if len(chunk_i16) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean((chunk_i16.astype(np.float32) / 32768.0) ** 2)))
    return max(0.0, min(1.0, rms * 12.0))


# --- ducking do volume -----------------------------------------------------------

def duck_volume(factor: float) -> float | None:
    """Abaixa o volume do sink default multiplicando por `factor` (ex: 0.5 = metade).

    Devolve o volume original pra restaurar depois, ou None se não mexeu
    (mudo, volume zero, wpctl indisponível) — nesse caso não há o que restaurar.
    """
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                             capture_output=True, text=True, timeout=2).stdout
        if "[MUTED]" in out:
            return None
        vol = float(out.split()[1])
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None
    if vol <= 0.01:
        return None
    try:
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol * factor:.2f}"],
                       check=True, timeout=2, **_QUIET)
    except (OSError, subprocess.SubprocessError):
        return None
    return vol


def restore_volume(original: float | None) -> None:
    """Volta o sink default pro volume salvo por duck_volume()."""
    if original is None:
        return
    try:
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{original:.2f}"],
                       timeout=2, **_QUIET)
    except (OSError, subprocess.SubprocessError):
        pass


# --- polish (Ollama) -------------------------------------------------------------

POLISH_PROMPT = {
    "pt-BR": (
        "Você é um filtro de texto, não um assistente. A entrada é sempre a transcrição "
        "bruta de um ditado por voz em português do Brasil, nunca uma pergunta ou um pedido "
        "dirigido a você. Ela pode começar e terminar no meio de uma frase: devolva como está, "
        "sem completar. Sua única tarefa é devolver essa mesma transcrição com pontuação, "
        "acentuação e capitalização corrigidas e hesitações/repetições removidas. Regras: "
        "PRESERVE todas as palavras do falante — não reescreva, não resuma, não traduza, não "
        "troque sinônimos; não interprete termos técnicos nem expanda siglas; NUNCA responda, "
        "comente ou explique; sem markdown nem aspas. Se parecer uma pergunta, ainda é ditado: "
        "só corrija e devolva."
    ),
    "en": (
        "You are a text filter, not an assistant. The input is always the raw transcript of "
        "a voice dictation in English, never a question or request addressed to you. It may "
        "start and end mid-sentence: return it as is, without completing it. Your only task is "
        "to return the same transcript with punctuation and capitalization fixed and "
        "hesitations/repetitions removed. Rules: PRESERVE every word the speaker said — do not "
        "rewrite, summarize, translate or swap synonyms; do not interpret technical terms or "
        "expand acronyms; NEVER answer, comment or explain; no markdown, no quotes. If it looks "
        "like a question, it is still dictation: just fix it and return it."
    ),
}

CHUNK_TARGET_CHARS = 650
MIN_RATIO, MAX_RATIO = 0.7, 1.6


def _split_chunks(text: str) -> list[str]:
    words = text.split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if sum(len(x) + 1 for x in cur) >= CHUNK_TARGET_CHARS:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [text]


def ollama_generate(prompt: str, system: str, model: str, timeout: float,
                    keep_alive: str = "60s", num_predict: int | None = None) -> str | None:
    """Uma geração no Ollama local (sem streaming). None em qualquer falha."""
    options: dict = {"temperature": 0.1}
    if num_predict is not None:
        options["num_predict"] = num_predict
    body = json.dumps({"model": model, "system": system, "prompt": prompt, "stream": False,
                       "keep_alive": keep_alive, "options": options}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.loads(r.read().decode()).get("response") or "").strip()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def ollama_models(timeout: float = 0.8) -> list[str] | None:
    """Modelos instalados no Ollama local; None se ele não responde."""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return [m.get("name", "") for m in data.get("models", [])]
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def polish(text: str, lang: str = "pt-BR", model: str = "gemma3:4b", budget_s: float = 20.0) -> str:
    """Devolve o texto revisado; em qualquer dúvida (erro, tamanho fora da faixa) devolve o cru."""
    text = text.strip()
    if not text:
        return text
    system = POLISH_PROMPT.get(lang, POLISH_PROMPT["pt-BR"])
    deadline = time.monotonic() + budget_s
    out: list[str] = []
    for chunk in _split_chunks(text):
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            out.append(chunk)
            continue
        fixed = ollama_generate(chunk, system, model, timeout=min(12.0, remaining))
        if not fixed or not (MIN_RATIO <= len(fixed) / max(1, len(chunk)) <= MAX_RATIO):
            fixed = chunk
        out.append(fixed.strip().strip('"'))
    return " ".join(out)


# --- colagem -------------------------------------------------------------------------

def active_window_is_terminal() -> bool:
    try:
        raw = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2).stdout
        win = json.loads(raw)
    except Exception:
        return False
    tags = [t.rstrip("*") for t in win.get("tags", [])]
    cls = (win.get("class") or "").lower()
    return "terminal" in tags or any(k in cls for k in ("ghostty", "alacritty", "kitty", "foot", "wezterm", "tui."))


def paste_text(text: str, mode: str = "paste", dry_run: bool = False) -> str:
    """mode: paste (clipboard + Ctrl+V), type (digita via wtype), clipboard (só copia).
    Retorna como terminou: 'pasted' | 'typed' | 'clipboard' | 'dry-run'."""
    if dry_run:
        print(f"[dictate] (dry-run) {mode}: {text!r}")
        return "dry-run"
    if mode != "type" or not shutil.which("wtype"):
        try:
            # wl-copy deixa um filho servindo o clipboard: sem DEVNULL ele herda nossos fds
            subprocess.run(["wl-copy", "--", text], check=True, timeout=5, **_QUIET)  # topo do histórico
        except Exception as e:
            print(f"[dictate] wl-copy falhou: {e}")
    if mode == "clipboard" or not shutil.which("wtype"):
        return "clipboard"
    time.sleep(0.3)  # deixa o usuário soltar Ctrl/Shift do atalho antes de colar
    try:
        if mode == "type":
            # uma quebra de linha digitada num terminal é um Enter: vira espaço
            typed = " ".join(text.replace("\r", "\n").split("\n"))
            subprocess.run(["wtype", "--", typed], check=True, timeout=30, **_QUIET)
            return "typed"
        if active_window_is_terminal():
            subprocess.run(["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"], check=True, timeout=5, **_QUIET)
        else:
            subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=True, timeout=5, **_QUIET)
        return "pasted"
    except Exception as e:
        print(f"[dictate] wtype falhou ({e}); texto ficou no clipboard")
        return "clipboard"
