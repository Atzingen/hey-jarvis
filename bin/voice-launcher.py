#!/usr/bin/env python
"""
voice-launcher: 'hey jarvis' + fala -> o modelo decide -> ação/resposta.

Não há palavras-chave: tudo que você diz vai pro modelo, que entende a
intenção e responde falando e/ou acionando marcadores que o launcher executa
(ver ACTION_PROTOCOL): <<ABRIR_PROJETO: x>>, <<ABRIR_APP: x>>, <<DORMIR>>,
<<FIM>>. Pedidos livres sobre a máquina ele executa sozinho (system_access).
Exceções mínimas, locais: "fim"/"pausa" como palavra solta e o prefixo
"pense bem", que só escolhe o modelo mais forte.

A captura de fala é por VAD (silero, embutido no faster-whisper): grava
enquanto você fala e encerra após END_SILENCE_SECONDS de silêncio contínuo,
sem tempo fixo. Depois de cada resposta abre uma janela de
FOLLOWUP_SECONDS pra emendar a próxima fala sem repetir a wake word;
o contexto das trocas anteriores vai junto pro modelo.

Durante a fase busy (pensando/falando a resposta), falar por cima interrompe
o Jarvis e a fala é capturada e encadeada na conversa (barge-in, com gate de
energia pra ele não se auto-interromper ouvindo a própria voz na caixa).

Uma janela persistente (alacritty float + jarvis-window.py) abre no wake e
acompanha a conversa inteira: fase atual com countdown, últimas trocas e
dicas de voz/tecla no rodapé. Tecla q/Esc na janela encerra a conversa.
Trabalho que estoura o prazo (HANDOFF_SECONDS_*) não é morto: é entregue a
um terminal rascunho separado, onde segue executando até terminar.
"""

import argparse
import json
import os
import signal
import difflib
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper.vad import get_vad_model
from openwakeword.model import Model as WakeModel

import jarvis_config  # mesmo diretório (~/.local/bin)
import jarvis_consent
import jarvis_dictate
import jarvis_events
import jarvis_narrate
import jarvis_i18n
import jarvis_stt
from jarvis_i18n import T

# Tudo que é ajustável vem de ~/.config/jarvis/config.toml (UI: `jarvis config`);
# os defaults e a descrição de cada chave estão em jarvis_config.SETTINGS.
CFG = jarvis_config.load()


def _hits(spec: str) -> tuple[int, int]:
    need, window = spec.split("/")
    return int(need), int(window)


SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms @ 16kHz (openWakeWord default)
LANG = jarvis_i18n.norm_lang(CFG["language"])
DEV_DIR = Path(CFG["dev_dir"]).expanduser()
VOICE_NAME = CFG["voice"] if CFG["voice"] != "auto" else jarvis_i18n.DEFAULT_VOICE[LANG]
VOICE = jarvis_config.VOICES_DIR / f"{VOICE_NAME}.onnx"
VOICE_LENGTH_SCALE = CFG["voice_length_scale"]
LAYOUT_SCRIPT = Path(CFG["layout_script"]).expanduser()
WAKE_WORD = CFG["wake_word"]
WAKE_THRESHOLD = CFG["wake_threshold"]
GREETING = CFG["greeting"] or T(LANG, "greeting")

# Captura por VAD: fim de fala = silêncio contínuo, não tempo fixo.
VAD_FRAME = 2560              # 160ms @ 16kHz — múltiplo de 512 exigido pelo silero
VAD_SPEECH_THRESHOLD = CFG["vad_speech_threshold"]
END_SILENCE_SECONDS = CFG["end_silence_seconds"]
MAX_UTTERANCE_SECONDS = CFG["max_utterance_seconds"]
FIRST_SPEECH_WAIT_SECONDS = CFG["first_speech_wait_seconds"]
PREROLL_CHUNKS = CFG["preroll_chunks"]

# Conversa encadeada: após cada resposta, janela pra follow-up sem wake word.
FOLLOWUP_SECONDS = CFG["followup_seconds"]
MAX_HISTORY_EXCHANGES = CFG["max_history_exchanges"]

# Barge-in: fala do usuário durante a fase busy interrompe e encadeia.
# Medido em 2026-08-28 (QuadCast + caixas): ambiente rms≈0.0045, vazamento do
# TTS no mic rms≈0.0066 (pico 0.011) — o mic quase não ouve as caixas.
BARGE_MIN_RMS = CFG["barge_min_rms"]
TTS_BLEED_FACTOR = CFG["tts_bleed_factor"]
BARGE_TTS_WARMUP_FRAMES = CFG["barge_tts_warmup_frames"]
BARGE_HITS_TTS = _hits(CFG["barge_hits_tts"])
BARGE_HITS_IDLE = _hits(CFG["barge_hits_idle"])
BARGE_DEBUG = CFG["barge_debug"]

# Janela persistente da conversa (alacritty float rodando jarvis-window.py).
WINDOW_ENABLED = CFG["window_enabled"]
_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE_FILE = _RUNTIME_DIR / "jarvis-state.json"
QUIT_FLAG = _RUNTIME_DIR / "jarvis-quit"
WAKE_OFF_FILE = _RUNTIME_DIR / "jarvis-wake-off"  # existe = sem escuta da wake word (só atalhos)
VIEWER_SCRIPT = Path(__file__).resolve().parent / "jarvis-window.py"

# Acesso do modelo à máquina: "ask" (default) roda o CLI sem bypass e cada ação
# que altera algo passa por jarvis_consent (janela com o comando exato);
# "full" = --dangerously-* como antes; "off" = sem tools / sandbox só leitura.
SYSTEM_ACCESS = CFG["system_access"]
CONSENT_TIMEOUT = jarvis_consent.DEFAULT_TIMEOUT  # s até negar sozinho
CONSENT_MCP = Path(__file__).resolve().parent / "jarvis_consent_mcp.py"
# cwd do CLI em "ask": pasta vazia própria do plugin (o modelo não tem tool de
# leitura; tudo passa pela tool `run` do servidor MCP, com autorização).
ASK_WORKDIR = jarvis_consent.WORKDIR
# arquivos de trabalho de cada chamada ao modelo (resposta, eventos, stderr,
# pergunta): só no runtime dir do usuário (0700), nunca em /tmp compartilhado
MODEL_TMP_DIR = _RUNTIME_DIR / "jarvis-model"
# Transcrições, respostas e texto ditado só vão pro log (journal) se o usuário
# pedir; por padrão o log traz tamanhos e tempos.
LOG_TEXT = bool(CFG.get("log_transcripts", False))

# Prazo até entregar trabalho lento a um terminal rascunho (segue rodando lá).
HANDOFF_SECONDS_QUICK = CFG["handoff_seconds_quick"]
HANDOFF_SECONDS_DEEP = CFG["handoff_seconds_deep"]

# Narração de progresso: a cada N s sem fala, uma frase curta do que o modelo
# está fazendo (jarvis_narrate). "auto" é resolvido no início de cada conversa.
NARRATION_MODE = CFG["narration"]
NARRATION_INTERVAL_QUICK = CFG["narration_interval_quick"]
NARRATION_INTERVAL_DEEP = CFG["narration_interval_deep"]

# Prompt de sistema compartilhado pelos dois provedores (vazio = padrão do idioma).
CLAUDE_SYSTEM = CFG["system_prompt"] or jarvis_i18n.SYSTEM_PROMPT[LANG]

# Perguntas rápidas (não-deep): "codex" (Codex CLI) ou "claude" (Claude Code CLI).
# "pense bem" sempre usa o Claude Code CLI com deep_model/deep_effort.
QUICK_PROVIDER = CFG["quick_provider"]

# Executor pra rodar ask_model em paralelo com o ack falado.
# Single worker: no máximo uma pergunta em voo por vez.
claude_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude")

# Interrupção por hotword: durante TTS/espera do modelo, se "hey jarvis"
# dispara de novo, cancelamos o que está rolando. Threshold elevado em relação
# ao wake normal pra reduzir falso-positivo do próprio TTS vazando pro mic.
INTERRUPT_THRESHOLD_BOOST = CFG["interrupt_threshold_boost"]

# --- utils -----------------------------------------------------------

