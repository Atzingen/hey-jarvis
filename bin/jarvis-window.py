#!/usr/bin/python3
"""Viewer da janela persistente da conversa do Jarvis.

Renderiza $XDG_RUNTIME_DIR/jarvis-state.json (escrito pelo voice-launcher) num terminal
flutuante: fase atual (com countdown), últimas trocas e dicas no rodapé.
Tecla q/Esc cria $XDG_RUNTIME_DIR/jarvis-quit (encerra a conversa) e fecha. Sai sozinho
quando o estado vira "closed".
"""
import json
import os
import select
import shutil
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path

_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE_FILE = _RUNTIME_DIR / "jarvis-state.json"
QUIT_FLAG = _RUNTIME_DIR / "jarvis-quit"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_consent import safe_text  # noqa: E402
from jarvis_i18n import T  # noqa: E402

# fase -> cor ANSI; badge e dica vêm do i18n (chave ph_<fase>)
PHASE_COLOR = {"listening": "96", "recording": "92", "transcribing": "93", "thinking": "95",
               "speaking": "94", "followup": "96", "handoff": "93",
               "dictating": "92", "polishing": "93", "pasted": "92", "copied": "92", "cancelled": "90"}
METER = " ▁▂▃▄▅▆▇█"
WAVE_HALF = 3  # linhas acima e abaixo do eixo do waveform do ditado (8 degraus por linha)


def wave_lines(levels: list[float], width: int, color: str) -> list[str]:
    """Waveform espelhado do ditado: uma coluna por amostra (a mais nova à direita),
    WAVE_HALF linhas pra cima e pra baixo do eixo. A metade de baixo usa os mesmos
    blocos em vídeo reverso, que preenche a célula de cima pra baixo."""
    recent = [max(0.0, min(1.0, float(l))) for l in levels[-width:]]
    # Ganho automático: a fala mais alta da janela preenche a altura toda; o piso
    # do pico limita a amplificação, pra silêncio/ruído não virar onda.
    gain = 1.0 / max(max(recent, default=0.0), 0.35)
    cols = [0.0] * max(0, width - len(recent)) + [min(1.0, l * gain) if l >= 0.04 else 0.0 for l in recent]
    steps = [int(round(l * WAVE_HALF * 8)) for l in cols]

    def fill(step: int, d: int) -> int:
        return max(0, min(8, step - (d - 1) * 8))

    top = [f"  \x1b[{color}m" + "".join(METER[fill(s, d)] for s in steps) + "\x1b[0m"
           for d in range(WAVE_HALF, 0, -1)]
    bottom = [f"  \x1b[7;{color}m" + "".join(METER[8 - fill(s, d)] for s in steps) + "\x1b[0m"
              for d in range(1, WAVE_HALF + 1)]
    return top + bottom


def load_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def _wrap(text: str, width: int) -> list[str]:
    """Quebra texto vindo do modelo / da transcrição: antes passa por safe_text,
    então nenhum caractere de controle chega ao terminal."""
    out: list[str] = []
    for para in safe_text(text or "", keep_newlines=True).splitlines() or [""]:
        out += textwrap.wrap(para, width) or [""]
    return out


