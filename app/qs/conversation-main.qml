import QtQuick
import QtQuick.Window
import qs.Commons

// Janela gráfica da conversa via Qt puro (PySide6) — fora do Omarchy. Carregada
// por bin/jarvis-conversation.py, que expõe `bridge` (state, tts, quit()).
Window {
  id: win
  visible: true
  title: "Jarvis"
  color: content.background
  width: content.implicitWidth
  height: content.implicitHeight
  minimumWidth: content.implicitWidth
  minimumHeight: content.implicitHeight

  ConversationContent {
    id: content
    anchors.fill: parent
    state: bridge.state
    tts: bridge.tts
    onQuitRequested: bridge.quit()
  }
}
