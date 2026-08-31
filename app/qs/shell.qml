import QtQuick
import Quickshell
import qs.Commons

// Janela standalone do painel do Jarvis rodando em quickshell (Omarchy/Arch):
//   quickshell -p <este diretório>
// Renderiza o MESMO PanelContent.qml do popup da bar (arquivo real neste
// diretório — o quickshell só carrega arquivos dentro do config root); o estado vem do
// mesmo StatusPoller.qml. Fora do quickshell, use bin/jarvis-panel.py (PySide6).
ShellRoot {
  FloatingWindow {
    id: win
    title: "Jarvis"
    color: Color.popups.background
    implicitWidth: panel.implicitWidth + 36
    implicitHeight: panel.implicitHeight + 32

    StatusPoller { id: poller }

    PanelContent {
      id: panel
      x: 18
      y: 16
      serviceState: poller.serviceState
      detailText: poller.detailText
      dictating: poller.dictating
      installed: poller.installed
      lang: poller.lang
      config: poller.config
      onRunRequested: function(cmd, close) {
        Quickshell.execDetached(["bash", "-c", cmd])
      }
    }
  }
}
