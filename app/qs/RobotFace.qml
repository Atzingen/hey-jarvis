import QtQuick

// Rosto do Jarvis desenhado em QML (sem imagem): cabeça, visor, olhos que
// piscam e vagueiam enquanto o modelo trabalha, boca que abre com a voz.
// `style` = "square" (cabeça quadrada arredondada) ou "round" (cabeça redonda,
// sorriso quando calado). Cores seguem `accent`/`urgent` do tema.
Item {
  id: face

  property string style: "square"
  property color accent: "#8f96ee"
  property color urgent: "#e06065"
  property color shell: "#2c313d"
  property color visor: "#141821"
  property real level: 0.0      // 0..1 abre a boca
  property string phase: "listening"

  readonly property bool thinking: phase === "thinking" || phase === "transcribing" || phase === "polishing" || phase === "handoff"
  readonly property bool alarmed: phase === "consent"
  readonly property color eyeColor: alarmed ? urgent : accent
  readonly property bool round: style === "round"
  readonly property real s: Math.min(width, height)

  // olhos: piscada ocasional e olhar que vagueia quando pensa
  property real blink: 1.0
  property real lookX: 0
  property real lookY: 0

  Timer {
    interval: 2600 + Math.random() * 3200
    running: face.visible
    repeat: true
    onTriggered: { face.blink = 0.08; unblink.restart(); interval = 2600 + Math.random() * 3200 }
  }
  Timer { id: unblink; interval: 110; onTriggered: face.blink = 1.0 }
  Timer {
    interval: 700
    running: face.visible && face.thinking
    repeat: true
    onTriggered: { face.lookX = (Math.random() - 0.5) * face.s * 0.06; face.lookY = -Math.random() * face.s * 0.05 }
  }
  onThinkingChanged: if (!thinking) { lookX = 0; lookY = 0 }
  Behavior on blink { NumberAnimation { duration: 70 } }
  Behavior on lookX { NumberAnimation { duration: 260; easing.type: Easing.InOutQuad } }
  Behavior on lookY { NumberAnimation { duration: 260; easing.type: Easing.InOutQuad } }

  // antena
  Rectangle {
    width: face.s * 0.035; height: face.s * 0.12; radius: width / 2
    color: Qt.lighter(face.shell, 1.5)
    x: face.round ? face.s * 0.56 : (face.s - width) / 2
    y: face.s * 0.08
    rotation: face.round ? -18 : 0
  }
  Rectangle {
    id: antennaGlow
    width: face.s * 0.12; height: width; radius: width / 2
    color: face.eyeColor; opacity: face.thinking ? 0.5 + 0.4 * Math.abs(Math.sin(Date.now() / 300)) : 0.35
    x: (face.round ? face.s * 0.62 : face.s / 2) - width / 2
    y: face.s * 0.03
    SequentialAnimation on opacity {
      running: face.thinking; loops: Animation.Infinite
      NumberAnimation { to: 0.9; duration: 350 } NumberAnimation { to: 0.3; duration: 350 }
    }
  }
  Rectangle {
    width: face.s * 0.05; height: width; radius: width / 2
    color: face.eyeColor
    anchors.centerIn: antennaGlow
  }

  // orelhas
  Repeater {
    model: 2
    Rectangle {
      required property int index
      width: face.s * 0.075; height: face.s * 0.2; radius: width / 2
      color: face.shell
      border.width: 2; border.color: Qt.rgba(face.accent.r, face.accent.g, face.accent.b, 0.55)
      x: index === 0 ? face.s * 0.11 : face.s * 0.89 - width
      y: face.s * 0.46
    }
  }

  // cabeça
  Rectangle {
    id: head
    width: face.s * 0.64; height: face.round ? width : face.s * 0.58
    radius: face.round ? width / 2 : width * 0.18
    x: (face.s - width) / 2; y: face.round ? face.s * 0.22 : face.s * 0.24
    gradient: Gradient {
      GradientStop { position: 0.0; color: Qt.lighter(face.shell, 1.35) }
      GradientStop { position: 1.0; color: face.shell }
    }
    border.width: 2.5; border.color: face.accent

    // visor
    Rectangle {
      id: visorRect
      width: head.width * 0.78; height: face.round ? head.height * 0.6 : head.height * 0.42
      radius: face.round ? height / 2 : height * 0.36
      x: (head.width - width) / 2; y: face.round ? head.height * 0.17 : head.height * 0.2
      color: face.visor
      border.width: 1; border.color: Qt.rgba(face.accent.r, face.accent.g, face.accent.b, 0.3)

      // olhos
      Repeater {
        model: 2
        Item {
          required property int index
          readonly property real ex: (index === 0 ? visorRect.width * 0.3 : visorRect.width * 0.7) + face.lookX
          readonly property real ey: visorRect.height * 0.5 + face.lookY
          x: ex; y: ey
          Rectangle {   // halo
            width: visorRect.height * (face.round ? 0.62 : 0.72); height: face.round ? width * 1.2 : width
            radius: width / 2; anchors.centerIn: parent
            color: face.eyeColor; opacity: 0.18
          }
          Rectangle {   // núcleo
            width: visorRect.height * (face.round ? 0.32 : 0.4); height: (face.round ? width * 1.35 : width) * face.blink
            radius: width / 2; anchors.centerIn: parent
            color: face.eyeColor
            Rectangle {   // brilho
              width: parent.width * 0.32; height: width; radius: width / 2
              x: parent.width * 0.55; y: parent.height * 0.16
              color: "white"; opacity: 0.85 * face.blink
            }
          }
        }
      }
    }

    // boca: pílula que abre com a voz (no estilo redondo vira sorriso quando calado)
    Item {
      id: mouth
      width: head.width * 0.36
      height: face.round ? head.height * 0.14 : head.height * 0.16
      x: (head.width - width) / 2
      y: face.round ? head.height * 0.72 : visorRect.y + visorRect.height + head.height * 0.09
      Canvas {
        anchors.fill: parent
        property real open: face.level
        onOpenChanged: requestPaint()
        onPaint: {
          var ctx = getContext("2d"); ctx.reset()
          var w = width, h = height, c = face.eyeColor
          var o = Math.max(0, Math.min(1, open))
          ctx.lineCap = "round"
          if (face.round && o < 0.08) {   // sorriso
            ctx.beginPath(); ctx.moveTo(w * 0.12, h * 0.35)
            ctx.quadraticCurveTo(w * 0.5, h * 0.95, w * 0.88, h * 0.35)
            ctx.strokeStyle = c; ctx.lineWidth = Math.max(2, h * 0.18); ctx.stroke()
            return
          }
          var mh = Math.max(h * 0.16, h * (0.16 + 0.84 * o))
          var mw = w * (1 - 0.18 * o)
          var x0 = (w - mw) / 2, y0 = (h - mh) / 2, r = Math.min(mh, mw) / 2
          ctx.beginPath()
          ctx.moveTo(x0 + r, y0); ctx.lineTo(x0 + mw - r, y0)
          ctx.arc(x0 + mw - r, y0 + r, r, -Math.PI / 2, 0); ctx.lineTo(x0 + mw, y0 + mh - r)
          ctx.arc(x0 + mw - r, y0 + mh - r, r, 0, Math.PI / 2); ctx.lineTo(x0 + r, y0 + mh)
          ctx.arc(x0 + r, y0 + mh - r, r, Math.PI / 2, Math.PI); ctx.lineTo(x0, y0 + r)
          ctx.arc(x0 + r, y0 + r, r, Math.PI, Math.PI * 1.5); ctx.closePath()
          ctx.fillStyle = face.visor; ctx.fill()
          ctx.strokeStyle = Qt.rgba(c.r, c.g, c.b, 0.7); ctx.lineWidth = 1.5; ctx.stroke()
          if (o > 0.08) {   // "voz" dentro da boca
            ctx.fillStyle = Qt.rgba(c.r, c.g, c.b, 0.35 + 0.5 * o)
            ctx.fillRect(x0 + mw * 0.2, y0 + mh * 0.3, mw * 0.6, mh * 0.4)
          } else {
            ctx.fillStyle = Qt.rgba(c.r, c.g, c.b, 0.8)
            ctx.fillRect(x0 + mw * 0.15, y0 + mh * 0.36, mw * 0.7, mh * 0.28)
          }
        }
      }
    }
  }

  // pescoço
  Rectangle {
    width: face.s * 0.16; height: face.s * 0.08; radius: face.s * 0.02
    color: face.shell
    border.width: 2; border.color: Qt.rgba(face.accent.r, face.accent.g, face.accent.b, 0.4)
    x: (face.s - width) / 2; y: head.y + head.height - 2
  }
}
