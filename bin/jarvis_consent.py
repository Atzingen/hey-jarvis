#!/usr/bin/python3
"""Consentimento do usuário para ações do modelo na máquina (system_access = "ask").

No modo `ask` o modelo (Claude Code ou Codex) não tem NENHUMA tool própria de
execução: a única forma de tocar na máquina é a tool `run` do servidor MCP
`jarvis_consent_mcp.py`, que passa por aqui. Um pedido = um arquivo JSON em
$XDG_RUNTIME_DIR/jarvis-consent/<id>.json; a decisão volta em <id>.decision. A
janela `jarvis-consent.py` mostra o comando EXATO e grava a decisão; sem
resposta em `timeout` segundos a ação é negada.

    request = new_request("command", "bash", {"command": cmd}, cmd, question, lang)
    decision = ask(request, timeout=90)     # "allow" | "allow_all" | "deny"
    rc, output = execute_brokered(cmd)      # só depois de "allow*"
    pending()                               # há pedido aberto? (cue de voz)
    cancel_all()                            # nega pedidos abertos (cancelamento)

`allow_all` = permitir o resto DESTA chamada do modelo. É um arquivo de grant
ligado ao id da chamada (o scope systemd do CLI), com validade curta; o
launcher o revoga ao cancelar, ao entregar a resposta, no handoff e ao subir.
Nunca é gravado em nenhum settings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CONSENT_DIR = RUNTIME_DIR / "jarvis-consent"
UI_SCRIPT = Path(__file__).resolve().parent / "jarvis-consent.py"
# cwd dos comandos autorizados: pasta vazia própria do plugin (o comando pode
# citar qualquer caminho, mas ele aparece por extenso na janela).
WORKDIR = Path.home() / ".local/share/jarvis/workdir"

DEFAULT_TIMEOUT = 90       # s até negar sozinho
COMMAND_TIMEOUT = 60       # s por comando autorizado
OUTPUT_LIMIT = 64 * 1024   # bytes de saída devolvidos ao modelo (o resto é cortado)
GRANT_TTL = 15 * 60        # s de validade de um "permitir o resto desta pergunta"
DECISIONS = ("allow", "allow_all", "deny")

# Variáveis que um comando autorizado enxerga. Nada além disso é herdado do
# serviço (chaves de API, tokens, etc. não chegam ao comando).
ENV_ALLOWLIST = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "LC_ALL", "LC_CTYPE",
                 "TERM", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                 "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_DATA_DIRS",
                 "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                 "HYPRLAND_INSTANCE_SIGNATURE", "TZ")


def _ensure_dir() -> None:
    CONSENT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CONSENT_DIR.is_symlink() or CONSENT_DIR.stat().st_uid != os.getuid():
        raise OSError(f"{CONSENT_DIR} is not owned by this user")
    os.chmod(CONSENT_DIR, 0o700)


def _write_private(path: Path, text: str) -> None:
    """Escreve `text` em `path` com modo 0600, sem seguir symlink, de forma atômica."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


def request_path(req_id: str) -> Path:
    return CONSENT_DIR / f"{req_id}.json"


def decision_path(req_id: str) -> Path:
    return CONSENT_DIR / f"{req_id}.decision"


def safe_text(text: object, keep_newlines: bool = False) -> str:
    """Representação segura para mostrar texto controlado pelo modelo num terminal.

    Todo caractere de controle (C0, DEL, C1) e todo caractere de formato Unicode
    (bidi overrides, zero-width, BOM…) vira o seu escape visível (`\\x1b`,
    `\\u202e`), então o payload não consegue emitir sequências ANSI que limpem ou
    redesenhem a janela de autorização, nem esconder texto. Tab vira espaços;
    `\\n` só é preservado quando `keep_newlines=True` (o resumo do comando).
    """
    out: list[str] = []
    for ch in str(text):
        code = ord(ch)
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch == "\t":
            out.append("    ")
        elif code < 0x20 or 0x7F <= code <= 0x9F or unicodedata.category(ch) == "Cf":
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def new_request(kind: str, tool: str, tool_input: dict, summary: str,
                question: str = "", lang: str = "en", cwd: str = "") -> dict:
    """kind: "command" (a tool `run` do broker) — o que aparece na janela é `summary`."""
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "tool": tool,
        "input": tool_input,
        "summary": summary,
        "question": question,
        "lang": lang,
        "cwd": cwd or str(WORKDIR),
        "created": time.time(),
    }


