#!/usr/bin/python3
"""Eventos em streaming dos CLIs (codex --json / claude --output-format stream-json).

    parse_line(provider, line) -> (kind, text) | None
        kind: "thinking" | "tool" | "text" | "result"
    final_answer(provider, path, fallback_text) -> str
    follow(path, provider, pid, answer_file)   # CLI: acompanha ao vivo no terminal rascunho

Usado pelo voice-launcher pra mostrar na janela o que o modelo está fazendo
("executando: docker ps", "pensando: ...") enquanto a resposta não vem, e pelo
terminal rascunho quando um trabalho longo é entregue a ele.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _short(text: str, n: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tool_summary(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return name
    if inp.get("description"):
        return f"{inp['description']}"
    for key in ("command", "cmd", "pattern", "file_path", "path", "query", "url"):
        if inp.get(key):
            return f"{name}: {inp[key]}"
    return name


def parse_line(provider: str, line: str):
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None

    if provider == "codex":
        t = ev.get("type", "")
        item = ev.get("item") or {}
        it = item.get("type")
        if t == "item.started" and it == "command_execution":
            cmd = item.get("command", "")
            cmd = cmd.replace("/bin/bash -lc ", "").strip('"').strip("'")
            return ("tool", _short(cmd))
        if t == "item.completed" and it == "reasoning":
            txt = item.get("text") or item.get("summary") or ""
            txt = txt.replace("**", "")
            return ("thinking", _short(txt)) if txt else None
        if t == "item.completed" and it == "agent_message":
            return ("text", item.get("text", ""))
        if t == "turn.completed":
            return ("result", "")
        return None

    # claude
    t = ev.get("type", "")
    if t == "assistant":
        blocks = (ev.get("message") or {}).get("content") or []
        out = None
        for b in blocks:
            bt = b.get("type")
            if bt == "tool_use":
                out = ("tool", _short(_tool_summary(b.get("name", "tool"), b.get("input") or {})))
            elif bt == "thinking" and b.get("thinking"):
                out = ("thinking", _short(b["thinking"]))
            elif bt == "text" and b.get("text"):
                out = ("text", b["text"])
        return out
    if t == "result":
        return ("result", str(ev.get("result") or ""))
    return None


def status_label(kind: str, text: str, lang: str = "pt-BR") -> str | None:
    """Linha curta pra janela; None pra eventos que não merecem exibição."""
    from jarvis_i18n import T
    if kind == "tool":
        return f"{T(lang, 'ev_tool')}: {text}"
    if kind == "thinking":
        return f"{T(lang, 'ev_thinking')}: {text}"
    if kind == "text" and text.strip():
        return f"{T(lang, 'ev_text')}: {_short(text, 90)}"
    return None


def final_answer(provider: str, path: Path, fallback_text: str = "") -> str:
    """Resposta final a partir do arquivo de eventos; se não houver JSON de
    resultado, devolve o texto cru (modo compatível / provider sem --json)."""
    try:
        raw = Path(path).read_text()
    except OSError:
        return fallback_text
    result = None
    last_text = None
    any_json = False
    for line in raw.splitlines():
        parsed = parse_line(provider, line)
        if parsed is None:
            continue
        any_json = True
        kind, text = parsed
        if kind == "result" and text:
            result = text
        elif kind == "text" and text:
            last_text = text
    if result:
        return result.strip()
    if last_text:
        return last_text.strip()
    if any_json:
        return fallback_text
    return raw.strip() or fallback_text


def follow(path: str, provider: str, pid: int, answer_file: str | None = None,
           lang: str = "pt-BR") -> None:
    """Acompanha o arquivo de eventos até o processo `pid` terminar."""
    from jarvis_i18n import T
    p = Path(path)
    pos = 0
    print(f"  {T(lang, 'handoff_running')}\n")
    while True:
        try:
            with open(p) as f:
                f.seek(pos)
                for line in f:
                    pos += len(line.encode())
                    parsed = parse_line(provider, line)
                    if parsed:
                        label = status_label(*parsed, lang=lang)
                        if label:
                            print("  ·", label, flush=True)
        except FileNotFoundError:
            pass
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.4)
    final = ""
    if answer_file and Path(answer_file).exists():
        final = Path(answer_file).read_text().strip()
    if not final:
        final = final_answer(provider, p)
    print(f"\n  {T(lang, 'handoff_answer')}\n")
    for para in final.splitlines():
        print("    " + para)
    print()


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "follow":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        follow(sys.argv[2], sys.argv[3], int(sys.argv[4]),
               sys.argv[5] if len(sys.argv) > 5 else None,
               sys.argv[6] if len(sys.argv) > 6 else "pt-BR")
    else:
        print(__doc__)
        sys.exit(2)
