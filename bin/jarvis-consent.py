#!/usr/bin/python3
"""Janela de autorização do Jarvis (system_access = "ask").

Aberta por jarvis_consent.ask() num terminal flutuante. Mostra o que o modelo
quer executar — o comando exato, o diretório, a pergunta que originou — e
espera uma tecla:

    y           permite esta ação (só o `y`; Enter não permite)
    a           permite tudo até o fim desta pergunta
    n / Esc / q nega
    j / k / espaço   rolam quando o comando não cabe na janela; `y` e `a` só
                     ficam ativos depois de rolar até o fim

Nada do que o modelo escreveu chega ao terminal como controle: todo caractere
de controle vira escape visível (jarvis_consent.safe_text). Teclas são ignoradas
no primeiro instante após abrir (uma janela que rouba o foco não pode ser
aprovada por um Enter/y que já estava sendo digitado). Sem resposta até o
prazo, nega. Fecha sozinha se o pedido for cancelado.

Uso: jarvis-consent.py <id-do-pedido>
"""

from __future__ import annotations

import json
import select
import shutil
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_consent  # noqa: E402
import jarvis_i18n  # noqa: E402
from jarvis_i18n import T  # noqa: E402

BOLD, DIM, RESET = "\x1b[1m", "\x1b[90m", "\x1b[0m"
YELLOW, GREEN, RED, CYAN = "\x1b[93m", "\x1b[92m", "\x1b[91m", "\x1b[96m"
INPUT_GRACE = 0.7  # s após a primeira renderização em que teclas são ignoradas


def body_lines(req: dict, lang: str, width: int) -> list[str]:
    """Conteúdo rolável: pergunta, comando exato, diretório. Tudo que vem do
    modelo / da transcrição passa por safe_text()."""
    safe = jarvis_consent.safe_text
    wrap = lambda s: textwrap.wrap(s, width - 4) or [""]  # noqa: E731
    lines: list[str] = []
    if req.get("question"):
        lines.append(f"  {DIM}{T(lang, 'consent_question')}{RESET}")
        lines += [f"    {l}" for l in wrap(safe(req["question"]))]
        lines.append("")
    what = T(lang, "consent_command") if req.get("kind") == "command" else \
        T(lang, "consent_tool", tool=safe(req.get("tool", "?")))
    lines.append(f"  {DIM}{what}{RESET}")
    for raw in safe(req.get("summary", ""), keep_newlines=True).splitlines() or [""]:
        for l in textwrap.wrap(raw, width - 6, replace_whitespace=False,
                               drop_whitespace=False) or [""]:
            lines.append(f"    {CYAN}{l}{RESET}")
    lines.append("")
    lines.append(f"  {DIM}{T(lang, 'consent_cwd')}{RESET} {safe(req.get('cwd', ''))}")
    return lines


def render(req: dict, lang: str, left: int, offset: int) -> tuple[str, bool, int]:
    """Desenha a janela. Retorna (texto, chegou_ao_fim, offset_máximo)."""
    cols, rows = shutil.get_terminal_size((100, 30))
    width = min(cols, 110) - 2
    body = body_lines(req, lang, width)
    header = ["", f"  {YELLOW}{BOLD}{T(lang, 'consent_title')}{RESET}", ""]
    footer_h = 4
    avail = max(3, rows - len(header) - footer_h - 1)
    max_offset = max(0, len(body) - avail)
    offset = min(max(0, offset), max_offset)
    at_end = offset >= max_offset
    visible = body[offset: offset + avail]
    visible += [""] * (avail - len(visible))

    if at_end:
        keys = (f"  {GREEN}[y]{RESET} {T(lang, 'consent_key_allow')}     "
                f"{GREEN}[a]{RESET} {T(lang, 'consent_key_allow_all')}     "
                f"{RED}[n]{RESET} {T(lang, 'consent_key_deny')}")
    else:
        keys = (f"  {YELLOW}{T(lang, 'consent_scroll', more=len(body) - offset - avail)}{RESET}     "
                f"{RED}[n]{RESET} {T(lang, 'consent_key_deny')}")
    footer = ["", keys, "", f"  {DIM}{T(lang, 'consent_timeout', s=max(0, int(left)))}{RESET}"]
    lines = header + visible + footer
    return "\x1b[H\x1b[2J" + "\n".join(lines) + "\n", at_end, max_offset


def read_key(timeout: float) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    key = sys.stdin.read(1)
    if key == "\x1b":
        # setas chegam como ESC [ A/B — distingue de um Esc solto
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if ready:
            seq = sys.stdin.read(1)
            if seq == "[":
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                code = sys.stdin.read(1) if ready else ""
                return {"A": "k", "B": "j", "5": "K", "6": "J"}.get(code, "")
            return ""
    return key


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    req_id = argv[1]
    try:
        req = json.loads(jarvis_consent.request_path(req_id).read_text())
    except (OSError, json.JSONDecodeError):
        return 0  # pedido já resolvido/cancelado
    lang = jarvis_i18n.norm_lang(req.get("lang", "en"))
    deadline = float(req.get("deadline") or (time.time() + jarvis_consent.DEFAULT_TIMEOUT))

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    decision = None
    offset = 0
    at_end = False
    try:
        tty.setcbreak(fd)
        print("\x1b[?25l", end="")  # esconde o cursor
        termios.tcflush(fd, termios.TCIFLUSH)  # descarta teclas digitadas antes de abrir
        first_render = 0.0
        last_render = 0.0
        while decision is None:
            now = time.time()
            if not jarvis_consent.request_path(req_id).exists():
                return 0  # cancelado ou já decidido em outro lugar
            if now >= deadline:
                decision = "deny"
                break
            if now - last_render >= 1.0:
                text, at_end, _ = render(req, lang, deadline - now, offset)
                sys.stdout.write(text)
                sys.stdout.flush()
                last_render = now
                first_render = first_render or now
            key = read_key(0.3)
            if key is None or not first_render or time.time() - first_render < INPUT_GRACE:
                continue
            if key in ("j", "J", " "):
                offset += 1 if key == "j" else 10
                last_render = 0.0
            elif key in ("k", "K"):
                offset = max(0, offset - (1 if key == "k" else 10))
                last_render = 0.0
            elif key in ("y", "Y") and at_end:
                decision = "allow"
            elif key in ("a", "A") and at_end:
                decision = "allow_all"
            elif key in ("n", "N", "q", "Q", "\x1b"):
                decision = "deny"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\x1b[?25h", end="")

    jarvis_consent.write_decision(req_id, decision)
    color = GREEN if decision.startswith("allow") else RED
    print(f"\n  {color}{BOLD}{T(lang, 'consent_' + decision)}{RESET}")
    time.sleep(0.6)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
