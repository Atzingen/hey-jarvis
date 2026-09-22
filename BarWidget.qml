import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "app/qs" as Shared

// Jarvis bar widget. The icon shows the voice-launcher.service state; hovering
// shows a one-line tooltip ("Jarvis — active"), a click opens the panel
// (PanelContent.qml — the same QML the standalone `jarvis app` window renders).
// State plumbing lives in StatusPoller.qml, also shared.
// Texts follow the `language` key of ~/.config/jarvis/config.toml.
BarWidget {
  id: root
  moduleName: "atzingen.jarvis"

  property bool popupOpen: false

  readonly property bool isOn: poller.serviceState === "on" || poller.serviceState === "manual"
  readonly property bool isPaused: poller.serviceState === "paused"
  readonly property bool pt: poller.lang.indexOf("pt") === 0
  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Short state for the tooltip; the panel carries the full status line.
  readonly property string stateWord: !poller.installed ? (pt ? "não instalado" : "not installed")
    : poller.dictating ? (pt ? "gravando ditado" : "recording dictation")
    : poller.serviceState === "manual" ? (pt ? "ativo, só atalhos" : "active, hotkeys only")
    : isOn ? (pt ? "ativo" : "active")
    : isPaused ? (pt ? "pausado" : "paused")
    : (pt ? "desligado" : "off")

  // Popout contract the bar expects on the widget root (requestPopout closes
  // the previous panel through close(); shell.summon/toggle route via
  // open/close/opened).
  readonly property bool opened: popupOpen
  function open() { popupOpen = true }
  function close() { popupOpen = false }
  function togglePanel() { popupOpen = !popupOpen }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function run(cmd, close) {
    if (!root.bar) return
    root.bar.run(cmd)
    poller.refreshSoon()
    if (close) root.popupOpen = false
  }

  Shared.StatusPoller { id: poller }

  onPopupOpenChanged: if (popupOpen) poller.probeNow()

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: poller.dictating ? "󰍬" : root.isOn ? "󰧑" : root.isPaused ? "󱍎" : "󱍄"
    active: poller.serviceState === "on" || poller.dictating  // modo manual: cérebro sem cor de ativo
    // Active state in the theme accent (the bar default falls back to `urgent`, red).
    activeColor: poller.dictating ? Color.urgent : Color.accent
    tooltipText: root.popupOpen ? "" : "Jarvis — " + root.stateWord

    onPressed: function(b) {
      if (!root.bar) return
      if (b === Qt.RightButton) { if (poller.installed) root.run("jarvis pause 30m && notify-send -t 1500 Jarvis 'paused 30 min'") }
      else if (b === Qt.MiddleButton) { if (poller.installed) root.run("jarvis dictate toggle") }
      else root.togglePanel()
    }
  }

  // Click-mode popup: the bar coordinates it with the other panels and a
  // Hyprland focus grab closes it on any click outside the card or the bar.
  PopupCard {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(panelContent.implicitWidth + popup.padding * 2
      + Border.left(popup.borderSpec) + Border.right(popup.borderSpec))
    contentHeight: popup.fittedContentHeight(panelContent.implicitHeight)

    Shared.PanelContent {
      id: panelContent
      serviceState: poller.serviceState
      detailText: poller.detailText
      dictating: poller.dictating
      installed: poller.installed
      lang: poller.lang
      config: poller.config
      pluginDir: root.pluginDir
      fontFamily: root.fontFamily
      onRunRequested: function(cmd, close) { root.run(cmd, close) }
    }
  }
}
