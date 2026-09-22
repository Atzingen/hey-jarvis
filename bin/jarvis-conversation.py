#!/usr/bin/python3
"""Janela gráfica da conversa do Jarvis via Qt puro (PySide6) — o caminho fora
do Omarchy (Ubuntu etc., onde não há quickshell). Carrega
app/qs/conversation-main.qml, que renderiza o MESMO ConversationContent.qml da
versão quickshell: avatar do Jarvis, anel que pulsa com a voz, balões da conversa.

Só fornece o estado: $XDG_RUNTIME_DIR/jarvis-state.json (fase, trocas, dicas,
escrito pelo voice-launcher) e jarvis-tts.json (envelope da fala em curso).
q/Esc na janela cria jarvis-quit (encerra a conversa); a janela fecha sozinha
quando o estado vira "closed".

Uso: jarvis-conversation.py [dir-do-app]   (default: ~/.local/share/jarvis/app,
ou o app/ do repositório quando rodando do checkout)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE_FILE = RUNTIME_DIR / "jarvis-state.json"
TTS_FILE = RUNTIME_DIR / "jarvis-tts.json"
QUIT_FLAG = RUNTIME_DIR / "jarvis-quit"


def app_dir(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    installed = Path.home() / ".local/share/jarvis/app"
    if (installed / "qs/conversation-main.qml").exists():
        return installed
    return Path(__file__).resolve().parent.parent / "app"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class Bridge(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._state: dict | None = None
        self._tts: dict | None = None
        self._raw_state = ""
        self._raw_tts = ""

    def poll(self) -> None:
        raw_state = _raw(STATE_FILE)
        raw_tts = _raw(TTS_FILE)
        if raw_state == self._raw_state and raw_tts == self._raw_tts:
            return
        self._raw_state, self._raw_tts = raw_state, raw_tts
        self._state = read_json(STATE_FILE)
        self._tts = read_json(TTS_FILE)
        self.changed.emit()
        if self._state is None or self._state.get("phase") == "closed":
            QGuiApplication.quit()

    @Slot()
    def quit(self) -> None:
        try:
            QUIT_FLAG.touch()
        except OSError:
            pass
        QGuiApplication.quit()

    state = Property("QVariant", lambda self: self._state, notify=changed)
    tts = Property("QVariant", lambda self: self._tts, notify=changed)


def _raw(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def main() -> int:
    base = app_dir(sys.argv)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Jarvis")
    bridge = Bridge()
    bridge.poll()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(base))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(str(base / "qs/conversation-main.qml"))
    if not engine.rootObjects():
        print("jarvis-conversation: falha ao carregar o QML", file=sys.stderr)
        return 1
    timer = QTimer()
    timer.timeout.connect(bridge.poll)
    timer.start(100)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