def _launch_ui(req_id: str) -> bool:
    """Abre a janela flutuante com o pedido. uwsm-app / systemd-run mantêm a
    janela num scope próprio (não morre com o processo que perguntou).
    Retorna False quando não há terminal com que abrir a janela."""
    if shutil.which("alacritty"):
        ui = ["alacritty", "--class", "TUI.float", "--title", "Jarvis — autorização",
              "-o", "window.dimensions.columns=100", "-o", "window.dimensions.lines=30",
              "-e", "python3", str(UI_SCRIPT), req_id]
    elif shutil.which("xdg-terminal-exec"):
        ui = ["xdg-terminal-exec", "-e", "python3", str(UI_SCRIPT), req_id]
    else:
        return False
    if shutil.which("uwsm-app"):
        full = ["uwsm-app", "--"] + ui
    elif shutil.which("systemd-run"):
        full = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"] + ui
    else:
        full = ui
    try:
        subprocess.Popen(full, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


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
    _write_private(decision_path(req_id), decision)


def ask(request: dict, timeout: float = DEFAULT_TIMEOUT, should_cancel=None) -> str:
    """Mostra o pedido e espera a decisão. Sem resposta em `timeout` s → "deny";
    `should_cancel()` verdadeiro (pergunta interrompida) também nega; sem terminal
    para abrir a janela → "deny" na hora. O arquivo do pedido some ao final; a
    janela fecha sozinha quando ele some."""
    _ensure_dir()
    req_id = request["id"]
    request = dict(request, deadline=time.time() + timeout)
    _write_private(request_path(req_id), json.dumps(request, ensure_ascii=False))
    decision_path(req_id).unlink(missing_ok=True)
    try:
        if not _launch_ui(req_id):
            return "deny"
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


# --- "permitir o resto desta pergunta" ---------------------------------------

def _grant_path(call_id: str) -> Path:
    safe = "".join(c for c in call_id if c.isalnum() or c in "-_.")
    return CONSENT_DIR / f"grant-{safe}"


def grant_allow_all(call_id: str) -> None:
    _ensure_dir()
    _write_private(_grant_path(call_id), str(time.time() + GRANT_TTL))


def has_grant(call_id: str) -> bool:
    if not call_id:
        return False
    try:
        return time.time() < float(_grant_path(call_id).read_text().strip())
    except (OSError, ValueError):
        return False


def revoke_grants(call_id: str | None = None) -> None:
    """Sem `call_id`, revoga todos (cancelamento, handoff, fim da pergunta, subida do serviço)."""
    if not CONSENT_DIR.is_dir():
        return
    paths = [_grant_path(call_id)] if call_id else list(CONSENT_DIR.glob("grant-*"))
    for p in paths:
        p.unlink(missing_ok=True)


# --- execução de um comando autorizado ----------------------------------------

def broker_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}


def execute_brokered(cmd: str, timeout: float = COMMAND_TIMEOUT,
                     limit: int = OUTPUT_LIMIT) -> tuple[int, str]:
    """Roda o comando EXATAMENTE como mostrado ao usuário (`bash -c`), no
    workdir do plugin, com ambiente mínimo e saída limitada a `limit` bytes.
    Retorna (código de saída, saída combinada). Roda no cgroup de quem chama —
    dentro do scope do modelo, quando chamado pelo servidor MCP — então cancelar
    a pergunta mata o comando junto; no timeout, o grupo de processos inteiro
    (inclusive o que o comando deixou em background) é morto."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(["bash", "-c", cmd], cwd=str(WORKDIR), env=broker_env(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    buf = bytearray()
    total = [0]

    def reader() -> None:
        assert proc.stdout is not None
        while True:
            data = proc.stdout.read1(8192)
            if not data:
                return
            total[0] += len(data)
            if len(buf) < limit:
                buf.extend(data[: limit - len(buf)])

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    _kill_group(proc)
    t.join(timeout=2)
    if proc.stdout is not None:
        proc.stdout.close()
    out = buf.decode(errors="replace")
    if total[0] > limit:
        out += f"\n[output truncated at {limit} bytes]"
    if timed_out:
        return (124, out + f"\n[timeout after {int(timeout)}s — command killed]")
    return (proc.returncode, out)


def _kill_group(proc: subprocess.Popen) -> None:
    """Mata o grupo de processos do comando (o próprio bash e o que ele deixou
    rodando em background na mesma sessão)."""
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
