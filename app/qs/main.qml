import QtQuick
import QtQuick.Window
import qs.Commons

// Janela standalone do painel do Jarvis via Qt puro (PySide6) — o caminho pra
// fora do Omarchy (Ubuntu etc., onde não há quickshell). Carregada por
// bin/jarvis-panel.py, que expõe `bridge` (estado + execução de comandos).
Window {
  id: win
  visible: true
  title: "Jarvis"
  color: Color.popups.background
  width: panel.implicitWidth + 36
  height: panel.implicitHeight + 32

  PanelContent {
    id: panel
    x: 18
    y: 16
    serviceState: bridge.serviceState
    detailText: bridge.detailText
    dictating: bridge.dictating
    installed: bridge.installed
    lang: bridge.lang
    config: bridge.config
    onRunRequested: function(cmd, close) {
      bridge.run(cmd)
    }
  }
}
