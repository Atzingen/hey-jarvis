import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons

// Janela gráfica da conversa do Jarvis em quickshell (Omarcho/Arch):
//   quickshell -p <este arquivo>
// Janela em layer-shell, ancorada no topo do monitor com foco, sem regra do
// Hyprland: aparece por cima, some sozinha quando o estado vira "closed".
// Lê os mesmos arquivos do viewer de terminal (jarvis-state.json, jarvis-tts.json)
// e as cores do tema atual do Omarchy; q/Esc (com o mouse sobre a janela) ou o
// ✕ criam jarvis-quit, que encerra a conversa.
ShellRoot {
  id: root

  // JARVIS_RUNTIME_DIR só para testes com um estado sintético; o Wayland precisa do XDG_RUNTIME_DIR real
  readonly property string runtimeDir: Quickshell.env("JARVIS_RUNTIME_DIR") || Quickshell.env("XDG_RUNTIME_DIR") || ("/run/user/" + Quickshell.env("UID"))
  readonly property string themeFile: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"

  property var state: null
  property var tts: null
  property bool closing: false

  function parse(text) {
    try { return JSON.parse(String(text)) } catch (e) { return null }
  }

  function quit() {
    if (root.closing) return
    root.closing = true
    Quickshell.execDetached(["touch", root.runtimeDir + "/jarvis-quit"])
    Qt.quit()
  }

  FileView {
    id: stateFile
    path: root.runtimeDir + "/jarvis-state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var s = root.parse(text())
      root.state = s
      if (s === null || s.phase === "closed") Qt.quit()
    }
    onLoadFailed: if (root.state !== null) Qt.quit()
  }
  FileView {
    id: ttsFile
    path: root.runtimeDir + "/jarvis-tts.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.tts = root.parse(text())
    onLoadFailed: root.tts = null
  }
  FileView {
    id: themeFileView
    path: root.themeFile
    printErrors: false
  }
  // O launcher escreve por rename atômico; o watcher nem sempre vê o novo inode,
  // então a releitura periódica é a rede de segurança.
  Timer {
    interval: 150; running: true; repeat: true
    onTriggered: { stateFile.reload(); ttsFile.reload() }
  }

  PanelWindow {
    id: win
    screen: {
      // JARVIS_SCREEN só para testes (força um monitor); normal é o monitor com foco
      var name = Quickshell.env("JARVIS_SCREEN") || (Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : "")
      for (var i = 0; i < Quickshell.screens.length; i++)
        if (Quickshell.screens[i].name === name) return Quickshell.screens[i]
      return Quickshell.screens.length ? Quickshell.screens[0] : null
    }
    anchors.top: true
    margins.top: Style.space(52)
    // 1 px transparente ao redor do cartão: com escala fracionária (1.25, 1.333…)
    // a borda encostada na beira da superfície some no arredondamento de pixels.
    implicitWidth: content.implicitWidth + 4
    implicitHeight: content.implicitHeight + 4
    exclusionMode: ExclusionMode.Ignore
    color: "transparent"
    WlrLayershell.namespace: "jarvis-conversation"
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand

    Rectangle {
      anchors.fill: parent
      anchors.margins: 1
      radius: Style.cornerRadius
      color: content.background
      border.width: 1
      border.color: Qt.rgba(content.accent.r, content.accent.g, content.accent.b, 0.7)
      clip: true

      ConversationContent {
        id: content
        anchors.fill: parent
        anchors.margins: 1
        state: root.state
        tts: root.tts
        themeRaw: themeFileView.text()
        onQuitRequested: root.quit()
      }
    }
  }
}
