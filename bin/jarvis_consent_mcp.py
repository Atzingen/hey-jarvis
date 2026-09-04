#!/usr/bin/python3
"""Servidor MCP (stdio) com a ÚNICA tool que o modelo tem no modo `ask`: `run`.

O launcher roda o Claude Code com `--restricted --tools "" --strict-mcp-config
--mcp-config <este servidor>` e o Codex com a shell desligada + este servidor:
nenhum dos dois tem tool própria de execução, leitura ou escrita. Tudo que o
modelo quiser fazer na máquina vira `run(command)`:

  1. o comando exato aparece na janela jarvis-consent.py;
  2. só depois de `y` (ou de um "permitir o resto desta pergunta" ainda válido)
     ele roda — `bash -c`, no workdir do plugin, ambiente mínimo, saída limitada;
  3. a saída (ou "negado") volta ao modelo como texto.

Só stdlib: JSON-RPC 2.0, uma mensagem por linha, como o transporte stdio do MCP.
Contexto pela env JARVIS_CTX = caminho de um JSON com {lang, question, call_id,
timeout} escrito pelo launcher (evita escapar a pergunta na linha de comando).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_consent  # noqa: E402


def _load_ctx() -> dict:
    path = os.environ.get("JARVIS_CTX", "")
    try:
        return json.loads(Path(path).read_text()) if path else {}
    except (OSError, json.JSONDecodeError):
        return {}


CTX = _load_ctx()
LANG = CTX.get("lang", "en")
QUESTION = str(CTX.get("question", ""))
CALL_ID = str(CTX.get("call_id", ""))
TIMEOUT = float(CTX.get("timeout", jarvis_consent.DEFAULT_TIMEOUT))

TOOL = {
    "name": "run",
    "description": (
        "Run one shell command on the user's machine. The exact command is shown to the "
        "user in an authorization window and only runs after they approve it (this can "
        "take some seconds). Returns the exit code and the combined output, or a denial. "
        "If denied, do not retry it or try another way: tell the user it was not authorized."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Exact bash command line."}},
        "required": ["command"],
        "additionalProperties": False,
    },
}


def log(msg: str) -> None:
    print(f"[consent-mcp] {msg}", file=sys.stderr, flush=True)


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def reply_error(req_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def run_tool(args: dict) -> str:
    """Consentimento → execução → texto pro modelo."""
    cmd = args.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return "error: 'command' must be a non-empty string"
    if jarvis_consent.has_grant(CALL_ID):
        decision = "allow"
        log(f"grant → {cmd[:120]!r}")
    else:
        req = jarvis_consent.new_request("command", "bash", {"command": cmd}, cmd,
                                         question=QUESTION, lang=LANG)
        log(f"asking user: {cmd[:120]!r}")
        decision = jarvis_consent.ask(req, timeout=TIMEOUT)
        log(f"decision: {decision}")
        if decision == "allow_all" and CALL_ID:
            jarvis_consent.grant_allow_all(CALL_ID)
    if not decision.startswith("allow"):
        return (f"$ {cmd}\n→ DENIED: the user did not authorize this command on screen. "
                "Do not retry it; answer without it and say the action was not authorized.")
    rc, out = jarvis_consent.execute_brokered(cmd)
    log(f"ran (exit {rc}): {cmd[:120]!r}")
    return f"$ {cmd}\n→ exit {rc}\n{out}".rstrip() + "\n"


def handle(msg: dict) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        reply(req_id, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jarvis", "version": "2.0"},
        })
    elif method.startswith("notifications/"):
        return
    elif method == "ping":
        reply(req_id, {})
    elif method == "tools/list":
        reply(req_id, {"tools": [TOOL]})
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            reply_error(req_id, -32602, f"unknown tool {params.get('name')!r}")
            return
        text = run_tool(params.get("arguments") or {})
        reply(req_id, {"content": [{"type": "text", "text": text}]})
    elif req_id is not None:
        reply_error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception as e:  # nunca derruba o servidor no meio de uma pergunta
            log(f"error: {e}")
            if msg.get("id") is not None:
                reply_error(msg["id"], -32603, str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
