#!/usr/bin/python3
"""Consentimento do usuário para ações do modelo na máquina (system_access = "ask").

Um pedido = um arquivo JSON em $XDG_RUNTIME_DIR/jarvis-consent/<id>.json; a
decisão volta em <id>.decision. Quem pede (o servidor MCP do Claude ou o
launcher, no broker <<RODAR>> do Codex) abre a janela `jarvis-consent.py`, que
mostra o comando EXATO e grava a decisão; sem resposta em `timeout` segundos a
ação é negada.

    request = new_request(kind, tool, tool_input, summary, question, lang)
    decision = ask(request, timeout=90)     # "allow" | "allow_all" | "deny"
    pending()                               # há pedido aberto? (cue de voz)
    cancel_all()                            # fecha janelas abertas (cancelamento)

`allow_all` = permitir tudo até o fim desta pergunta — vale só para o
processo que perguntou (o servidor MCP daquela chamada), nunca é persistido.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CONSENT_DIR = RUNTIME_DIR / "jarvis-consent"
UI_SCRIPT = Path(__file__).resolve().parent / "jarvis-consent.py"

DEFAULT_TIMEOUT = 90  # s — abaixo de CLAUDE_CODE_APPROVAL_TIMEOUT_MS (setado pelo launcher)
DECISIONS = ("allow", "allow_all", "deny")


def _ensure_dir() -> None:
    CONSENT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def request_path(req_id: str) -> Path:
    return CONSENT_DIR / f"{req_id}.json"


def decision_path(req_id: str) -> Path:
    return CONSENT_DIR / f"{req_id}.decision"


def describe_tool(tool: str, tool_input: dict) -> str:
    """Texto exato do que vai ser executado, por tool do Claude Code."""
    if not isinstance(tool_input, dict):
        return f"{tool} {tool_input!r}"
    if tool == "Bash" and tool_input.get("command"):
        return str(tool_input["command"])
    for key in ("command", "file_path", "path", "pattern", "url", "query"):
        if tool_input.get(key):
            extra = ""
            if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                extra = "  (escrita / write)"
            return f"{tool}: {tool_input[key]}{extra}"
    return f"{tool}: {json.dumps(tool_input, ensure_ascii=False)[:600]}"


def new_request(kind: str, tool: str, tool_input: dict, summary: str,
                question: str = "", lang: str = "en", cwd: str = "") -> dict:
    """kind: "claude-tool" (permissão pedida pelo Claude Code) | "command" (broker do Codex)."""
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "tool": tool,
        "input": tool_input,
        "summary": summary,
        "question": question,
        "lang": lang,
        "cwd": cwd or str(Path.home()),
        "created": time.time(),
    }


def _launch_ui(req_id: str) -> None:
    """Abre a janela flutuante com o pedido. uwsm-app / systemd-run mantêm a
    janela num scope próprio (não morre com o processo que perguntou)."""
    ui = ["alacritty", "--class", "TUI.float", "--title", "Jarvis — autorização",
          "-o", "window.dimensions.columns=100", "-o", "window.dimensions.lines=28",
          "-e", "python3", str(UI_SCRIPT), req_id]
    if shutil.which("uwsm-app"):
        full = ["uwsm-app", "--"] + ui
    elif shutil.which("systemd-run"):
        full = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"] + ui
    else:
        full = ui
    subprocess.Popen(full, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_decision(req_id: str) -> str | None:
    try:
        value = decision_path(req_id).read_text().strip()
    except OSError:
        return None
    return value if value in DECISIONS else "deny"


def write_decision(req_id: str, decision: str) -> None:
    if decision not in DECISIONS:
        decision = "deny"
    _ensure_dir()
    tmp = decision_path(req_id).with_suffix(".tmp")
    tmp.write_text(decision)
    tmp.replace(decision_path(req_id))


def ask(request: dict, timeout: float = DEFAULT_TIMEOUT, should_cancel=None) -> str:
    """Mostra o pedido e espera a decisão. Sem resposta em `timeout` s → "deny";
    `should_cancel()` verdadeiro (pergunta interrompida) também nega. O arquivo do
    pedido some ao final; a janela fecha sozinha quando ele some."""
    _ensure_dir()
    req_id = request["id"]
    request = dict(request, deadline=time.time() + timeout)
    request_path(req_id).write_text(json.dumps(request, ensure_ascii=False))
    decision_path(req_id).unlink(missing_ok=True)
    try:
        _launch_ui(req_id)
    except OSError:
        pass  # sem terminal: fica só o timeout → deny
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            decision = read_decision(req_id)
            if decision is not None:
                return decision
            if should_cancel is not None and should_cancel():
                return "deny"
            time.sleep(0.2)
        return "deny"
    finally:
        request_path(req_id).unlink(missing_ok=True)
        decision_path(req_id).unlink(missing_ok=True)


def pending() -> dict | None:
    """Primeiro pedido ainda sem decisão (pro launcher avisar por voz), ou None."""
    if not CONSENT_DIR.is_dir():
        return None
    for path in sorted(CONSENT_DIR.glob("*.json")):
        if decision_path(path.stem).exists():
            continue
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def cancel_all() -> None:
    """Nega todo pedido aberto (a pergunta foi cancelada) — as janelas fecham."""
    if not CONSENT_DIR.is_dir():
        return
    for path in CONSENT_DIR.glob("*.json"):
        if not decision_path(path.stem).exists():
            write_decision(path.stem, "deny")
