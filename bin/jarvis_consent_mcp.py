#!/usr/bin/python3
"""Servidor MCP (stdio) que faz o Claude Code pedir permissão ao usuário.

O launcher roda `claude -p --permission-mode default --permission-prompt-tool
mcp__jarvis__approve --mcp-config <este servidor>`: toda tool que o Claude
Code não auto-aprova (Bash que não é read-only, escrita de arquivo, rede…)
vira uma chamada a `approve` aqui, com o nome da tool e o input exato. A
janela jarvis-consent.py mostra isso ao usuário; a resposta volta como
{"behavior": "allow", "updatedInput": ...} ou {"behavior": "deny", ...}.

"allow_all" na janela libera o resto DESTA chamada do modelo (este processo
vive só enquanto o `claude -p` da pergunta vive) e nunca é gravado.

Só stdlib: JSON-RPC 2.0, uma mensagem por linha, como o transporte stdio do MCP.
Contexto pela env: JARVIS_LANG, JARVIS_QUESTION, JARVIS_CONSENT_TIMEOUT, JARVIS_CWD.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_consent  # noqa: E402

LANG = os.environ.get("JARVIS_LANG", "en")
QUESTION = os.environ.get("JARVIS_QUESTION", "")
CWD = os.environ.get("JARVIS_CWD", str(Path.home()))
TIMEOUT = float(os.environ.get("JARVIS_CONSENT_TIMEOUT", jarvis_consent.DEFAULT_TIMEOUT))

TOOL = {
    "name": "approve",
    "description": "Asks the Jarvis user, on screen, whether a tool call may run. "
                   "Used only as Claude Code's permission prompt tool.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
}

allow_all = False


def log(msg: str) -> None:
    print(f"[consent-mcp] {msg}", file=sys.stderr, flush=True)


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def reply_error(req_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def decide(args: dict) -> dict:
    """Pergunta ao usuário e devolve o payload que o Claude Code espera."""
    global allow_all
    tool = str(args.get("tool_name") or args.get("toolName") or "tool")
    tool_input = args.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if allow_all:
        log(f"allow_all → {tool}")
        return {"behavior": "allow", "updatedInput": tool_input}

    summary = jarvis_consent.describe_tool(tool, tool_input)
    req = jarvis_consent.new_request("claude-tool", tool, tool_input, summary,
                                     question=QUESTION, lang=LANG, cwd=CWD)
    log(f"asking user: {tool} :: {summary[:120]}")
    decision = jarvis_consent.ask(req, timeout=TIMEOUT)
    log(f"decision: {decision}")
    if decision == "allow_all":
        allow_all = True
    if decision.startswith("allow"):
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny",
            "message": "The user did not authorize this action on screen. Do not retry it; "
                       "answer without it and say the action was not authorized."}


def handle(msg: dict) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        reply(req_id, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jarvis", "version": "1.0"},
        })
    elif method == "notifications/initialized" or method.startswith("notifications/"):
        return
    elif method == "ping":
        reply(req_id, {})
    elif method == "tools/list":
        reply(req_id, {"tools": [TOOL]})
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            reply_error(req_id, -32602, f"unknown tool {params.get('name')!r}")
            return
        result = decide(params.get("arguments") or {})
        reply(req_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
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