def render(state: dict) -> str:
    cols, rows = shutil.get_terminal_size((100, 32))
    width = min(cols - 2, 96)
    phase = state.get("phase", "listening")
    lang = state.get("lang", "pt-BR")
    color = PHASE_COLOR.get(phase, "37")
    ph = T(lang, f"ph_{phase}")
    name, hint = ph if isinstance(ph, tuple) else (phase.upper(), "")
    you = T(lang, "you")

    badge = name
    deadline = state.get("deadline")
    if deadline:
        badge = f"{name}  {int(max(0, deadline - time.time()))}s"

    detail = safe_text(state.get("detail") or "")
    title = " Jarvis" + (f"  \x1b[90m{detail}\x1b[0m" if detail else "")
    title_len = 7 + (len(detail) + 2 if detail else 0)
    pad = max(1, width - title_len - len(badge) - 4)
    header = [
        "",
        f"\x1b[1m{title}\x1b[0m" + " " * pad + f"\x1b[1;{color}m[ {badge} ]\x1b[0m",
        "\x1b[90m" + "─" * width + "\x1b[0m",
    ]

    # --- ditado: transcrição grande + medidor de áudio -------------------------
    if state.get("mode") == "dictation":
        text = state.get("partial") or state.get("final") or ""
        body = [""]
        if text:
            body += ["  " + line for line in _wrap(text + (" …" if phase == "dictating" else ""), width - 4)]
        else:
            body += ["  \x1b[90m" + (T(lang, "dict_empty") if phase != "dictating" else "…") + "\x1b[0m"]
        levels = state.get("levels") or []
        footer = ["\x1b[90m" + "─" * width + "\x1b[0m"] \
            + wave_lines(levels, width - 4, "92" if phase == "dictating" else "90") \
            + ["\x1b[90m" + "─" * width + "\x1b[0m"]
        footer += ["  \x1b[90m" + line + "\x1b[0m" for line in textwrap.wrap(hint, width - 2)]
        avail = max(3, rows - len(header) - len(footer) - 1)
        body = body[-avail:] + [""] * max(0, avail - len(body))
        lines = header + body + footer
        return "\x1b[H" + "\n".join(l + "\x1b[K" for l in lines) + "\x1b[0J"

    # --- conversa: um bloco por fala, rótulo do falante em destaque ------------
    blocks: list[list[str]] = []
    for ex in state.get("exchanges", []):
        b = [f"\x1b[1;96m▌ {you}\x1b[0m"]
        b += ["  \x1b[96m" + line + "\x1b[0m" for line in _wrap(ex.get("q", ""), width - 4)]
        b.append("")
        label = safe_text(ex.get("label", ""))
        b.append(f"\x1b[1;93m▌ Jarvis\x1b[0m" + (f"  \x1b[90m{label}\x1b[0m" if label else ""))
        b += ["  " + line for line in _wrap(ex.get("a", ""), width - 4)]
        b.append("")
        blocks.append(b)
    partial = state.get("partial") or ""
    if phase in ("recording", "transcribing") and partial:
        b = [f"\x1b[1;96m▌ {you}\x1b[0m"]
        b += ["  \x1b[96m" + line + "\x1b[0m" for line in _wrap(partial + " …", width - 4)]
        b.append("")
        blocks.append(b)

    # --- área de atividade (fixa, embaixo): pensamentos / eventos do modelo ----
    thoughts = state.get("thoughts") or []
    activity_h = 3
    activity: list[str] = []
    if phase in ("thinking", "handoff") and thoughts:
        for line in thoughts[-activity_h:]:
            activity.append("  \x1b[90m⋯ " + safe_text(line)[: width - 6] + "\x1b[0m")
    activity = activity[-activity_h:]
    while len(activity) < activity_h:
        activity.insert(0, "")

    footer = ["\x1b[90m" + "─" * width + "\x1b[0m"] + activity \
        + ["\x1b[90m" + "─" * width + "\x1b[0m"]
    footer += ["  \x1b[90m" + line + "\x1b[0m" for line in textwrap.wrap(hint, width - 2)]

    # conversa ocupa o resto; mostra os blocos mais recentes que couberem
    avail = max(3, rows - len(header) - len(footer) - 1)
    body: list[str] = []
    for b in reversed(blocks):
        if len(body) + len(b) > avail:
            if not body:
                body = b[-avail:]
            break
        body = b + body
    if not blocks:
        body = ["", "  \x1b[90m" + T(lang, "empty") + "\x1b[0m"]
    body = body + [""] * (avail - len(body))

    # \x1b[K limpa o resto de cada linha — evita restos de frames anteriores
    lines = header + body + footer
    return "\x1b[H" + "\n".join(l + "\x1b[K" for l in lines) + "\x1b[0J"


def main() -> None:
    interactive = sys.stdin.isatty()
    old = None
    if interactive:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    sys.stdout.write("\x1b[?25l\x1b[2J")
    try:
        while True:
            state = load_state()
            if state is None or state.get("phase") == "closed":
                break
            sys.stdout.write(render(state))
            sys.stdout.flush()
            if interactive:
                r, _, _ = select.select([sys.stdin], [], [], 0.25)
                if r:
                    ch = sys.stdin.read(1)
                    if ch in ("q", "Q", "\x1b"):
                        QUIT_FLAG.touch()
                        break
            else:
                time.sleep(0.25)
    finally:
        if old is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h\x1b[0m\x1b[2J\x1b[H")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
