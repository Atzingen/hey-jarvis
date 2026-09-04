import QtQuick
import qs.Commons
import qs.Ui

// Conteúdo do painel do Jarvis — o MESMO QML nos dois lugares:
//   - no popup do widget da bar (BarWidget.qml), com os módulos reais do omarchy-shell;
//   - na janela standalone (`jarvis app`), com os shims de app/qs (quickshell ou PySide6).
// Recebe o estado por propriedades e pede ações pelo sinal runRequested.
Item {
  id: panel

  property string serviceState: "off"   // "on" | "off" | "paused"
  property string detailText: ""
  property bool dictating: false
  property bool installed: true
  property string lang: "en"
  property var config: ({})             // language, stt_provider, quick_provider, deep_model, system_access
  property string pluginDir: ""         // onde está o install.sh (só usado pelo botão Instalar)
  property string fontFamily: Style.font.family

  signal runRequested(string cmd, bool close)

  readonly property bool isOn: serviceState === "on" || serviceState === "manual"
  readonly property bool wakeOn: serviceState === "on"
  readonly property bool isPaused: serviceState === "paused"
  readonly property bool pt: lang.indexOf("pt") === 0

  // --- palette ------------------------------------------------------------
  // Section tints derive from the theme accent so every theme stays coherent:
  // voice = accent, dictation = accent rotated one way, keys = the other way.
  // Achromatic accents (gray themes) get a minimum saturation.
  readonly property color fg: Color.popups.text
  readonly property color voiceColor: Color.accent
  readonly property color dictColor: panel.tone(Color.accent, -0.22)
  readonly property color keysColor: panel.tone(Color.accent, 0.38)
  readonly property color pausedColor: "#d9a441"
  readonly property color stateColor: dictating ? Color.urgent : isOn ? Color.accent : isPaused ? pausedColor : Qt.darker(fg, 1.6)

  function tone(base, shift) {
    var h = base.hslHue < 0 ? 0.08 : base.hslHue
    var s = Math.max(base.hslSaturation, 0.45)
    var l = Math.min(Math.max(base.hslLightness, 0.58), 0.74)
    return Qt.hsla((h + shift + 1) % 1, s, l, 1)
  }

  // --- strings -----------------------------------------------------------
  readonly property var str: pt ? ({
    statusOn: "Ativo — escutando “hey jarvis”", statusPaused: "Pausado", statusOff: "Desligado — microfone livre",
    statusManual: "Ativo — só atalhos (“hey jarvis” desligado, mic fechado)",
    swService: "serviço", swWake: "“hey jarvis”",
    keyWake: "liga/desliga só a escuta “hey jarvis”",
    statusDictating: "Gravando ditado…",
    notInstalled: "Não instalado — clique em Instalar para configurar o serviço de voz",
    chipLang: "IDIOMA", chipStt: "STT", chipQuick: "RÁPIDO", chipDeep: "PENSE BEM", chipAccess: "ACESSO",
    voiceTitle: "CONVERSA POR VOZ",
    intro: "Diga “hey jarvis” e fale depois da saudação — ele escuta até você parar e abre a janela da conversa. Não há palavras-chave: o modelo decide.",
    rows: [
      ["“abre o projeto X”", "layout dev: Ghostty 2×2 + VS Code + Chrome"],
      ["“abre o btop / o Chrome”", "abre um app instalado"],
      ["“pense bem <pergunta>”", "modelo mais forte (Claude Fable)"],
      ["“quantos containers no Docker?”", "roda o comando e responde o resultado"],
      ["falar por cima da resposta", "ele para e escuta você (barge-in)"],
      ["“fecha a conversa” / “é só isso”", "encerra (o modelo entende)"],
      ["“pode dormir”", "suspende o computador"]
    ],
    dictTitle: "DITADO",
    dictReady: "pronto", dictRecording: "gravando", dictNeedsOn: "Jarvis desligado",
    dictIntro: "Speech-to-text: fale e o texto é transcrito e colado na janela ativa (vai pro topo do clipboard).",
    dictK: "aperta, fala, aperta de novo",
    dictL: "segura e fala, solta pra colar",
    dictOther: "outra tecla", dictOtherAction: "cancela e descarta",
    dictStart: "Ditar agora", dictStop: "Parar e colar",
    keysTitle: "ATALHOS",
    keyH: "falar agora, sem “hey jarvis”",
    keyDictToggle: "ditado (toggle)", keyDictPtt: "ditado (push-to-talk)",
    keyJ: "liga/desliga o Jarvis",
    keyClick: "clique", keyClickAction: "liga/desliga",
    keyRight: "direito", keyRightAction: "pausa 30 min",
    keyConfig: "configuração no terminal",
    tipOn: "Ligar", tipOff: "Desligar", tipPause: "Pausar 30 min", tipDictate: "Ditar agora",
    tipDictateStop: "Parar e colar", tipLogs: "Logs (journalctl -f)", tipConfig: "Configuração",
    btnInstall: "Instalar",
    footHint: "ícone da bar: clique liga/desliga · direito pausa 30 min · meio dita"
  }) : ({
    statusOn: "Active — listening for “hey jarvis”", statusPaused: "Paused", statusOff: "Off — microphone free",
    statusManual: "Active — hotkeys only (“hey jarvis” off, mic closed)",
    swService: "service", swWake: "“hey jarvis”",
    keyWake: "toggles just the wake-word listening",
    statusDictating: "Recording dictation…",
    notInstalled: "Not installed — click Install to set up the voice service",
    chipLang: "LANGUAGE", chipStt: "STT", chipQuick: "QUICK", chipDeep: "THINK HARD", chipAccess: "ACCESS",
    voiceTitle: "VOICE CONVERSATION",
    intro: "Say “hey jarvis” and talk after the greeting — it listens until you stop and opens the conversation window. No keywords: the model decides.",
    rows: [
      ["“open project X”", "dev layout: Ghostty 2×2 + VS Code + Chrome"],
      ["“open btop / Chrome”", "launches an installed app"],
      ["“think hard <question>”", "stronger model (Claude Fable)"],
      ["“how many Docker containers?”", "runs the command, answers with the result"],
      ["talk over the answer", "it stops and listens (barge-in)"],
      ["“close it” / “that's all, thanks”", "ends it (the model understands)"],
      ["“go to sleep”", "suspends the computer"]
    ],
    dictTitle: "DICTATION",
    dictReady: "ready", dictRecording: "recording", dictNeedsOn: "Jarvis off",
    dictIntro: "Speech-to-text: talk and the text is transcribed and pasted into the active window (top of the clipboard).",
    dictK: "press · talk · press again",
    dictL: "hold to talk, release to paste",
    dictOther: "other key", dictOtherAction: "cancels and discards",
    dictStart: "Dictate now", dictStop: "Stop and paste",
    keysTitle: "KEYBINDINGS",
    keyH: "talk now, no “hey jarvis” needed",
    keyDictToggle: "dictation (toggle)", keyDictPtt: "dictation (push-to-talk)",
    keyJ: "toggles Jarvis on/off",
    keyClick: "click", keyClickAction: "toggle on/off",
    keyRight: "right", keyRightAction: "pause 30 min",
    keyConfig: "settings in the terminal",
    tipOn: "Turn on", tipOff: "Turn off", tipPause: "Pause 30 min", tipDictate: "Dictate now",
    tipDictateStop: "Stop and paste", tipLogs: "Logs (journalctl -f)", tipConfig: "Settings",
    btnInstall: "Install",
    footHint: "bar icon: click toggles · right-click pauses 30 min · middle-click dictates"
  })

  readonly property string statusLine: !installed ? str.notInstalled
    : dictating ? str.statusDictating
    : serviceState === "manual" ? str.statusManual
    : isOn ? str.statusOn : isPaused ? str.statusPaused : str.statusOff

  function run(cmd, close) { panel.runRequested(cmd, close) }

  // --- building blocks -----------------------------------------------------

  // Rounded card with a colored rail, a small-caps title and an optional meta
  // label on the right. Children go into the body column.
  component SectionCard: Rectangle {
    id: sc
    property string title: ""
    property string meta: ""
    property color metaColor: Qt.darker(panel.fg, 1.5)
    property color tint: Color.accent
    property int pad: Style.space(12)
    property int bodySpacing: Style.space(6)
    default property alias body: bodyCol.data

    radius: Style.cornerRadius + 4
    color: Util.alpha(panel.fg, 0.03)
    border.width: 1
    border.color: Util.alpha(sc.tint, 0.22)
    implicitHeight: bodyCol.y + bodyCol.implicitHeight + pad

    Rectangle {
      x: 0; y: sc.pad + Style.space(2)
      width: Style.space(3); height: header.implicitHeight - Style.space(4)
      radius: 2
      color: sc.tint
    }

    Item {
      id: header
      x: sc.pad; y: sc.pad
      width: sc.width - sc.pad * 2
      implicitHeight: Math.max(titleText.implicitHeight, metaText.implicitHeight)
      Text {
        id: titleText
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(0, parent.width - (metaText.visible ? metaText.implicitWidth + Style.space(12) : 0))
        elide: Text.ElideRight
        text: sc.title
        color: sc.tint
        font.family: panel.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
        font.bold: true
      }
      Text {
        id: metaText
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: sc.meta
        visible: sc.meta !== ""
        color: sc.metaColor
        font.family: panel.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
      }
    }

    Column {
      id: bodyCol
      x: sc.pad
      y: header.y + header.implicitHeight + Style.space(8)
      width: sc.width - sc.pad * 2
      spacing: sc.bodySpacing
    }
  }

  // Switch do painel: estado ligado bem evidente — trilho preenchido com a cor
  // de destaque, knob claro e um halo suave; desligado fica apagado.
  component PanelSwitch: Item {
    id: sw
    property bool checked: false
    property color accent: Color.accent
    signal toggled()

    readonly property int trackH: Style.space(22)
    readonly property int trackW: Math.round(trackH * 1.9)
    readonly property int knobSize: Math.round(trackH * 0.72)
    readonly property int knobInset: Math.round((trackH - knobSize) / 2)

    implicitWidth: trackW + Style.space(8)
    implicitHeight: trackH + Style.space(8)

    // halo (dois anéis de alpha decrescente — brilho sem shader)
    Rectangle {
      anchors.centerIn: parent
      width: sw.trackW + Style.space(8); height: sw.trackH + Style.space(8)
      radius: height / 2
      color: Util.alpha(sw.accent, 0.14)
      opacity: sw.checked ? 1.0 : 0.0
      Behavior on opacity { NumberAnimation { duration: 160 } }
    }
    Rectangle {
      anchors.centerIn: parent
      width: sw.trackW + Style.space(4); height: sw.trackH + Style.space(4)
      radius: height / 2
      color: Util.alpha(sw.accent, 0.22)
      opacity: sw.checked ? 1.0 : 0.0
      Behavior on opacity { NumberAnimation { duration: 160 } }
    }

    Rectangle {
      id: track
      anchors.centerIn: parent
      width: sw.trackW; height: sw.trackH
      radius: height / 2
      color: sw.checked ? Util.alpha(sw.accent, 0.85) : Util.alpha(panel.fg, 0.10)
      border.width: 1
      border.color: sw.checked ? Qt.lighter(sw.accent, 1.25) : Util.alpha(panel.fg, 0.35)
      Behavior on color { ColorAnimation { duration: 160 } }
      Behavior on border.color { ColorAnimation { duration: 160 } }

      Rectangle {
        width: sw.knobSize; height: sw.knobSize; radius: sw.knobSize / 2
        anchors.verticalCenter: parent.verticalCenter
        x: sw.checked ? track.width - width - sw.knobInset : sw.knobInset
        color: sw.checked ? Qt.lighter(sw.accent, 1.6) : Qt.darker(panel.fg, 1.5)
        Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: 160 } }
      }
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: sw.toggled()
    }
  }

  // Keyboard key as a small bordered cap.
  component KeyCap: Rectangle {
    id: cap
    property string label: ""
    property color tint: panel.keysColor
    implicitWidth: capText.implicitWidth + Style.space(12)
    implicitHeight: capText.implicitHeight + Style.space(5)
    radius: Style.cornerRadius > 0 ? Math.max(3, Style.cornerRadius - 2) : 0
    color: Util.alpha(cap.tint, 0.10)
    border.width: 1
    border.color: Util.alpha(cap.tint, 0.5)
    Text {
      id: capText
      anchors.centerIn: parent
      text: cap.label
      color: cap.tint
      font.family: panel.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }

  // KeyCap + explanation, caps aligned in a fixed-width column.
  component KeyRow: Row {
    id: keyRow
    property string key: ""
    property string action: ""
    property color tint: panel.keysColor
    property int keyWidth: Style.space(100)
    spacing: Style.space(8)
    Item {
      width: keyRow.keyWidth
      height: capItem.implicitHeight
      KeyCap { id: capItem; label: keyRow.key; tint: keyRow.tint }
    }
    Text {
      anchors.verticalCenter: parent.verticalCenter
      width: keyRow.width - keyRow.keyWidth - keyRow.spacing
      text: keyRow.action
      color: panel.fg
      opacity: 0.85
      font.family: panel.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }

  // Spoken phrase (tinted) → what happens.
  component VoiceRow: Row {
    id: voiceRow
    property string phrase: ""
    property string action: ""
    property int phraseWidth: Style.space(290)
    spacing: Style.space(10)
    Text {
      text: voiceRow.phrase
      width: voiceRow.phraseWidth
      elide: Text.ElideRight
      color: panel.voiceColor
      font.family: panel.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      text: voiceRow.action
      width: voiceRow.width - voiceRow.phraseWidth - voiceRow.spacing
      elide: Text.ElideRight
      color: panel.fg
      opacity: 0.85
      font.family: panel.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  // "LABEL value" chip for the config strip.
  component Chip: Rectangle {
    id: chip
    property string label: ""
    property string value: ""
    property color tint: panel.fg
    visible: value !== ""
    implicitWidth: chipRow.implicitWidth + Style.space(14)
    implicitHeight: chipRow.implicitHeight + Style.space(6)
    radius: Style.cornerRadius > 0 ? height / 2 : 0
    color: Util.alpha(panel.fg, 0.04)
    border.width: 1
    border.color: Util.alpha(panel.fg, 0.10)
    Row {
      id: chipRow
      anchors.centerIn: parent
      spacing: Style.space(6)
      Text {
        text: chip.label
        color: Qt.darker(panel.fg, 1.5)
        font.family: panel.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
      }
      Text {
        text: chip.value
        color: chip.tint
        font.family: panel.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
      }
    }
  }

  component FooterAction: PanelActionButton {
    foreground: panel.fg
    fontFamily: panel.fontFamily
    fontSize: Style.font.iconLarge
    size: Style.space(34)
    bordered: true
  }

  // --- layout ----------------------------------------------------------------

  readonly property int contentW: Style.space(700)
  readonly property int colGap: Style.space(10)
  readonly property int colW: Math.floor((contentW - colGap) / 2)

  implicitWidth: contentW
  implicitHeight: content.implicitHeight

  Column {
    id: content
    width: panel.contentW
    spacing: Style.space(12)

    // ---- Hero: icon, name, state; on/off switch (or Install) on the right.
    Item {
      width: parent.width
      height: heroLabels.implicitHeight + Style.space(6)

      Text {
        id: heroIcon
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: panel.dictating ? "󰍬" : panel.isOn ? "󰧑" : panel.isPaused ? "󱍎" : "󱍄"
        color: panel.stateColor
        font.family: panel.fontFamily
        font.pixelSize: Style.font.displayLarge + Style.space(10)
      }

      Column {
        id: heroLabels
        anchors.left: heroIcon.right
        anchors.leftMargin: Style.space(14)
        anchors.right: heroTrailing.left
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(3)

        Text {
          text: "Jarvis"
          color: panel.fg
          font.family: panel.fontFamily
          font.pixelSize: Style.font.heading
          font.bold: true
        }
        Row {
          spacing: Style.space(7)
          width: parent.width
          Rectangle {
            id: stateDot
            width: Style.space(8); height: width; radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            color: panel.stateColor
            opacity: panel.isOn || panel.isPaused || panel.dictating ? 1.0 : 0.45
            SequentialAnimation on opacity {
              running: panel.dictating
              loops: Animation.Infinite
              NumberAnimation { to: 0.25; duration: 450 }
              NumberAnimation { to: 1.0; duration: 450 }
            }
          }
          Text {
            width: parent.width - stateDot.width - parent.spacing
            text: panel.statusLine + (panel.detailText !== "" && !panel.dictating ? "  ·  " + panel.detailText : "")
            elide: Text.ElideRight
            color: panel.fg
            opacity: 0.85
            font.family: panel.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }

      Item {
        id: heroTrailing
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: panel.installed ? heroSwitch.implicitWidth : installBtn.implicitWidth
        height: panel.installed ? heroSwitch.implicitHeight : installBtn.implicitHeight

        Column {
          id: heroSwitch
          visible: panel.installed
          spacing: Style.space(4)

          Row {
            anchors.right: parent.right
            spacing: Style.space(8)
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: panel.str.swService
              color: Qt.darker(panel.fg, 1.5)
              font.family: panel.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1
            }
            PanelSwitch {
              checked: panel.isOn
              accent: Color.accent
              onToggled: panel.run("jarvis toggle-notify", false)
            }
          }
          Row {
            anchors.right: parent.right
            spacing: Style.space(8)
            opacity: panel.isOn ? 1.0 : 0.4
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: panel.str.swWake
              color: Qt.darker(panel.fg, 1.5)
              font.family: panel.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1
            }
            PanelSwitch {
              checked: panel.wakeOn
              accent: Color.accent
              onToggled: if (panel.isOn) panel.run("jarvis wake toggle", false)
            }
          }
        }
        Button {
          id: installBtn
          visible: !panel.installed && panel.pluginDir !== ""
          text: panel.str.btnInstall
          bordered: true
          foreground: panel.fg
          fontFamily: panel.fontFamily
          onClicked: panel.run("xdg-terminal-exec bash '" + panel.pluginDir.replace(/'/g, "'\\''") + "/install.sh'", true)
        }
      }
    }

    // ---- Config chips: what the assistant is running with right now.
    Flow {
      visible: panel.installed
      width: parent.width
      spacing: Style.space(6)
      Chip { label: panel.str.chipLang;  value: panel.config.language || "";        tint: panel.fg }
      Chip { label: panel.str.chipStt;   value: panel.config.stt_provider || "";    tint: panel.dictColor }
      Chip { label: panel.str.chipQuick; value: panel.config.quick_provider || "";  tint: panel.voiceColor }
      Chip { label: panel.str.chipDeep;  value: panel.config.deep_model || "";      tint: panel.keysColor }
      // machine access mode: "full" is the only one without a consent step, so it stands out
      Chip { label: panel.str.chipAccess; value: panel.config.system_access || "";
             tint: panel.config.system_access === "full" ? Color.urgent : panel.fg }
    }

    // ---- Voice conversation guide.
    SectionCard {
      width: parent.width
      title: panel.str.voiceTitle
      tint: panel.voiceColor
      meta: "“hey jarvis”"
      metaColor: panel.voiceColor

      Text {
        width: parent.width
        text: panel.str.intro
        wrapMode: Text.WordWrap
        color: panel.fg
        opacity: 0.8
        font.family: panel.fontFamily
        font.pixelSize: Style.font.bodySmall
        lineHeight: 1.2
        bottomPadding: Style.space(4)
      }
      Repeater {
        model: panel.str.rows
        VoiceRow { width: parent.width; phrase: modelData[0]; action: modelData[1] }
      }
    }

    // ---- Dictation + keybindings, side by side.
    Row {
      width: parent.width
      spacing: panel.colGap

      SectionCard {
        id: dictCard
        width: panel.colW
        height: Math.max(dictCard.implicitHeight, keysCard.implicitHeight)
        title: panel.str.dictTitle
        tint: panel.dictColor
        meta: panel.dictating ? "● " + panel.str.dictRecording : panel.isOn ? panel.str.dictReady : panel.str.dictNeedsOn
        metaColor: panel.dictating ? Color.urgent : panel.isOn ? panel.dictColor : Qt.darker(panel.fg, 1.5)

        Text {
          width: parent.width
          text: panel.str.dictIntro
          wrapMode: Text.WordWrap
          color: panel.fg
          opacity: 0.8
          font.family: panel.fontFamily
          font.pixelSize: Style.font.bodySmall
          lineHeight: 1.2
          bottomPadding: Style.space(4)
        }
        KeyRow { width: parent.width; key: "Ctrl+Shift+K"; action: panel.str.dictK; tint: panel.dictColor }
        KeyRow { width: parent.width; key: "Ctrl+Shift+L"; action: panel.str.dictL; tint: panel.dictColor }
        KeyRow { width: parent.width; key: panel.str.dictOther; action: panel.str.dictOtherAction; tint: Qt.darker(panel.fg, 1.3) }

        Item { width: 1; height: Style.space(4) }

        Button {
          text: panel.dictating ? panel.str.dictStop : panel.str.dictStart
          iconText: panel.dictating ? "󰓛" : "󰍬"
          bordered: true
          selected: panel.dictating
          enabled: panel.isOn
          opacity: enabled ? 1.0 : 0.45
          foreground: panel.dictating ? Color.urgent : panel.dictColor
          accent: panel.dictating ? Color.urgent : panel.dictColor
          fontFamily: panel.fontFamily
          fontSize: Style.font.bodySmall
          onClicked: if (panel.isOn) panel.run("jarvis dictate toggle", false)
        }
      }

      SectionCard {
        id: keysCard
        width: panel.colW
        height: Math.max(dictCard.implicitHeight, keysCard.implicitHeight)
        title: panel.str.keysTitle
        tint: panel.keysColor

        KeyRow { width: parent.width; key: "Ctrl+Shift+H"; action: panel.str.keyH }
        KeyRow { width: parent.width; key: "Ctrl+Shift+J"; action: panel.str.keyJ }
        KeyRow { width: parent.width; key: "Ctrl+Shift+K"; action: panel.str.keyDictToggle; tint: panel.dictColor }
        KeyRow { width: parent.width; key: "Ctrl+Shift+L"; action: panel.str.keyDictPtt; tint: panel.dictColor }
        KeyRow { width: parent.width; key: panel.str.keyClick; action: panel.str.keyClickAction; tint: Qt.darker(panel.fg, 1.3) }
        KeyRow { width: parent.width; key: panel.str.keyRight; action: panel.str.keyRightAction; tint: Qt.darker(panel.fg, 1.3) }
        KeyRow { width: parent.width; key: "jarvis wake"; action: panel.str.keyWake; tint: Qt.darker(panel.fg, 1.3) }
        KeyRow { width: parent.width; key: "jarvis config"; action: panel.str.keyConfig; tint: Qt.darker(panel.fg, 1.3) }
      }
    }

    PanelSeparator { foreground: panel.fg }

    // ---- Footer: icon actions centered, hint below.
    Column {
      visible: panel.installed
      width: parent.width
      spacing: Style.space(8)

      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Style.space(10)

        FooterAction {
          iconText: "󰐥"
          hoverColor: panel.isOn ? Color.urgent : Color.accent
          tooltipText: panel.isOn ? panel.str.tipOff : panel.str.tipOn
          onClicked: panel.run("jarvis toggle-notify", false)
        }
        FooterAction {
          iconText: "󰏤"
          visible: panel.isOn
          hoverColor: panel.pausedColor
          tooltipText: panel.str.tipPause
          onClicked: panel.run("jarvis pause 30m", true)
        }
        FooterAction {
          iconText: panel.dictating ? "󰓛" : "󰍬"
          enabled: panel.isOn
          opacity: enabled ? 1.0 : 0.4
          hoverColor: panel.dictating ? Color.urgent : panel.dictColor
          tooltipText: panel.dictating ? panel.str.tipDictateStop : panel.str.tipDictate
          onClicked: if (panel.isOn) panel.run("jarvis dictate toggle", false)
        }
        FooterAction {
          iconText: "󰈙"
          tooltipText: panel.str.tipLogs
          onClicked: panel.run("xdg-terminal-exec jarvis log", true)
        }
        FooterAction {
          iconText: "󰒓"
          hoverColor: panel.keysColor
          tooltipText: panel.str.tipConfig
          onClicked: panel.run("jarvis config", true)
        }
      }

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: panel.str.footHint
        color: Qt.darker(panel.fg, 1.7)
        font.family: panel.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }
}
