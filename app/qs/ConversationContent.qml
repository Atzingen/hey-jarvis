import QtQuick
import qs.Commons

// Conteúdo da janela gráfica da conversa — o MESMO QML nos dois hosts:
//   - quickshell (conversation.qml, janela em layer-shell no Omarchy);
//   - PySide6 (conversation-main.qml, janela comum em qualquer Linux).
// Recebe o estado por propriedades (o JSON de jarvis-state.json e o envelope de
// jarvis-tts.json) e só pede uma coisa de volta: encerrar (quitRequested).
// À esquerda o avatar do Jarvis com o anel que pulsa com a voz; à direita a
// conversa em balões (ou, no ditado, a transcrição ao vivo e o waveform).
Item {
  id: panel

  property var state: null            // jarvis-state.json
  property var tts: null              // jarvis-tts.json (envelope da fala) ou null
  property string themeRaw: ""        // colors.toml do tema do Omarchy (opcional)
  property string fontFamily: Style.font.family
  property string robotStyle: "square"

  signal quitRequested()

  // --- paleta: tema do Omarchy quando disponível, senão a fixa do shim ------
  property color background: Color.popups.background
  property color foreground: Color.popups.text
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color green: "#7fd18a"
  property color amber: "#d9a441"
  property color blue: Color.accent
  property color muted: Qt.darker(Color.popups.text, 1.6)
  property color raised: Qt.lighter(Color.popups.background, 1.35)

  onThemeRawChanged: applyTheme(themeRaw)
  function applyTheme(raw) {
    var found = {}
    var lines = String(raw || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
      if (m) found[m[1]] = m[2]
    }
    if (found.background) background = found.background
    if (found.foreground) foreground = found.foreground
    if (found.accent) accent = found.accent; else if (found.blue) accent = found.blue
    if (found.red) urgent = found.red
    if (found.green) green = found.green
    if (found.yellow) amber = found.yellow
    if (found.blue) blue = found.blue
    if (found.muted) muted = found.muted; else muted = Qt.darker(foreground, 1.6)
    raised = found.lighter_background ? found.lighter_background : Qt.lighter(background, 1.35)
  }

  // --- estado derivado --------------------------------------------------------
  readonly property var st: state || ({})
  readonly property string phase: String(st.phase || "listening")
  readonly property string mode: String(st.mode || "conversation")
  readonly property bool dictation: mode === "dictation"
  readonly property var i18n: st.i18n || ({})
  readonly property var phases: i18n.phases || ({})
  readonly property var ph: phases[phase] || [phase.toUpperCase(), ""]
  readonly property string phaseName: String(ph[0] || "")
  readonly property string hint: String(ph[1] || "")
  readonly property string youLabel: String(i18n.you || "you")
  readonly property var exchanges: st.exchanges || []
  readonly property string partial: String(st.partial || "")
  readonly property var thoughts: st.thoughts || []
  readonly property var levels: st.levels || []
  readonly property string detail: String(st.detail || "")

  readonly property color phaseColor:
    phase === "consent" ? urgent
    : (phase === "recording" || phase === "dictating" || phase === "pasted" || phase === "copied") ? green
    : (phase === "thinking" || phase === "transcribing" || phase === "polishing" || phase === "handoff") ? amber
    : (phase === "speaking") ? blue
    : (phase === "cancelled") ? muted
    : accent

  readonly property string ringMode:
    phase === "speaking" ? "speak"
    : (phase === "recording" || phase === "dictating") ? "mic"
    : (phase === "thinking" || phase === "transcribing" || phase === "polishing" || phase === "handoff") ? "spin"
    : phase === "consent" ? "alert"
    : (phase === "listening" || phase === "followup") ? "breathe"
    : "still"

  // nível do microfone (ditado/gravação): última amostra com ganho automático
  readonly property real micLevel: {
    if (!levels.length) return 0
    var recent = levels.slice(-40)
    var peak = 0.35
    for (var i = 0; i < recent.length; i++) peak = Math.max(peak, Number(recent[i]) || 0)
    var last = Number(levels[levels.length - 1]) || 0
    return last < 0.04 ? 0 : Math.min(1, last / peak)
  }
  readonly property real ringLevel: ringMode === "speak" ? meter.level : ringMode === "mic" ? micLevel : 0

  // countdown da fase (deadline em epoch s)
  property int secondsLeft: -1
  Timer {
    interval: 250; running: true; repeat: true; triggeredOnStart: true
    onTriggered: panel.secondsLeft = panel.st.deadline ? Math.max(0, Math.round(Number(panel.st.deadline) - Date.now() / 1000)) : -1
  }

  SpeechMeter { id: meter; tts: panel.tts }

  focus: true
  Keys.onPressed: function(ev) {
    if (ev.key === Qt.Key_Q || ev.key === Qt.Key_Escape) { panel.quitRequested(); ev.accepted = true }
  }

  readonly property int sidebarW: Style.space(250)
  readonly property int pad: Style.space(18)
  implicitWidth: Style.space(900)
  implicitHeight: Style.space(520)

  // --- coluna do avatar ------------------------------------------------------
  Item {
    id: sidebar
    x: 0; y: 0
    width: panel.sidebarW; height: parent.height

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(panel.foreground.r, panel.foreground.g, panel.foreground.b, 0.03)
    }
    Rectangle {
      anchors.right: parent.right; width: 1; height: parent.height
      color: Qt.rgba(panel.foreground.r, panel.foreground.g, panel.foreground.b, 0.08)
    }

    Item {
      id: avatar
      width: panel.sidebarW - panel.pad; height: width
      anchors.horizontalCenter: parent.horizontalCenter
      y: Style.space(34)

      VoiceRing {
        anchors.fill: parent
        level: panel.ringLevel
        mode: panel.ringMode
        color: panel.phaseColor
      }
      RobotFace {
        width: parent.width * 0.56; height: width
        anchors.centerIn: parent
        style: panel.robotStyle
        accent: panel.accent
        urgent: panel.urgent
        shell: panel.raised
        visor: Qt.darker(panel.background, 1.25)
        level: panel.ringMode === "speak" ? meter.level : 0
        phase: panel.phase
      }
    }

    // fase + countdown
    Column {
      anchors.horizontalCenter: parent.horizontalCenter
      y: avatar.y + avatar.height + Style.space(18)
      spacing: Style.space(8)

      Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        width: badgeRow.implicitWidth + Style.space(22); height: badgeRow.implicitHeight + Style.space(10)
        radius: height / 2
        color: Qt.rgba(panel.phaseColor.r, panel.phaseColor.g, panel.phaseColor.b, 0.14)
        border.width: 1; border.color: Qt.rgba(panel.phaseColor.r, panel.phaseColor.g, panel.phaseColor.b, 0.6)
        Row {
          id: badgeRow
          anchors.centerIn: parent
          spacing: Style.space(8)
          Rectangle {
            width: Style.space(8); height: width; radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            color: panel.phaseColor
            SequentialAnimation on opacity {
              running: panel.ringMode === "mic" || panel.ringMode === "alert"; loops: Animation.Infinite
              NumberAnimation { to: 0.25; duration: 450 } NumberAnimation { to: 1.0; duration: 450 }
            }
          }
          Text {
            text: panel.phaseName + (panel.secondsLeft >= 0 ? "  " + panel.secondsLeft + "s" : "")
            textFormat: Text.PlainText
            color: panel.phaseColor
            font.family: panel.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1
          }
        }
      }
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        width: panel.sidebarW - panel.pad * 2
        text: panel.detail
        visible: panel.detail !== ""
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        color: panel.muted
        font.family: panel.fontFamily; font.pixelSize: Style.font.caption
      }
    }
  }

  // --- coluna da conversa ----------------------------------------------------
  Item {
    id: main
    x: panel.sidebarW; y: 0
    width: parent.width - panel.sidebarW; height: parent.height

    // cabeçalho
    Item {
      id: header
      x: panel.pad; y: Style.space(14)
      width: parent.width - panel.pad * 2; height: Style.space(28)
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: "Jarvis"
        color: panel.foreground
        font.family: panel.fontFamily; font.pixelSize: Style.font.title; font.bold: true
      }
      Text {   // fechar
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        text: "✕"
        color: closeHover.hovered ? panel.urgent : panel.muted
        font.family: panel.fontFamily; font.pixelSize: Style.font.body
        HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: panel.quitRequested() }
      }
    }
    Rectangle {
      x: panel.pad; y: header.y + header.height + Style.space(6)
      width: parent.width - panel.pad * 2; height: 1
      color: Qt.rgba(panel.foreground.r, panel.foreground.g, panel.foreground.b, 0.1)
    }

    // rodapé: atividade do modelo + dica
    Column {
      id: footer
      x: panel.pad
      width: parent.width - panel.pad * 2
      anchors.bottom: parent.bottom; anchors.bottomMargin: Style.space(14)
      spacing: Style.space(6)

      Column {
        width: parent.width
        spacing: Style.space(2)
        visible: !panel.dictation && (panel.phase === "thinking" || panel.phase === "handoff") && panel.thoughts.length > 0
        Repeater {
          model: panel.thoughts.slice(-3)
          Text {
            required property var modelData
            width: parent.width
            text: "⋯ " + String(modelData)
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: panel.muted
            font.family: panel.fontFamily; font.pixelSize: Style.font.bodySmall
          }
        }
      }
      Rectangle { width: parent.width; height: 1; color: Qt.rgba(panel.foreground.r, panel.foreground.g, panel.foreground.b, 0.1) }
      Text {
        width: parent.width
        text: panel.hint
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: panel.muted
        font.family: panel.fontFamily; font.pixelSize: Style.font.caption
      }
    }

    // ---- conversa em balões
    Flickable {
      id: chat
      visible: !panel.dictation
      x: panel.pad; y: header.y + header.height + Style.space(16)
      width: parent.width - panel.pad * 2
      height: footer.y - y - Style.space(10)
      contentWidth: width
      contentHeight: chatCol.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      onContentHeightChanged: contentY = Math.max(0, contentHeight - height)

      Column {
        id: chatCol
        width: chat.width
        spacing: Style.space(12)

        Text {
          visible: panel.exchanges.length === 0 && panel.partial === ""
          text: String(panel.i18n.empty || "")
          textFormat: Text.PlainText
          color: panel.muted
          font.family: panel.fontFamily; font.pixelSize: Style.font.bodySmall
        }

        Repeater {
          model: panel.exchanges
          Column {
            required property var modelData
            width: chatCol.width
            spacing: Style.space(8)
            Bubble { width: parent.width; mine: true; who: panel.youLabel; body: String(modelData.q || "") }
            Bubble { width: parent.width; mine: false; who: "Jarvis"; meta: String(modelData.label || ""); body: String(modelData.a || "") }
          }
        }
        Bubble {
          visible: (panel.phase === "recording" || panel.phase === "transcribing") && panel.partial !== ""
          width: chatCol.width; mine: true; who: panel.youLabel; body: panel.partial + " …"; ghost: true
        }
      }
    }

    // ---- ditado: transcrição grande + waveform
    Item {
      visible: panel.dictation
      x: panel.pad; y: header.y + header.height + Style.space(16)
      width: parent.width - panel.pad * 2
      height: footer.y - y - Style.space(10)

      Flickable {
        id: dictScroll
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: wave.top; anchors.bottomMargin: Style.space(10)
        contentWidth: width; contentHeight: dictText.implicitHeight
        clip: true
        onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
        Text {
          id: dictText
          width: dictScroll.width
          text: {
            var t = String(panel.st.partial || panel.st.final || "")
            if (t !== "") return t + (panel.phase === "dictating" ? " …" : "")
            return panel.phase === "dictating" ? "…" : String(panel.i18n.dict_empty || "")
          }
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          color: String(panel.st.partial || panel.st.final || "") !== "" ? panel.foreground : panel.muted
          font.family: panel.fontFamily; font.pixelSize: Style.font.subtitle
          lineHeight: 1.25
        }
      }

      Canvas {
        id: wave
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: Style.space(96)
        property var samples: panel.levels
        property color tint: panel.phase === "dictating" ? panel.green : panel.muted
        onSamplesChanged: requestPaint()
        onTintChanged: requestPaint()
        onWidthChanged: requestPaint()
        onPaint: {
          var ctx = getContext("2d"); ctx.reset()
          var n = 120, w = width, h = height, cy = h / 2
          var bw = w / n
          var s = samples || []
          var recent = s.slice(-n)
          var peak = 0.35
          for (var i = 0; i < recent.length; i++) peak = Math.max(peak, Number(recent[i]) || 0)
          ctx.strokeStyle = Qt.rgba(tint.r, tint.g, tint.b, 0.18); ctx.lineWidth = 1
          ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke()
          ctx.fillStyle = tint
          var offset = n - recent.length
          for (var j = 0; j < recent.length; j++) {
            var l = Number(recent[j]) || 0
            l = l < 0.04 ? 0 : Math.min(1, l / peak)
            var half = Math.max(1, l * (h / 2 - 2))
            ctx.fillRect((offset + j) * bw + 1, cy - half, Math.max(1, bw - 2), half * 2)
          }
        }
      }
    }
  }

  component Bubble: Item {
    id: bubble
    property bool mine: false
    property bool ghost: false
    property string who: ""
    property string meta: ""
    property string body: ""
    readonly property color tint: mine ? panel.accent : panel.foreground
    implicitHeight: card.height

    Rectangle {
      id: card
      width: Math.min(bubble.width * 0.88, bodyText.implicitWidth + Style.space(28))
      height: labelRow.implicitHeight + bodyText.implicitHeight + Style.space(24)
      x: bubble.mine ? bubble.width - width : 0
      radius: Style.cornerRadius + 4
      color: Qt.rgba(bubble.tint.r, bubble.tint.g, bubble.tint.b, bubble.mine ? 0.13 : 0.05)
      border.width: 1
      border.color: Qt.rgba(bubble.tint.r, bubble.tint.g, bubble.tint.b, bubble.mine ? 0.35 : 0.12)
      opacity: bubble.ghost ? 0.6 : 1

      Row {
        id: labelRow
        x: Style.space(14); y: Style.space(10)
        spacing: Style.space(8)
        Text {
          text: bubble.who
          textFormat: Text.PlainText
          color: bubble.mine ? panel.accent : panel.amber
          font.family: panel.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1
        }
        Text {
          visible: bubble.meta !== ""
          text: bubble.meta
          textFormat: Text.PlainText
          color: panel.muted
          font.family: panel.fontFamily; font.pixelSize: Style.font.caption
        }
      }
      Text {
        id: bodyText
        x: Style.space(14); y: labelRow.y + labelRow.implicitHeight + Style.space(4)
        width: card.width - Style.space(28)
        text: bubble.body
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: bubble.mine ? Qt.lighter(panel.accent, 1.15) : panel.foreground
        font.family: panel.fontFamily; font.pixelSize: Style.font.body
        lineHeight: 1.2
        // largura "natural" limitada ao balão: implicitWidth do texto sem quebra
        // não é confiável com wrap, então mede num Text invisível
      }
    }
  }
}
