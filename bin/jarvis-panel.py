#!/usr/bin/python3
"""Janela standalone do painel do Jarvis via Qt puro (PySide6).

É o caminho pra rodar a MESMA interface do popup da bar fora do Omarchy
(Ubuntu, Fedora…), onde não existe quickshell: carrega app/qs/main.qml, que
renderiza o PanelContent.qml compartilhado. Este script só fornece o estado
(`jarvis status`, marcador de ditado, chaves de config) e executa as ações.

Uso: jarvis-panel.py [dir-do-app]   (default: ~/.local/share/jarvis/app,
ou o app/ do repositório quando rodando do checkout)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

JARVIS = str(Path(__file__).resolve().parent / "jarvis")
DICTATING_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "jarvis-dictating"
CONFIG_KEYS = ("language", "stt_provider", "quick_provider", "deep_model", "system_access")


def app_dir(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    installed = Path.home() / ".local/share/jarvis/app"
    if (installed / "qs/main.qml").exists():
        return installed
    return Path(__file__).resolve().parent.parent / "app"


class Bridge(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._state = "off"
        self._detail = ""
        self._dictating = False
        self._installed = True
        self._lang = "en"
        self._config: dict[str, str] = {}
        self._last_probe = 0.0

    def _jarvis(self, *args: str) -> str:
        try:
            return subprocess.run([JARVIS, *args], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def poll(self) -> None:
        out = self._jarvis("status")
        try:
            data = json.loads(out.strip().splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            self._installed = False
            self.changed.emit()
            return
        self._installed = True
        self._state = data.get("alt", "off")
        tooltip = str(data.get("tooltip", ""))
        lines = tooltip.split("\\n") if "\\n" in tooltip else tooltip.split("\n")
        detail = " · ".join(lines[1:]) if len(lines) > 1 else ""
        self._detail = re.sub(
            r"\w{3} \d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2}( [-+]\d{2,4}| \w+)?", r"\1", detail)
        self._dictating = DICTATING_FILE.exists()
        if time.monotonic() - self._last_probe > 30:
            self._last_probe = time.monotonic()
            self._config = {k: self._jarvis("config", "get", k).strip() for k in CONFIG_KEYS}
            if self._config.get("language"):
                self._lang = self._config["language"]
        self.changed.emit()

    @Slot(str)
    def run(self, cmd: str) -> None:
        subprocess.Popen(["bash", "-c", cmd],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        QTimer.singleShot(700, self.poll)

    serviceState = Property(str, lambda self: self._state, notify=changed)
    detailText = Property(str, lambda self: self._detail, notify=changed)
    dictating = Property(bool, lambda self: self._dictating, notify=changed)
    installed = Property(bool, lambda self: self._installed, notify=changed)
    lang = Property(str, lambda self: self._lang, notify=changed)
    config = Property("QVariant", lambda self: self._config, notify=changed)


def main() -> int:
    base = app_dir(sys.argv)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Jarvis")
    bridge = Bridge()
    bridge.poll()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(base))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(str(base / "qs/main.qml"))
    if not engine.rootObjects():
        print("jarvis-panel: falha ao carregar o QML", file=sys.stderr)
        return 1
    timer = QTimer()
    timer.timeout.connect(bridge.poll)
    timer.start(2000)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
