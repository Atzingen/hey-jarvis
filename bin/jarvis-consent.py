#!/usr/bin/python3
"""Janela de autorização do Jarvis (system_access = "ask").

Aberta por jarvis_consent.ask() num terminal flutuante. Mostra o que o modelo
quer executar — o comando exato, o diretório, a pergunta que originou — e
espera uma tecla:

    y / Enter   permite esta ação
    a           permite tudo até o fim desta pergunta
    n / Esc / q nega

Sem resposta até o prazo, nega. Fecha sozinha se o pedido for cancelado
(o launcher nega pedidos abertos quando a pergunta é interrompida).

Uso: jarvis-consent.py <id-do-pedido>
"""

from __future__ import annotations

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_consent  # noqa: E402
import jarvis_i18n  # noqa: E402
from jarvis_i18n import T  # noqa: E402

BOLD, DIM, RESET = "\x1b[1m", "\x1b[90m", "\x1b[0m"
YELLOW, GREEN, RED, CYAN = "\x1b[93m", "\x1b[92m", "\x1b[91m", "\x1b[96m"


def render(req: dict, lang: str, left: int) -> str:
    width = min(shutil.get_terminal_size((100, 28)).columns, 110) - 2
    wrap = lambda s: textwrap.wrap(s, width - 4) or [""]  # noqa: E731

    lines = ["", f"  {YELLOW}{BOLD}{T(lang, 'consent_title')}{RESET}", ""]
    if req.get("question"):
        lines.append(f"  {DIM}{T(lang, 'consent_question')}{RESET}")
        lines += [f"    {l}" for l in wrap(req["question"])]
        lines.append("")

    what = T(lang, "consent_command") if req.get("kind") == "command" else \
        T(lang, "consent_tool", tool=req.get("tool", "?"))
    lines.append(f"  {DIM}{what}{RESET}")
    for raw in str(req.get("summary", "")).splitlines() or [""]:
        for l in textwrap.wrap(raw, width - 6, replace_whitespace=False) or [""]:
            lines.append(f"    {CYAN}{l}{RESET}")
    lines.append("")
    lines.append(f"  {DIM}{T(lang, 'consent_cwd')}{RESET} {req.get('cwd', '')}")
    lines.append("")
    lines.append(f"  {GREEN}[y]{RESET} {T(lang, 'consent_key_allow')}     "
                 f"{GREEN}[a]{RESET} {T(lang, 'consent_key_allow_all')}     "
                 f"{RED}[n]{RESET} {T(lang, 'consent_key_deny')}")
    lines.append("")
    lines.append(f"  {DIM}{T(lang, 'consent_timeout', s=max(0, int(left)))}{RESET}")
    return "\x1b[H\x1b[2J" + "\n".join(lines) + "\n"


def read_key(timeout: float) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    return sys.stdin.read(1)


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
    try:
        tty.setcbreak(fd)
        print("\x1b[?25l", end="")  # esconde o cursor
        last_render = 0.0
        while decision is None:
            now = time.time()
            if not jarvis_consent.request_path(req_id).exists():
                return 0  # cancelado ou já decidido em outro lugar
            if now >= deadline:
                decision = "deny"
                break
            if now - last_render >= 1.0:
                sys.stdout.write(render(req, lang, deadline - now))
                sys.stdout.flush()
                last_render = now
            key = read_key(0.3)
            if key is None:
                continue
            if key in ("y", "Y", "\r", "\n"):
                decision = "allow"
            elif key in ("a", "A"):
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
