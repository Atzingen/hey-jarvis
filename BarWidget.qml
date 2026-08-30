import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Jarvis bar widget. The icon shows the voice-launcher.service state; hovering
// opens the panel (PanelContent.qml — the same QML the standalone `jarvis app`
// window renders). State plumbing lives in StatusPoller.qml, also shared.
// Texts follow the `language` key of ~/.config/jarvis/config.toml.
BarWidget {
  id: root
  moduleName: "atzingen.jarvis"

  property bool popupOpen: false

  readonly property bool isOn: poller.serviceState === "on"
  readonly property bool isPaused: poller.serviceState === "paused"
  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function run(cmd, close) {
    if (!root.bar) return
    root.bar.run(cmd)
    if (close) root.popupOpen = false
  }

  StatusPoller { id: poller }

  onPopupOpenChanged: if (popupOpen) poller.probeNow()

  Timer { id: openTimer; interval: 350; onTriggered: root.popupOpen = true }
  Timer {
    id: closeTimer
    interval: 450
    onTriggered: if (!rootHover.hovered && !popupHover.hovered) root.popupOpen = false
  }

  HoverHandler {
    id: rootHover
    onHoveredChanged: hovered ? openTimer.start() : closeTimer.restart()
  }

  // Safety net: cursor teleports and focus changes don't always deliver the
  // leave event to the popup; while open, re-check hover periodically.
  Timer {
    interval: 900
    running: root.popupOpen
    repeat: true
    onTriggered: if (!rootHover.hovered && !popupHover.hovered) root.popupOpen = false
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: poller.dictating ? "󰍬" : root.isOn ? "󰧑" : root.isPaused ? "󱍎" : "󱍄"
    active: root.isOn || poller.dictating
    // Active state in the theme accent (the bar default falls back to `urgent`, red).
    activeColor: poller.dictating ? Color.urgent : Color.accent
    tooltipText: ""

    onPressed: function(b) {
      if (!root.bar || !poller.installed) return
      if (b === Qt.RightButton) root.run("jarvis pause 30m && notify-send -t 1500 Jarvis 'paused 30 min'")
      else if (b === Qt.MiddleButton) root.run("jarvis dictate toggle")
      else root.run("jarvis toggle-notify")
    }
  }

  PopupWindow {
    id: popup
    visible: root.popupOpen
    color: "transparent"
    implicitWidth: Math.ceil(card.implicitWidth)
    implicitHeight: Math.ceil(card.implicitHeight)

    anchor {
      id: popupAnchor
      window: root.QsWindow.window
      adjustment: PopupAdjustment.Slide
      edges: Edges.Top | Edges.Left
      gravity: Edges.Bottom | Edges.Right
      rect.width: 1
      rect.height: 1
      onAnchoring: {
        var w = root.QsWindow.window
        if (!w) return
        var point = w.contentItem.mapFromItem(root, root.width / 2 - popup.implicitWidth / 2, root.height + 6)
        popupAnchor.rect.x = Math.round(point.x)
        popupAnchor.rect.y = Math.round(point.y)
      }
    }

    BorderSurface {
      id: card
      color: Color.popups.background
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, 1)
      radius: Style.cornerRadius
      implicitWidth: panelContent.implicitWidth + Style.space(18) * 2
      implicitHeight: panelContent.implicitHeight + Style.space(16) * 2

      HoverHandler {
        id: popupHover
        onHoveredChanged: if (!hovered) closeTimer.restart()
      }

      PanelContent {
        id: panelContent
        x: Style.space(18)
        y: Style.space(16)
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
}