def _txt(text: str, n: int = 200) -> str:
    """Texto pro log: o conteúdo só com log_transcripts = true; senão o tamanho."""
    text = str(text)
    return repr(text[:n]) if LOG_TEXT else f"<{len(text)} chars>"


def private_tmp(prefix: str, suffix: str) -> Path:
    """Arquivo temporário 0600 em $XDG_RUNTIME_DIR/jarvis-model (dir 0700)."""
    MODEL_TMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(MODEL_TMP_DIR))
    os.close(fd)
    return Path(name)


def sweep_model_tmp(max_age: float = 24 * 3600) -> None:
    """Remove restos de chamadas antigas (handoffs que ninguém fechou)."""
    if not MODEL_TMP_DIR.is_dir():
        return
    now = time.time()
    for p in MODEL_TMP_DIR.iterdir():
        try:
            if now - p.stat().st_mtime > max_age:
                p.unlink()
        except OSError:
            pass


def list_projects() -> list[str]:
    """Subpastas reais de dev_dir (sem symlinks: um link pra fora da pasta não
    vira "projeto" que o dev-layout abre)."""
    if not DEV_DIR.is_dir():
        return []
    return sorted(p.name for p in DEV_DIR.iterdir()
                  if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def match_project(text: str) -> str | None:
    projs = list_projects()
    text_n = _norm(text)
    if not text_n:
        return None
    # 1) substring (ambos sentidos)
    for p in projs:
        pn = _norm(p)
        if pn and (pn in text_n or text_n in pn):
            return p
    # 2) fuzzy
    norms = [_norm(p) for p in projs]
    m = difflib.get_close_matches(text_n, norms, n=1, cutoff=0.55)
    if m:
        return projs[norms.index(m[0])]
    return None


APP_DIRS = ("/usr/share/applications", "~/.local/share/applications")


def launch_detached(cmd: list[str], env: dict | None = None) -> None:
    """Lança processo fora do cgroup do serviço (sobrevive a restart do Jarvis).

    uwsm-app coloca o app num scope próprio em app-graphical.slice, como o
    launcher do Omarchy faz; fallback systemd-run --scope. O umask volta ao
    padrão (o serviço roda com UMask=0077, que não deve vazar pros apps).
    """
    if shutil.which("uwsm-app"):
        full = ["uwsm-app", "--"] + cmd
    elif shutil.which("systemd-run"):
        full = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"] + cmd
    else:
        full = cmd
    subprocess.Popen(full, start_new_session=True, env=env,
                     preexec_fn=lambda: os.umask(0o022),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def scoped(cmd: list[str], unit: str | None = None) -> list[str]:
    """Prefixo pra rodar `cmd` num scope transiente próprio (fora do cgroup do
    serviço) mantendo o pid real como filho direto — systemd-run --scope faz
    exec no comando, então proc.pid/kill continuam válidos. Com `unit`, o scope
    tem nome fixo e `stop_scope(unit)` mata tudo que ele contém."""
    if shutil.which("systemd-run"):
        extra = [f"--unit={unit}"] if unit else []
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", *extra, "--"] + cmd
    return cmd


def stop_scope(unit: str | None, proc: subprocess.Popen) -> None:
    """Cancela um trabalho do modelo inteiro: o CLI, o que ele lançou (bash, o
    servidor de consentimento), qualquer autorização pendente e o grant
    "permitir o resto desta pergunta"."""
    jarvis_consent.cancel_all()
    jarvis_consent.revoke_grants(unit)
    finish_scope(unit)
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def finish_scope(unit: str | None) -> None:
    """Encerra o scope de uma chamada já respondida: nada que o modelo deixou
    rodando (um comando autorizado em background) sobrevive à resposta."""
    if unit and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def tui_launch_cmd(cmd: list[str]) -> list[str]:
    """Comando pra abrir um app de terminal como o Omarchy faz (Super+Ctrl+T):
    omarchy-launch-tui -> janela flutuante com app-id org.omarchy.<app>."""
    if shutil.which("omarchy-launch-tui"):
        return ["omarchy-launch-tui"] + cmd
    return ["alacritty", "-e"] + cmd


def match_application(query: str) -> tuple[str, list[str], bool] | None:
    """Procura aplicativo instalado (desktop entries; fallback: PATH).

    Retorna (nome_falado, comando, terminal) ou None.
    """
    q = _norm(query)
    if not q:
        return None

    entries: list[tuple[str, str, str, bool]] = []  # (stem, name, exec, terminal)
    for d in APP_DIRS:
        for f in Path(d).expanduser().glob("*.desktop"):
            try:
                txt = f.read_text(errors="ignore")
            except OSError:
                continue
            if "NoDisplay=true" in txt:
                continue
            name = exec_ = None
            terminal = False
            is_app = False
            for line in txt.splitlines():
                if line.startswith("Name=") and name is None:
                    name = line[5:].strip()
                elif line.startswith("Exec=") and exec_ is None:
                    exec_ = line[5:].strip()
                elif line.startswith("Terminal=") and "true" in line.lower():
                    terminal = True
                elif line.strip() == "Type=Application":
                    is_app = True
            if exec_ and is_app:
                entries.append((f.stem, name or f.stem, exec_, terminal))

    best = None
    # 1) igual, ou a fala contida no nome ("chrome" -> "google chrome")
    for entry in entries:
        for key in (_norm(entry[0]), _norm(entry[1])):
            if key and (key == q or q in key):
                best = entry
                break
        if best:
            break
    # 2) nome contido na fala — só nomes com tamanho razoável (evita "R" ⊂ "chrome")
    if best is None:
        for entry in entries:
            for key in (_norm(entry[0]), _norm(entry[1])):
                if len(key) >= 4 and key in q:
                    best = entry
                    break
            if best:
                break
    if best is None:
        keyed = []
        for entry in entries:
            keyed.append((_norm(entry[0]), entry))
            keyed.append((_norm(entry[1]), entry))
        m = difflib.get_close_matches(q, [k for k, _ in keyed], n=1, cutoff=0.7)
        if m:
            best = next(e for k, e in keyed if k == m[0])

    if best:
        _stem, name, exec_, terminal = best
        cmd = shlex.split(re.sub(r"%[a-zA-Z]", "", exec_).strip())
        return (name, cmd, terminal)
    # sem fallback pro PATH: a allowlist são as desktop entries instaladas
    return None


def parse_command(text: str):
    """
    Roteamento mínimo do texto transcrito. Quem entende a intenção é o modelo
    (ver ACTION_PROTOCOL); aqui só ficam os dois comandos de uma palavra e o
    prefixo "pense bem", que escolhe o modelo mais forte.

    Retorna (kind, payload):
        ("ask",   ("<fala>", deep: bool))
        ("end",   None)   palavra solta: fim / encerrar / chega
        ("hush",  None)   palavra solta: pausa / pare / quieto
        ("noop",  None)   transcrição vazia
    """
    if not text or not text.strip():
        return ("noop", None)

    t_norm = _norm(text)
    t_low = text.lower().strip()

    if t_norm in ("fim", "encerrar", "encerra", "chega", "fimdaconversa"):
        return ("end", None)
    if t_norm in ("pausa", "pause", "para", "pare", "quieto", "silencio", "calado"):
        return ("hush", None)

    if re.search(r"\bpense[\s\-]?bem\b", t_low):
        q = re.sub(r"\bpense[\s\-]?bem\b[,\s]*", "", t_low, count=1).strip()
        return ("ask", (q or text, True))
    return ("ask", (text, False))


# --- protocolo de ações (o modelo decide, o launcher executa) ---------------

# Um marcador é uma linha inteira, e só conta nas linhas FINAIS da resposta
# (o protocolo pede assim). Um marcador citado no meio do texto — conteúdo de um
# arquivo que o modelo leu, por exemplo — não executa nada.
ACTION_LINE_RE = re.compile(r"^\s*<<\s*(FIM|DORMIR|ABRIR_PROJETO|ABRIR_APP)\s*(?::\s*(.*?))?\s*>>\s*$")
# palavras que precisam estar na FALA do usuário pra <<DORMIR>> valer
SUSPEND_WORDS_RE = re.compile(r"\b(dormir|durma|suspend\w*|hibern\w*|sleep|descans\w*)\b", re.I)


def action_protocol(access: str, provider: str) -> str:
    """Trecho anexado ao prompt de sistema: as ações que o launcher executa, o
    resumo do ambiente (quando o modelo pode agir na máquina) e como funciona a
    autorização no modo `ask` — que difere por provedor: o Claude Code pede
    permissão por tool (servidor MCP); o Codex fica em sandbox e propõe
    comandos pela tool `run` do servidor MCP, que pede consentimento."""
    projects = ", ".join(list_projects()) or "(none)"
    text = jarvis_i18n.ACTIONS_PROTOCOL[LANG].format(projects=projects)
    if access in ("full", "ask"):
        text += jarvis_i18n.ENVIRONMENT_NOTES[LANG]
    if access == "ask":
        text += jarvis_i18n.ACCESS_NOTES[LANG]["ask"]
    elif access == "off":
        text += jarvis_i18n.ACCESS_NOTES[LANG]["off"]
    if NARRATION_MODE == "self":
        text += jarvis_i18n.SELF_NARRATION_NOTES[LANG]
    return text


def parse_actions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Separa os marcadores de ação do texto falado. Só as linhas finais da
    resposta que são marcadores contam. Retorna (texto, [(ação, arg)])."""
    lines = text.rstrip().splitlines()
    actions: list[tuple[str, str]] = []
    while lines:
        if not lines[-1].strip():
            lines.pop()
            continue
        m = ACTION_LINE_RE.match(lines[-1])
        if not m:
            break
        actions.insert(0, (m.group(1), (m.group(2) or "").strip()))
        lines.pop()
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return clean, actions


def allowed_actions(actions: list[tuple[str, str]], spoken_question: str,
                    access: str) -> list[tuple[str, str]]:
    """Filtra o que o launcher aceita executar: em `off` só <<FIM>>; <<DORMIR>>
    só quando a própria fala do usuário pede pra dormir/suspender."""
    out = []
    for kind, arg in actions:
        if access == "off" and kind != "FIM":
            print(f"[act]  {kind} ignorado (system_access = off)")
            continue
        if kind == "DORMIR" and not SUSPEND_WORDS_RE.search(spoken_question or ""):
            print("[act]  DORMIR ignorado (a fala não pediu pra suspender)")
            continue
        out.append((kind, arg))
    return out


def with_context(question: str, history: list[tuple[str, str]]) -> str:
    """Prefixa a pergunta com as últimas trocas da conversa atual."""
    if not history:
        return question
    lines = []
    for q, a in history[-MAX_HISTORY_EXCHANGES:]:
        lines.append(f"Usuário: {q}")
        lines.append(f"Jarvis: {a[:1200]}")
    if LANG == "en":
        lines = [l.replace("Usuário:", "User:", 1) for l in lines]
        return ("Ongoing voice conversation. Previous exchanges:\n" + "\n".join(lines)
                + f"\n\nNew user message (answer only this one): {question}")
    return (
        "Conversa por voz em andamento. Trocas anteriores:\n"
        + "\n".join(lines)
        + f"\n\nNova pergunta do usuário (responda apenas a ela): {question}"
    )


# --- janela persistente ----------------------------------------------

class JarvisWindow:
    """Janela persistente da conversa (alacritty float rodando jarvis-window.py).

    O launcher só escreve o estado em STATE_FILE; o viewer renderiza fase,
    countdown, trocas e dicas. Tecla q/Esc no viewer cria QUIT_FLAG (encerra
    a conversa) e fecha a janela; o viewer sai sozinho no estado "closed".
    """

    def __init__(self, enabled: bool | None = None):
        self.state: dict = {}
        self.enabled = WINDOW_ENABLED if enabled is None else enabled

    def open(self, mode: str = "conversation", phase: str = "listening") -> None:
        if not self.enabled:
            return
        QUIT_FLAG.unlink(missing_ok=True)
        self.state = {"mode": mode, "phase": phase, "detail": "", "deadline": None,
                      "exchanges": [], "lang": LANG}
        self._write()
        try:
            launch_detached(["alacritty", "--class", "TUI.float", "--title", "Jarvis",
                             "-e", "python3", str(VIEWER_SCRIPT)])
        except FileNotFoundError:
            print("   [janela: alacritty não encontrado]")

    def update(self, phase: str | None = None, **fields) -> None:
        if not self.enabled:
            return
        if phase is not None:
            self.state["phase"] = phase
            self.state["deadline"] = fields.pop("deadline", None)
        self.state.update(fields)
        self._write()

    def add_exchange(self, question: str, answer: str, label: str) -> None:
        if not self.enabled:
            return
        self.state.setdefault("exchanges", []).append(
            {"q": question, "a": answer, "label": label})
        self._write()

    def set_last_answer(self, answer: str) -> None:
        if not self.enabled:
            return
        exchanges = self.state.get("exchanges")
        if exchanges:
            exchanges[-1]["a"] = answer
            self._write()

    def close(self) -> None:
        if not self.enabled:
            return
        self.update(phase="closed")

    def _write(self) -> None:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state))
            tmp.replace(STATE_FILE)
        except OSError as e:
            print(f"   [janela: falha escrevendo estado: {e}]")


def quit_requested() -> bool:
    return QUIT_FLAG.exists()


def consume_quit() -> None:
    QUIT_FLAG.unlink(missing_ok=True)


# --- modelo ----------------------------------------------------------

def model_label(deep: bool) -> str:
    if deep:
        return f"claude {CFG['deep_model']}/{CFG['deep_effort']}"
    if QUICK_PROVIDER == "codex":
        fast = "/fast" if CFG["codex_fast"] else ""
        return f"codex {CFG['codex_model'] or 'default'}/{CFG['codex_effort']}{fast}"
    return f"claude {CFG['claude_quick_model']}/{CFG['claude_quick_effort']}"


# Features do Codex desligadas fora do modo `full` (codex features list).
CODEX_DISABLED_FEATURES = ("shell_tool", "unified_exec", "view_image", "multi_agent",
                           "plugins", "memories", "skill_search", "apps", "image_generation",
                           "computer_use", "browser_use")
# Flags que o modo seguro exige do CLI; sem elas o acesso fica indisponível
# (fail closed), nunca degrada pra uma invocação sem restrição.
CLI_REQUIRED_FLAGS = {"claude": ("--restricted", "--strict-mcp-config", "--tools"),
                      "codex": ("--ignore-user-config", "--ignore-rules", "--disable", "--sandbox")}
_cli_ok_cache: dict[str, bool] = {}


def cli_supports_safe_mode(provider: str) -> bool:
    if provider in _cli_ok_cache:
        return _cli_ok_cache[provider]
    args = [provider, "--help"] if provider == "claude" else ["codex", "exec", "--help"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    ok = all(flag in out for flag in CLI_REQUIRED_FLAGS[provider])
    _cli_ok_cache[provider] = ok
    return ok


def _build_ask_call(question: str, deep: bool, ans: Path,
                    spoken_question: str = "", unit: str = "") -> tuple[list[str], str, dict]:
    """Monta o comando do CLI. Retorna (cmd, provider, kwargs extras do Popen).

    Os dois CLIs rodam em modo JSON por linha no stdout (eventos: comandos
    executados, raciocínio, mensagens) — é isso que alimenta os "pensamentos"
    na janela. codex ainda escreve a resposta final em `ans` via -o.

    system_access:
      full — Claude --dangerously-skip-permissions / Codex --dangerously-bypass-…
      ask  — Claude em --permission-mode default (sobrescreve o defaultMode das
             settings do usuário) com --permission-prompt-tool apontando pro
             servidor MCP de consentimento; Codex em sandbox read-only, e propõe
             comandos pela tool `run` do servidor MCP (consentimento por comando).
      off  — os dois sem tool nenhuma (Codex ainda dentro do sandbox read-only).
    """
    access = SYSTEM_ACCESS
    popen: dict = {}
    mcp_ctx = None
    if access == "ask":
        ASK_WORKDIR.mkdir(parents=True, exist_ok=True)
        mcp_ctx = private_tmp("jarvis-ctx-", ".json")
        mcp_ctx.write_text(json.dumps({"lang": LANG, "question": spoken_question,
                                       "call_id": unit, "timeout": CONSENT_TIMEOUT},
                                      ensure_ascii=False))
        popen["cwd"] = str(ASK_WORKDIR)
        popen["env"] = {**os.environ, "JARVIS_CTX": str(mcp_ctx)}
    if deep or QUICK_PROVIDER == "claude":
        system = CLAUDE_SYSTEM + action_protocol(access, "claude")
        model = CFG["deep_model"] if deep else CFG["claude_quick_model"]
        effort = CFG["deep_effort"] if deep else CFG["claude_quick_effort"]
        # a pergunta vem logo depois de -p: --tools / --allowedTools são
        # variádicos e engoliriam um argumento posicional que viesse depois
        cmd = ["claude", "-p", question,
               "--model", model,
               "--effort", effort,
               "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", system]
        if access == "full":
            cmd += ["--dangerously-skip-permissions"]   # roda comandos sem confirmar
        else:
            # --restricted: ignora settings/hooks/plugins do usuário, recusa bypass,
            # prende as tools de arquivo ao cwd; --tools "" tira TODAS as built-in
            # (Bash, Read, Write…); --strict-mcp-config: só o MCP daqui.
            cmd += ["--restricted", "--tools", "",
                    "--strict-mcp-config", "--permission-mode", "manual",
                    "--no-session-persistence"]
            if access == "ask":
                mcp = {"mcpServers": {"jarvis": {"type": "stdio", "command": "python3",
                                                 "args": [str(CONSENT_MCP)]}}}
                cmd += ["--mcp-config", json.dumps(mcp), "--allowedTools", "mcp__jarvis__run"]
            else:
                cmd += ["--mcp-config", json.dumps({"mcpServers": {}})]
        return cmd, "claude", popen
    # codex escreve a resposta via -o (stdout traz eventos do agente)
    system = CLAUDE_SYSTEM + action_protocol(access, "codex")
    prompt = f"{system}\n\n{'User said' if LANG == 'en' else 'Fala do usuário'}: {question}"
    cmd = ["codex", "exec",
           "--skip-git-repo-check",
           "--ephemeral",
           "--json",
           "-c", f"model_reasoning_effort={CFG['codex_effort']}",
           "-c", f"service_tier={'fast' if CFG['codex_fast'] else 'default'}"]
    if access == "full":
        cmd += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        # sem config/regras/MCPs do usuário; sandbox read-only do Codex por baixo;
        # shell, exec unificado, sub-agentes, plugins, web e afins desligados:
        # o modelo fica sem tool própria de execução ou leitura
        cmd += ["--ignore-user-config", "--ignore-rules",
                "--sandbox", "read-only", "-c", "approval_policy=never",
                "-c", 'web_search="disabled"', "-c", "agents.max_depth=0",
                "-c", "include_apply_patch_tool=false"]
        for feat in CODEX_DISABLED_FEATURES:
            cmd += ["--disable", feat]
        if access == "ask":
            cmd += ["-c", 'mcp_servers.jarvis.command="python3"',
                    "-c", f"mcp_servers.jarvis.args=[{json.dumps(str(CONSENT_MCP))}]",
                    "-c", f"mcp_servers.jarvis.env={{JARVIS_CTX={json.dumps(str(mcp_ctx))}}}",
                    "-c", 'mcp_servers.jarvis.default_tools_approval_mode="approve"']
        ASK_WORKDIR.mkdir(parents=True, exist_ok=True)
        cmd += ["-C", str(ASK_WORKDIR)]
    if CFG["codex_model"]:
        cmd += ["-m", CFG["codex_model"]]
    cmd += ["-o", str(ans), prompt]
    return cmd, "codex", popen


def run_action(kind: str, arg: str, args, window) -> tuple[str, bool]:
    """Executa uma ação decidida pelo modelo. Retorna (aviso_falado, encerra_conversa)."""
    if kind == "FIM":
        return "", True

    if kind == "DORMIR":
        window.add_exchange(T(LANG, "action"), T(LANG, "suspend"), T(LANG, "cmd_label"))
        if args.test:
            print("[test] NAO executou systemctl suspend")
        else:
            subprocess.Popen(["systemctl", "suspend"])
        return "", True

    if kind == "ABRIR_PROJETO":
        project = match_project(arg)
        if not project:
            return T(LANG, "project_not_found", name=arg), False
        window.add_exchange(T(LANG, "action"), T(LANG, "opening_project", name=project), T(LANG, "cmd_label"))
        if args.test:
            print(f"[test] NAO lancou dev-layout {project}")
        else:
            # os terminais do layout só abrem o claude sem confirmações em `full`
            env = dict(os.environ)
            env["DEV_LAYOUT_CLAUDE_ARGS"] = "--dangerously-skip-permissions" if SYSTEM_ACCESS == "full" else ""
            launch_detached([str(LAYOUT_SCRIPT), project], env=env)
        return "", False

    if kind == "ABRIR_APP":
        app = match_application(arg)
        if not app:
            return T(LANG, "app_not_found", name=arg), False
        name, cmd, terminal = app
        launch_cmd = tui_launch_cmd(cmd) if terminal else cmd
        window.add_exchange(T(LANG, "action"), T(LANG, "opening_app", name=name), T(LANG, "cmd_label"))
        if args.test:
            print(f"[test] NAO lancou app: {launch_cmd}")
        else:
            print(f"[app]  {launch_cmd}")
            launch_detached(launch_cmd)
        return "", False

    return "", False


def ask_model(question: str, deep: bool, cancel: threading.Event | None = None,
              on_status=None, spoken_question: str = "", on_event=None):
    """Roda o CLI do modelo; eventos em streaming vão pra `on_status(texto)`
    (rótulo pronto pra janela) e `on_event(kind, texto)` (evento cru, narração).

    Retorna (resposta, None) quando conclui dentro do prazo;
    ("", handoff) quando estourou HANDOFF_SECONDS_* — o processo SEGUE
    rodando e handoff tem proc/answer_file/events_file/provider/label;
    ("", None) quando cancelado (o scope inteiro do CLI é parado).
    """
    label = model_label(deep)
    unit = f"jarvis-model-{os.getpid()}-{int(time.time() * 1000) % 10**8}"
    provider_guess = "claude" if (deep or QUICK_PROVIDER == "claude") else "codex"
    if SYSTEM_ACCESS != "full" and not cli_supports_safe_mode(provider_guess):
        return (T(LANG, "access_unavailable", cli=provider_guess), None)
    ans = private_tmp("jarvis-ans-", ".txt")
    ev = private_tmp("jarvis-ev-", ".jsonl")
    err = private_tmp("jarvis-err-", ".txt")

    cmd, provider, popen_kwargs = _build_ask_call(question, deep, ans, spoken_question, unit)
    ctx = Path(popen_kwargs.get("env", {}).get("JARVIS_CTX", "")) if popen_kwargs.get("env") else None

    def cleanup():
        for p in (ans, ev, err, ctx):
            if p:
                p.unlink(missing_ok=True)
        jarvis_consent.revoke_grants(unit)

    if shutil.which(cmd[0]) is None:
        cleanup()
        return (f"{cmd[0]} não encontrado no ambiente.", None)
    try:
        out_f = open(ev, "w")
        err_f = open(err, "w")
        # scope próprio (nomeado): um restart do Jarvis não mata um trabalho longo
        # em voo, e cancelar para o scope inteiro (CLI + filhos).
        # stdin fechado: o codex fica esperando entrada se herdar um stdin aberto.
        proc = subprocess.Popen(scoped(cmd, unit), stdin=subprocess.DEVNULL,
                                stdout=out_f, stderr=err_f, **popen_kwargs)
        out_f.close()
        err_f.close()
    except Exception as e:
        cleanup()
        return (f"Erro ao consultar: {e}", None)

    stop_tail = threading.Event()

    def tail_events() -> None:
        pos = 0
        while not stop_tail.is_set():
            try:
                with open(ev) as f:
                    f.seek(pos)
                    for line in f:
                        pos += len(line.encode())
                        parsed = jarvis_events.parse_line(provider, line)
                        if not parsed:
                            continue
                        if on_event is not None:
                            on_event(*parsed)
                        if on_status is not None:
                            status = jarvis_events.status_label(*parsed, lang=LANG)
                            if status:
                                on_status(status)
            except OSError:
                pass
            stop_tail.wait(0.3)

    if on_status is not None or on_event is not None:
        threading.Thread(target=tail_events, daemon=True, name="events-tail").start()

    def final_text() -> str:
        out = ans.read_text().strip() if ans.exists() else ""
        if not out:
            out = jarvis_events.final_answer(provider, ev)
        return out

    limit = HANDOFF_SECONDS_DEEP if deep else HANDOFF_SECONDS_QUICK
    deadline = time.monotonic() + limit
    try:
        while True:
            if proc.poll() is not None:
                time.sleep(0.1)  # deixa o último flush do arquivo de eventos assentar
                out = final_text()
                emsg = err.read_text().strip()[:200] if err.exists() else ""
                cleanup()
                finish_scope(unit)  # nada fica rodando depois da resposta
                if out:
                    return (out, None)
                return (f"Sem resposta. {emsg}" if emsg else "Sem resposta.", None)
            if cancel is not None and cancel.is_set():
                stop_scope(unit, proc)
                cleanup()
                return ("", None)
            if time.monotonic() > deadline:
                # handoff: o trabalho segue, mas sem "permitir o resto" herdado
                jarvis_consent.revoke_grants(unit)
                return ("", {"proc": proc, "answer_file": ans, "events_file": ev, "err_file": err,
                             "ctx_file": ctx, "provider": provider, "label": label,
                             "question": question})
            time.sleep(0.2)
    finally:
        stop_tail.set()


def open_handoff_terminal(handoff: dict) -> None:
    """Terminal rascunho: acompanha o trabalho longo que continua rodando."""
    ans = handoff["answer_file"]
    ev = handoff["events_file"]
    pid = handoff["proc"].pid
    label = handoff["label"]
    events_script = Path(__file__).resolve().parent / "jarvis_events.py"

    qfile = private_tmp("jarvis-q-", ".txt")
    try:
        qfile.write_text(handoff["question"])
    except OSError:
        qfile.unlink(missing_ok=True)
        qfile = None

    # o script follow imprime pergunta/eventos/resposta via safe_text e apaga
    # os arquivos da chamada ao terminar (ctx/err também)
    extra = [str(p) for p in (handoff.get("err_file"), handoff.get("ctx_file")) if p]
    shell_cmd = (
        'clear; echo; '
        f'echo "  {T(LANG, "handoff_title")} ({shlex.quote(label)})"; echo; '
        f'python3 {shlex.quote(str(events_script))} follow {shlex.quote(str(ev))} '
        f'{handoff["provider"]} {pid} {shlex.quote(str(ans))} {LANG} '
        f'{shlex.quote(str(qfile)) if qfile else "-"} {" ".join(shlex.quote(e) for e in extra)}; '
        f'echo "  {T(LANG, "handoff_close")}"; read -n 1 -s -r'
    )
    try:
        launch_detached(["alacritty", "--class", "TUI.float", "--title", "Jarvis — rascunho",
                         "-e", "bash", "-c", shell_cmd])
    except FileNotFoundError:
        print("   [rascunho: alacritty não encontrado]")


# --- áudio -----------------------------------------------------------

def chime(freq_start=880, freq_end=None, ms=120, vol=0.25) -> None:
    """Gera sine wave e toca (blocking)."""
    n = int(SAMPLE_RATE * ms / 1000)
    t = np.linspace(0, ms / 1000, n, False)
    if freq_end is None:
        freq = np.full(n, freq_start)
    else:
        freq = np.linspace(freq_start, freq_end, n)
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase) * vol
    fade = int(0.01 * SAMPLE_RATE)
    wave[:fade] *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    sd.play(wave.astype(np.float32), SAMPLE_RATE, blocking=True)


class BargeInListener:
    """Thread da fase busy (TTS + espera do modelo): lê o mic e seta `fired` se
    (a) a wake word dispara, ou (b) o usuário fala por cima (VAD + gate de energia).

    No disparo por fala, `speech_chunks` guarda o áudio desde o onset (com
    pré-roll) pro caller continuar a captura; no disparo por wake word fica None.

    Gate de energia: enquanto `tts_playing` (setado pelo tts()), o nível do
    vazamento do TTS pro mic é calibrado nos primeiros frames de cada fala e
    depois só acompanha frames ABAIXO do gate (nunca aprende com a voz do
    usuário); a fala precisa superar TTS_BLEED_FACTOR vezes esse nível.
    Evita o Jarvis se auto-interromper ouvindo a própria voz na caixa.

    Assume consumo exclusivo do stream enquanto ativa — o loop principal
    não deve ler o mesmo stream em paralelo.
    """

    def __init__(self, stream, wake, vad_model, wake_threshold: float):
        self.stream = stream
        self.wake = wake
        self.vad_model = vad_model
        self.wake_threshold = wake_threshold
        self.tts_playing = False
        self.fired = False
        self.speech_chunks: list | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.fired = False
        self.speech_chunks = None
        self._stop.clear()
        self.wake.reset()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        recent: deque = deque(maxlen=int(1.5 * SAMPLE_RATE / CHUNK))  # ~1.5s
        pending = np.empty(0, dtype=np.float32)
        bleed_rms = 0.0
        warmup_left = 0
        hits: deque = deque(maxlen=4)  # 1 = frame passou (VAD + energia)
        tts_prev = False
        dbg_t0 = time.monotonic()
        dbg_max_rms = 0.0
        dbg_vad = 0
        dbg_n = 0
        try:
            while not self._stop.is_set():
                data, _ = self.stream.read(CHUNK)
                chunk = data.flatten()
                recent.append(chunk)

                pred = self.wake.predict(chunk)
                score = max(pred.values()) if pred else 0.0
                if score > self.wake_threshold:
                    print(f"[int!]  wake word durante resposta (score={score:.2f})")
                    self.fired = True
                    return

                tts_now = self.tts_playing
                if tts_now != tts_prev:
                    # mudou de fase: recalibra o vazamento do zero
                    warmup_left = BARGE_TTS_WARMUP_FRAMES if tts_now else 0
                    bleed_rms = 0.0
                    hits.clear()
                tts_prev = tts_now

                pending = np.concatenate([pending, chunk.astype(np.float32) / 32768.0])
                while len(pending) >= VAD_FRAME:
                    frame, pending = pending[:VAD_FRAME], pending[VAD_FRAME:]
                    rms = float(np.sqrt(np.mean(frame * frame)))
                    if tts_now:
                        if warmup_left > 0:
                            # calibração: o pico dos primeiros frames é o nível do vazamento
                            bleed_rms = max(bleed_rms, rms)
                            warmup_left -= 1
                            continue
                        gate = max(bleed_rms * TTS_BLEED_FACTOR, BARGE_MIN_RMS)
                        if rms < gate:
                            # só frames abaixo do gate atualizam o vazamento (nunca a voz do usuário)
                            bleed_rms = max(bleed_rms * 0.97, rms)
                        need, window = BARGE_HITS_TTS
                    else:
                        gate = BARGE_MIN_RMS
                        need, window = BARGE_HITS_IDLE
                    prob = float(self.vad_model(frame).max())
                    passed = prob >= VAD_SPEECH_THRESHOLD and rms >= gate
                    hits.append(1 if passed else 0)

                    dbg_n += 1
                    dbg_max_rms = max(dbg_max_rms, rms)
                    dbg_vad += int(prob >= VAD_SPEECH_THRESHOLD)
                    if BARGE_DEBUG and time.monotonic() - dbg_t0 >= 1.0:
                        print(f"[barge] tts={int(tts_now)} rms_max={dbg_max_rms:.4f} "
                              f"gate={gate:.4f} vad={dbg_vad}/{dbg_n} hits={list(hits)}")
                        dbg_t0 = time.monotonic()
                        dbg_max_rms = 0.0
                        dbg_vad = 0
                        dbg_n = 0

                    if sum(list(hits)[-window:]) >= need:
                        print(f"[int!]  fala do usuário detectada (rms={rms:.4f}, gate={gate:.4f}, prob={prob:.2f})")
                        self.speech_chunks = list(recent)
                        self.fired = True
                        return
        except Exception as e:
            print(f"   [listener erro: {e}]")


def tts(text: str, listener: "BargeInListener | None" = None, stop_when=None) -> bool:
    """Fala texto via piper -> paplay. Retorna True se interrompido pelo listener.
    `stop_when()` verdadeiro corta a fala sem contar como interrupção (narração
    de progresso quando a resposta chega)."""
    wav_path = private_tmp("jarvis-tts-", ".wav")
    wav = str(wav_path)
    try:
        subprocess.run(
            ["piper", "-m", str(VOICE),
             "--length-scale", str(VOICE_LENGTH_SCALE),
             "-f", wav],
            input=text.encode(),
            check=True, capture_output=True, timeout=15,
        )
    except Exception as e:
        print(f"   [tts gen falhou: {e}]")
        wav_path.unlink(missing_ok=True)
        return False

    try:
        proc = subprocess.Popen(["paplay", wav])
    except FileNotFoundError:
        wav_path.unlink(missing_ok=True)
        return False
    threading.Thread(target=_unlink_after, args=(proc, wav_path), daemon=True).start()

    def cut() -> None:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if listener is None:
        deadline = time.monotonic() + 30
        while proc.poll() is None:
            if (stop_when is not None and stop_when()) or time.monotonic() > deadline:
                cut()
                break
            time.sleep(0.05)
        return False

    listener.tts_playing = True
    try:
        deadline = time.monotonic() + 60  # hard cap: TTS nunca deveria passar disso
        while proc.poll() is None:
            if stop_when is not None and stop_when():
                cut()
                return False
            if listener.fired:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return True
            if time.monotonic() > deadline:
                print("   [tts: timeout absoluto, matando paplay]")
                proc.kill()
                return False
            time.sleep(0.05)
        return listener.fired
    finally:
        listener.tts_playing = False


def _unlink_after(proc: subprocess.Popen, path: Path) -> None:
    try:
        proc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        pass
    path.unlink(missing_ok=True)


def flush_stream(stream: sd.InputStream, chunks: int = 5) -> None:
    """Descarta áudio residual do buffer (TTS vazado pro mic, fala antiga)."""
    for _ in range(chunks):
        stream.read(CHUNK)


def record_until_silence(
    stream: sd.InputStream,
    vad_model,
    wait_seconds: float,
    silence_seconds: float = END_SILENCE_SECONDS,
    max_seconds: float = MAX_UTTERANCE_SECONDS,
    initial_chunks: list | None = None,
    on_speech_start=None,
    abort_check=None,
    on_chunk=None,
) -> np.ndarray | None:
    """Espera a fala começar (até wait_seconds) e grava até silêncio contínuo.

    initial_chunks: fala já capturada (barge-in) — entra como início da
    gravação e a função só completa o resto até o silêncio.
    on_speech_start: callback chamado uma vez quando a fala começa.
    abort_check: callable; True aborta a espera (retorna None).
    on_chunk: recebe cada chunk int16 gravado (pré-roll incluso) — é por aqui
    que o áudio flui pro reconhecimento em streaming enquanto você fala.

    Retorna float32 [-1,1] com ~0.5s de pré-roll, ou None se ninguém falou.
    """
    preroll: deque = deque(maxlen=PREROLL_CHUNKS)
    recorded: list[np.ndarray] = []
    pending = np.empty(0, dtype=np.float32)
    speech_started = False
    started_at = 0.0
    last_speech = 0.0
    t0 = time.monotonic()

    def emit(chunks) -> None:
        if on_chunk is not None:
            for c in chunks:
                on_chunk(c)

    if initial_chunks:
        recorded = list(initial_chunks)
        emit(recorded)
        speech_started = True
        started_at = t0
        last_speech = t0

    while True:
        now = time.monotonic()
        if abort_check is not None and abort_check():
            return None
        if not speech_started:
            if now - t0 > wait_seconds:
                return None
        else:
            if now - last_speech > silence_seconds:
                break
            if now - started_at > max_seconds:
                print("   [rec: teto de duração atingido]")
                break

        data, _ = stream.read(CHUNK)
        chunk = data.flatten()
        if speech_started:
            recorded.append(chunk)
            emit([chunk])
        else:
            preroll.append(chunk)

        pending = np.concatenate([pending, chunk.astype(np.float32) / 32768.0])
        while len(pending) >= VAD_FRAME:
            frame, pending = pending[:VAD_FRAME], pending[VAD_FRAME:]
            prob = float(vad_model(frame).max())
            if prob >= VAD_SPEECH_THRESHOLD:
                if not speech_started:
                    speech_started = True
                    started_at = now
                    recorded.extend(preroll)
                    emit(preroll)
                    if on_speech_start is not None:
                        on_speech_start()
                last_speech = now

    if not recorded:
        return None
    audio = np.concatenate(recorded).astype(np.float32) / 32768.0
    print(f"   [rec: {len(audio)/SAMPLE_RATE:.1f}s, rms={float(np.sqrt(np.mean(audio*audio))):.4f}]")
    return audio


def stt_terms() -> list[str]:
    """Vocabulário passado ao reconhecimento (hotwords/keywords): nomes que a
    transcrição costuma errar — o próprio Jarvis, apps e os projetos."""
    base = ["Jarvis", "hey Jarvis", "btop", "Hyprland", "Ghostty", "Alacritty", "Omarchy",
            "Codex", "Claude", "Docker", "systemd", "Python", "GitHub"]
    try:
        return base + list_projects()
    except OSError:
        return base


# --- conversa --------------------------------------------------------

def run_conversation(stream, wake, stt, vad_model, args) -> None:
    """Do prompt inicial até o fim da conversa.

    A janela persistente abre no início e acompanha tudo. Cada resposta
    reabre a escuta por FOLLOWUP_SECONDS. Falar por cima da resposta
    interrompe e a fala encadeia na conversa (barge-in). A conversa termina
    com "fim", tecla q/Esc na janela, comando terminal (sleep/open),
    transcrição vazia ou janela de follow-up expirada sem fala.
    """
    window = JarvisWindow()
    window.open()
    try:
        tts(GREETING)
        flush_stream(stream)

        narr_mode, narr_why = jarvis_narrate.resolve_mode(CFG)
        print(f"[narr] modo={narr_mode} ({narr_why})")
        if narr_mode == "local":
            jarvis_narrate.warm_up(CFG)

        history: list[tuple[str, str]] = []
        followup = False
        pending_audio: np.ndarray | None = None  # fala capturada por barge-in
        pending_chunks: list[np.ndarray] = []     # a mesma fala, em chunks int16 (pro stt)

        while True:
            # sessão de reconhecimento por fala: abre já (a conexão da API fica
            # pronta enquanto você começa a falar); o texto provisório vai pra janela
            session = stt.begin(on_partial=lambda t: window.update(partial=t))

            if pending_audio is not None:
                # barge-in: a fala já foi capturada inteira pelo listener + record
                audio = pending_audio
                pending_audio = None
                for c in pending_chunks:
                    session.feed(c)
            else:
                wait = FOLLOWUP_SECONDS if followup else FIRST_SPEECH_WAIT_SECONDS
                window.update(phase="followup" if followup else "listening",
                              deadline=time.time() + wait, partial="")
                print(f"[rec]  aguardando fala (janela {wait:.0f}s, fim após {END_SILENCE_SECONDS}s de silêncio)...")
                audio = record_until_silence(
                    stream, vad_model, wait_seconds=wait,
                    on_speech_start=lambda: window.update(phase="recording"),
                    abort_check=lambda: quit_requested() or dictate_requested(),
                    on_chunk=session.feed,
                )

            if audio is None:
                session.close()
                if dictate_requested():
                    print("[conv] cedendo o microfone pro ditado")
                    return
                if quit_requested():
                    consume_quit()
                    print("[conv] encerrada pela tecla na janela")
                elif followup:
                    print("[conv] janela de follow-up expirou — conversa encerrada")
                    chime(660, 440, ms=100, vol=0.12)
                else:
                    tts(T(LANG, "not_heard"))
                return

            window.update(phase="transcribing")
            print(f"[stt]  transcrevendo {len(audio)/SAMPLE_RATE:.1f}s de fala...")
            t0 = time.time()
            text = session.finish()
            window.update(partial="")
            print(f"[stt]  {_txt(text)} ({time.time()-t0:.1f}s)")

            kind, payload = parse_command(text)
            print(f"[cmd]  {kind}")

            if kind == "end":
                print("[conv] encerrada por comando de voz")
                chime(660, 440, ms=100, vol=0.12)
                return

            if kind == "hush":
                print("[conv] pausa — só escutando")
                followup = True
                flush_stream(stream)
                continue

            if kind == "noop":
                if followup:
                    # provavelmente ruído captado na janela — encerra sem drama
                    print("[conv] transcrição vazia no follow-up — conversa encerrada")
                    return
                tts(T(LANG, "not_understood"))
                return

            # kind == "ask"
            question, deep = payload
            label = model_label(deep)
            print(f"[ask]  provider={label} q={_txt(question)}")
            t0 = time.time()

            if args.test:
                chime(880, 1320, ms=100, vol=0.15)
                window.add_exchange(question, T(LANG, "test_answer"), label)
                tts(T(LANG, "test_answer"))
                followup = True
                flush_stream(stream)
                continue

            question_ctx = with_context(question, history)
            cancel = threading.Event()
            thoughts: list[str] = []
            narrator = jarvis_narrate.Narrator(
                narr_mode, LANG,
                NARRATION_INTERVAL_DEEP if deep else NARRATION_INTERVAL_QUICK, question,
                generate=jarvis_narrate.build_generate(narr_mode, CFG, LANG))

            def on_status(status: str) -> None:
                thoughts.append(status)
                window.update(thoughts=thoughts[-4:])

            fut = claude_executor.submit(ask_model, question_ctx, deep, cancel, on_status,
                                         question, narrator.feed)

            # a pergunta entra na janela já na transcrição; a resposta preenche depois
            window.add_exchange(question, "…", label)
            window.update(phase="thinking", detail=label, thoughts=[])

            # fase busy: listener assume leitura do mic (wake word OU fala = barge-in)
            listener = BargeInListener(
                stream, wake, vad_model,
                args.wake_threshold + INTERRUPT_THRESHOLD_BOOST,
            )
            listener.start()

            # ack curto no lugar de repetir a pergunta: chime pra rápida, aviso pra deep
            if deep:
                interrupted = tts(T(LANG, "thinking"), listener)
            else:
                chime(880, 1320, ms=100, vol=0.15)
                interrupted = listener.fired
            narrator.spoke()

            consent_seen: str | None = None  # id do pedido de autorização já anunciado
            while not interrupted and not fut.done():
                if listener.fired:
                    interrupted = True
                    break
                if quit_requested() or dictate_requested():
                    if quit_requested():
                        consume_quit()
                    cancel.set()
                    jarvis_consent.cancel_all()
                    listener.stop()
                    print("[conv] encerrada durante a espera (tecla na janela ou ditado)")
                    return
                # modo ask: o modelo pede autorização → avisa por voz e marca a fase
                pend = jarvis_consent.pending()
                if pend is not None and pend.get("id") != consent_seen:
                    consent_seen = pend.get("id")
                    print(f"[ask]  autorização pendente: {_txt(pend.get('summary', ''), 120)}")
                    window.update(phase="consent", detail=label)
                    if tts(T(LANG, "consent_needed"), listener):
                        interrupted = True
                        break
                    narrator.spoke()
                elif pend is None and consent_seen is not None and window.state.get("phase") == "consent":
                    window.update(phase="thinking", detail=label)
                elif pend is None:
                    # narração de progresso: uma frase curta do que o modelo está fazendo
                    phrase = narrator.poll()
                    if phrase and not fut.done():
                        print(f"[narr] ▶ {phrase}")
                        thoughts.append(f"{T(LANG, 'ev_narr')}: {phrase}")
                        window.update(thoughts=thoughts[-4:])
                        if tts(phrase, listener, stop_when=fut.done):
                            interrupted = True
                            break
                        narrator.spoke()
                time.sleep(0.1)
            narrator.close()

            resposta = ""
            handoff = None
            if not interrupted:
                try:
                    resposta, handoff = fut.result(timeout=5)
                except Exception as e:
                    resposta, handoff = f"Erro inesperado: {e}", None
                elapsed = time.time() - t0

                if handoff is not None:
                    # estourou o prazo: o trabalho segue num terminal rascunho
                    print(f"[ans]  handoff após {elapsed:.0f}s [{label}] — terminal rascunho")
                    open_handoff_terminal(handoff)
                    window.set_last_answer(T(LANG, "handoff_window"))
                    window.update(phase="handoff", detail=label)
                    history.append((question, T(LANG, "handoff_history")))
                    interrupted = tts(T(LANG, "handoff"), listener)
                else:
                    resposta, actions = parse_actions(resposta)
                    actions = allowed_actions(actions, question, SYSTEM_ACCESS)
                    print(f"[ans]  {elapsed:.1f}s [{label}] {actions or ''} :: {_txt(resposta)}")
                    window.set_last_answer(resposta or T(LANG, "action"))
                    # executa o que o modelo decidiu; avisos (ex.: projeto não achado) entram na fala
                    end_requested = False
                    notices: list[str] = []
                    for kind_a, arg_a in actions:
                        notice, ends = run_action(kind_a, arg_a, args, window)
                        if notice:
                            notices.append(notice)
                        end_requested = end_requested or ends
                    # fala só o resumo; o detalhe (após linha ---) vai só pra janela
                    parts = re.split(r"\n\s*-{3,}\s*\n", resposta, maxsplit=1)
                    spoken_answer = " ".join([parts[0].strip()] + notices).strip() or resposta
                    if not spoken_answer and actions:
                        spoken_answer = T(LANG, "done")
                    window.update(phase="speaking", detail=label)
                    history.append((question, resposta or T(LANG, "action_done")))
                    interrupted = tts(spoken_answer, listener) if spoken_answer else False
                    if interrupted:
                        print("[int]  resposta interrompida")
                    elif end_requested:
                        listener.stop()
                        print("[conv] encerrada por decisão do modelo (FIM/DORMIR)")
                        chime(660, 440, ms=100, vol=0.12)
                        return

            listener.stop()

            if interrupted:
                cancel.set()  # para o scope do claude/codex em voo (não afeta handoff já entregue)
                jarvis_consent.cancel_all()
                if not resposta and handoff is None:
                    window.set_last_answer(T(LANG, "interrupted"))
                if listener.speech_chunks is not None:
                    # barge-in por fala: completa a captura e encadeia na conversa
                    print("[int]  fala do usuário — capturando o resto da fala")
                    window.update(phase="recording", partial="")
                    pending_chunks = []
                    pending_audio = record_until_silence(
                        stream, vad_model, wait_seconds=0,
                        initial_chunks=listener.speech_chunks,
                        on_chunk=pending_chunks.append,
                    )
                else:
                    print("[int]  wake word — escutando de novo")
                    tts(T(LANG, "yes_sir"))
                    flush_stream(stream)
            else:
                flush_stream(stream)

            followup = True
    finally:
        window.close()


# --- ditado ------------------------------------------------------------
# SIGUSR2 = comando de ditado (`jarvis dictate start|stop|toggle|cancel` deixa o
# verbo em jarvis_dictate.CMD_FILE antes de sinalizar). Ditado tem prioridade
# sobre uma conversa em andamento: ela é encerrada e o mic passa pro ditado.
DICT = threading.Event()


def _on_sigusr2(signum, frame):
    DICT.set()


def dictate_requested() -> bool:
    return DICT.is_set()


def run_dictation(stream, stt, args, duck: bool = False) -> None:
    """Grava até o comando de parar, transcreve, revisa (opcional) e cola na janela ativa."""
    window = JarvisWindow(enabled=CFG["dictation_window"])
    window.open(mode="dictation", phase="dictating")
    # Abaixa a música enquanto grava (só no toggle) e restaura assim que parar de falar.
    ducked_from = jarvis_dictate.duck_volume(CFG["dictation_duck"]) if duck and CFG["dictation_duck"] < 1.0 else None
    jarvis_dictate.set_recording(True)
    session = stt.begin(on_partial=lambda t: window.update(partial=t))
    levels: deque = deque(maxlen=120)
    outcome = "stop"
    t0 = time.monotonic()
    last_ui = 0.0
    print("[dict] gravando — pare com o mesmo atalho")
    chime(880, 1320, ms=60, vol=0.12)
    try:
        while True:
            if DICT.is_set():
                DICT.clear()
                cmd = jarvis_dictate.take_command()
                if cmd == "cancel":
                    outcome = "cancel"
                    break
                if cmd in ("stop", "toggle"):
                    break
            if time.monotonic() - t0 > CFG["dictation_max_seconds"]:
                print("[dict] teto de duração atingido")
                break
            data, _ = stream.read(CHUNK)
            chunk = data.flatten()
            session.feed(chunk)
            levels.append(jarvis_dictate.level_of(chunk))
            now = time.monotonic()
            if now - last_ui > 0.1:
                window.update(levels=list(levels))
                last_ui = now
    finally:
        jarvis_dictate.set_recording(False)
        jarvis_dictate.restore_volume(ducked_from)

    if outcome == "cancel":
        session.close()
        print("[dict] cancelado")
        window.update(phase="cancelled")
        time.sleep(0.8)
        window.close()
        return

    window.update(phase="transcribing")
    t1 = time.time()
    text = session.finish()
    print(f"[dict] {time.monotonic()-t0:.1f}s de áudio -> {len(text)} chars em {time.time()-t1:.1f}s :: {_txt(text, 120)}")
    if text and CFG["dictation_polish"]:
        window.update(phase="polishing", final=text, partial="")
        t2 = time.time()
        text = jarvis_dictate.polish(text, LANG, CFG["dictation_polish_model"])
        print(f"[dict] revisão em {time.time()-t2:.1f}s :: {_txt(text, 120)}")
    if not text:
        window.update(phase="cancelled", final="", partial="")
        time.sleep(1.0)
        window.close()
        return
    window.update(final=text, partial="")
    result = jarvis_dictate.paste_text(text, CFG["dictation_output"], dry_run=args.test)
    print(f"[dict] saída: {result}")
    window.update(phase="pasted" if result in ("pasted", "typed", "dry-run") else "copied")
    chime(1320, 880, ms=60, vol=0.10)
    time.sleep(1.5)
    window.close()


# --- push-to-talk ----------------------------------------------------
# SIGUSR1 dispara a escuta como se a wake word tivesse sido detectada
# (bind de teclado: systemctl --user kill -s SIGUSR1 voice-launcher.service).
PTT = threading.Event()


def _on_sigusr1(signum, frame):
    PTT.set()


# --- main loop -------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="dry-run (nao abre layouts nem suspende)")
    ap.add_argument("--whisper-model", default=None, help="sobrescreve whisper_model do config")
    ap.add_argument("--stt", default=None, choices=["auto", "local", "openai"], help="sobrescreve stt_provider")
    ap.add_argument("--wake-threshold", type=float, default=WAKE_THRESHOLD)
    args = ap.parse_args()
    if args.whisper_model:
        CFG["whisper_model"] = args.whisper_model
    if args.stt:
        CFG["stt_provider"] = args.stt

    print(f">> config: {jarvis_config.CONFIG_FILE} "
          f"({'existe' if jarvis_config.CONFIG_FILE.exists() else 'ausente — defaults'})")
    print(f">> idioma: {LANG} | voz: {VOICE_NAME}")
    print(f">> carregando wake word ({WAKE_WORD})...")
    wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")

    print(">> carregando reconhecimento de fala...")
    stt = jarvis_stt.build_transcriber(CFG, terms=stt_terms(), language=jarvis_i18n.STT_LANG[LANG])
    print(f">> reconhecimento: {stt.label}")

    print(">> carregando VAD (silero / faster-whisper)...")
    vad_model = get_vad_model()

    projs = list_projects()
    print(f">> projetos ({len(projs)}): {', '.join(projs)}")
    print(">> script de layout:", LAYOUT_SCRIPT, "(existe)" if LAYOUT_SCRIPT.exists() else "(AUSENTE!)")
    print(">> viewer da janela:", VIEWER_SCRIPT, "(existe)" if VIEWER_SCRIPT.exists() else "(AUSENTE!)")
    print(">> voz:", VOICE, "(existe)" if VOICE.exists() else "(AUSENTE!)")
    print(f">> perguntas rápidas: {model_label(False)} | pense bem: {model_label(True)}")
    print(f">> modo: {'TEST (dry-run)' if args.test else 'REAL'}")
    signal.signal(signal.SIGUSR1, _on_sigusr1)
    signal.signal(signal.SIGUSR2, _on_sigusr2)
    jarvis_dictate.set_recording(False)
    jarvis_consent.revoke_grants()   # nenhum "permitir o resto" sobrevive a um restart
    jarvis_consent.cancel_all()
    sweep_model_tmp()
    if SYSTEM_ACCESS != "full":
        for prov in ("claude", "codex"):
            if shutil.which(prov) and not cli_supports_safe_mode(prov):
                print(f">> AVISO: {prov} instalado não tem as flags do modo seguro — acesso à máquina indisponível por ele")
    # sincroniza o modo de escuta com a config (o `jarvis wake` alterna o marcador em runtime)
    if CFG["wake_word_enabled"]:
        WAKE_OFF_FILE.unlink(missing_ok=True)
    else:
        WAKE_OFF_FILE.touch()
    spoken = WAKE_WORD.replace("_", " ")
    print(f">> pronto — diga '{spoken}' (ou o atalho push-to-talk) e fale depois da saudação\n")

    def open_stream() -> sd.InputStream:
        st = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK,
        )
        st.start()
        return st

    stream = open_stream()

    # chime inicial confirmando que tá no ar
    chime(660, 990, ms=80, vol=0.15)

    try:
        wake_was_off = False
        while True:
            # Modo manual (marcador jarvis-wake-off): sem escuta da wake word — o
            # mic fica FECHADO enquanto ocioso; só o push-to-talk (SIGUSR1) e o
            # ditado (SIGUSR2) abrem o stream, que volta a fechar em seguida.
            wake_off = WAKE_OFF_FILE.exists()
            if wake_off != wake_was_off:
                wake_was_off = wake_off
                print("[wake] escuta da wake word DESLIGADA — mic fechado; push-to-talk e ditado seguem ativos"
                      if wake_off else "[wake] escuta da wake word religada")
            score = 0.0
            try:
                if wake_off:
                    # fecha o stream de verdade: o node de captura some do
                    # PipeWire (nenhum indicador de mic em uso enquanto ocioso)
                    if stream is not None:
                        stream.stop()
                        stream.close()
                        stream = None
                    if not (PTT.is_set() or DICT.is_set()):
                        time.sleep(0.12)
                        continue
                    stream = open_stream()
                else:
                    if stream is None:
                        stream = open_stream()
                        wake.reset()
                    data, _ = stream.read(CHUNK)
                    chunk = data.flatten()
                    pred = wake.predict(chunk)
                    score = max(pred.values()) if pred else 0.0
            except Exception as e:
                print(f"[loop erro na leitura/wake: {e}] — sleep 1s e tenta de novo")
                time.sleep(1)
                continue

            if DICT.is_set():
                DICT.clear()
                cmd = jarvis_dictate.take_command()
                if cmd in ("start", "toggle"):
                    try:
                        run_dictation(stream, stt, args, duck=(cmd == "toggle"))
                    except Exception as e:
                        print(f"[dict erro: {type(e).__name__}: {e}]")
                        jarvis_dictate.set_recording(False)
                    try:
                        wake.reset()
                        flush_stream(stream)
                    except Exception:
                        pass
                continue

            ptt = PTT.is_set()
            if ptt:
                PTT.clear()
            if ptt or score > args.wake_threshold:
                try:
                    if ptt:
                        print("[wake] push-to-talk (SIGUSR1)")
                    else:
                        print(f"[wake] hey_jarvis detectado (score={score:.2f})")
                    run_conversation(stream, wake, stt, vad_model, args)

                except Exception as e:
                    print(f"[wake handler erro: {type(e).__name__}: {e}]")
                    try:
                        tts(T(LANG, "problem"))
                    except Exception:
                        pass

                # reset wake word state + cooldown (sempre roda, mesmo após erro)
                try:
                    wake.reset()
                    time.sleep(0.3)
                    flush_stream(stream)
                except Exception:
                    pass
                print("-> aguardando novo wake word...\n")

    except KeyboardInterrupt:
        print("\n>> bye")
    finally:
        claude_executor.shutdown(wait=False, cancel_futures=True)
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
