import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Jarvis bar widget. The icon shows the voice-launcher.service state; hovering
// opens a panel with the spoken-command guide and controls (on/off, pause,
// logs, settings). Texts follow the `language` key of ~/.config/jarvis/config.toml.
// When the assistant is not installed yet, the panel offers to run install.sh.
BarWidget {
  id: root
  moduleName: "atzingen.jarvis"

  property string serviceState: "off"   // "on" | "off" | "paused"
  property string detailText: ""
  property bool popupOpen: false
  property bool installed: true
  property string lang: "en"

  readonly property bool isOn: serviceState === "on"
  readonly property bool isPaused: serviceState === "paused"
  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property bool pt: lang.indexOf("pt") === 0

  // --- strings -----------------------------------------------------------
  function t(key) {
    var s = {
      statusOn:       pt ? "Ativo — escutando “hey jarvis”" : "Active — listening for “hey jarvis”",
      statusPaused:   pt ? "Pausado" : "Paused",
      statusOff:      pt ? "Desligado — microfone livre" : "Off — microphone free",
      notInstalled:   pt ? "Não instalado — clique em Instalar para configurar o serviço de voz" : "Not installed — click Install to set up the voice service",
      intro:          pt ? "Diga “hey jarvis” e fale depois da saudação — ele escuta até você\nparar e abre a janela da conversa. Não há palavras-chave: o modelo decide."
                         : "Say “hey jarvis” and talk after the greeting — it listens until you\nstop and opens the conversation window. No keywords: the model decides.",
      r1p: pt ? "“abre o projeto X”" : "“open project X”",              r1a: pt ? "layout dev: Ghostty 2×2 + VS Code + Chrome" : "dev layout: Ghostty 2×2 + VS Code + Chrome",
      r2p: pt ? "“abre o btop / o Chrome”" : "“open btop / Chrome”",      r2a: pt ? "abre um app instalado" : "launches an installed app",
      r3p: pt ? "“pense bem <pergunta>”" : "“think hard <question>”",   r3a: pt ? "modelo mais forte (Claude Fable)" : "stronger model (Claude Fable)",
      r4p: pt ? "“quantos containers no Docker?”" : "“how many Docker containers?”", r4a: pt ? "ele roda o comando e responde o resultado" : "runs the command, answers with the result",
      r5p: pt ? "falar por cima da resposta" : "talk over the answer",   r5a: pt ? "ele para e escuta você (barge-in)" : "it stops and listens (barge-in)",
      r6p: pt ? "“fecha a conversa” / “é só isso”" : "“close it” / “that's all, thanks”", r6a: pt ? "encerra (o modelo entende)" : "ends it (the model understands)",
      r7p: pt ? "“pode dormir”" : "“go to sleep”",                        r7a: pt ? "suspende o computador" : "suspends the computer",
      k1p: "Ctrl+Shift+H", k1a: pt ? "falar agora — pula o “hey jarvis” (push-to-talk)" : "talk now — skips “hey jarvis” (push-to-talk)",
      k2p: "Ctrl+Shift+J", k2a: pt ? "liga/desliga o Jarvis" : "toggles Jarvis on/off",
      k3p: "Ctrl+Shift+K", k3a: pt ? "ditado: fale, aperte de novo e o texto é colado na janela ativa" : "dictation: talk, press again, text is pasted into the active window",
      k4p: "jarvis config", k4a: pt ? "tela de configuração no terminal" : "settings screen in the terminal",
      btnOn: pt ? "Ligar" : "Turn on", btnOff: pt ? "Desligar" : "Turn off",
      btnPause: pt ? "Pausar 30 min" : "Pause 30 min", btnLogs: "Logs",
      btnConfig: pt ? "Config" : "Settings", btnInstall: pt ? "Instalar" : "Install"
    }
    return s[key] || key
  }

  readonly property string statusLine: !installed ? t("notInstalled")
    : isOn ? t("statusOn") : isPaused ? t("statusPaused") : t("statusOff")

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function applyStatus(raw) {
    var data
    try { data = JSON.parse(String(raw).trim()) } catch (e) { return }
    serviceState = data.alt === "on" ? "on" : data.alt === "paused" ? "paused" : "off"
    // 2nd tooltip line of the CLI carries the useful detail ("desde <ts>", "volta em Xmin").
    var lines = String(data.tooltip || "").split("\\n")
    if (lines.length < 2) lines = String(data.tooltip || "").split("\n")
    detailText = lines.length > 1 ? lines.slice(1).join(" · ")
      : (lines[0] || "").indexOf("(") >= 0 ? lines[0].replace(/^[^(]*/, "") : ""
  }

  // `jarvis` on PATH == voice service installed. Also reads the language.
  Process {
    id: probeProc
    command: ["bash", "-c", "command -v jarvis >/dev/null && echo installed && jarvis config get language 2>/dev/null || echo missing"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text).trim().split("\n")
        root.installed = lines[0] === "installed"
        if (root.installed && lines.length > 1 && lines[1]) root.lang = lines[1].trim()
      }
    }
  }

  Process {
    id: statusProc
    command: ["jarvis", "status"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
  }

  Timer {
    interval: 3000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      if (!probeProc.running) probeProc.running = true
      if (root.installed && !statusProc.running) statusProc.running = true
    }
  }

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
    text: root.isOn ? "󰧑" : root.isPaused ? "󱍎" : "󱍄"
    active: root.isOn
    // Active state in the theme accent (the bar default falls back to `urgent`, red).
    activeColor: Color.accent
    tooltipText: ""

    onPressed: function(b) {
      if (!root.bar || !root.installed) return
      if (b === Qt.RightButton) root.bar.run("jarvis pause 30m && notify-send -t 1500 Jarvis 'paused 30 min'")
      else root.bar.run("jarvis toggle-notify")
    }
  }

  component VoiceRow: Row {
    property string phrase: ""
    property string action: ""
    spacing: 10
    Text {
      text: phrase
      width: 360
      elide: Text.ElideRight
      color: Color.accent
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      wrapMode: Text.NoWrap
    }
    Text {
      text: action
      color: Color.popups.text
      opacity: 0.85
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      wrapMode: Text.NoWrap
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
      implicitWidth: content.implicitWidth + 32
      implicitHeight: content.implicitHeight + 28

      HoverHandler {
        id: popupHover
        onHoveredChanged: if (!hovered) closeTimer.restart()
      }

      Column {
        id: content
        x: 16
        y: 14
        spacing: 10

        Row {
          spacing: 8
          Rectangle {
            width: 10; height: 10; radius: 5
            anchors.verticalCenter: parent.verticalCenter
            color: root.isOn ? Color.accent : root.isPaused ? "#d9a441" : Color.popups.text
            opacity: root.isOn || root.isPaused ? 1.0 : 0.35
          }
          Text {
            text: "Jarvis  ·  " + root.statusLine
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.subtitle
            font.weight: Font.DemiBold
          }
        }

        Text {
          visible: root.detailText !== ""
          text: root.detailText
          color: Color.popups.text
          opacity: 0.6
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Rectangle { width: content.implicitWidth; height: 1; color: Color.popups.text; opacity: 0.15 }

        Text {
          text: root.t("intro")
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          lineHeight: 1.2
        }

        Column {
          spacing: 5
          VoiceRow { phrase: root.t("r1p"); action: root.t("r1a") }
          VoiceRow { phrase: root.t("r2p"); action: root.t("r2a") }
          VoiceRow { phrase: root.t("r3p"); action: root.t("r3a") }
          VoiceRow { phrase: root.t("r4p"); action: root.t("r4a") }
          VoiceRow { phrase: root.t("r5p"); action: root.t("r5a") }
          VoiceRow { phrase: root.t("r6p"); action: root.t("r6a") }
          VoiceRow { phrase: root.t("r7p"); action: root.t("r7a") }
        }

        Rectangle { width: content.implicitWidth; height: 1; color: Color.popups.text; opacity: 0.15 }

        Column {
          spacing: 5
          VoiceRow { phrase: root.t("k1p"); action: root.t("k1a") }
          VoiceRow { phrase: root.t("k2p"); action: root.t("k2a") }
          VoiceRow { phrase: root.t("k3p"); action: root.t("k3a") }
          VoiceRow { phrase: root.t("k4p"); action: root.t("k4a") }
        }

        Rectangle { width: content.implicitWidth; height: 1; color: Color.popups.text; opacity: 0.15 }

        Row {
          spacing: 8

          Button {
            text: root.t("btnInstall")
            bordered: true
            visible: !root.installed
            onClicked: { root.bar.run("xdg-terminal-exec bash " + root.pluginDir + "/install.sh"); root.popupOpen = false }
          }
          Button {
            text: root.isOn ? root.t("btnOff") : root.t("btnOn")
            bordered: true
            visible: root.installed
            onClicked: { root.bar.run("jarvis toggle-notify"); root.popupOpen = false }
          }
          Button {
            text: root.t("btnPause")
            bordered: true
            visible: root.installed && root.isOn
            onClicked: { root.bar.run("jarvis pause 30m"); root.popupOpen = false }
          }
          Button {
            text: root.t("btnLogs")
            bordered: true
            visible: root.installed
            onClicked: { root.bar.run("xdg-terminal-exec jarvis log"); root.popupOpen = false }
          }
          Button {
            text: root.t("btnConfig")
            bordered: true
            visible: root.installed
            onClicked: { root.bar.run("jarvis config"); root.popupOpen = false }
          }
        }
      }
    }
  }
}
