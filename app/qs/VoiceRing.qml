import QtQuick

// Anel de barras radiais ao redor do avatar: cada barra sobe com a voz
// (envelope da fala do Jarvis, ou o microfone no ditado). Nas outras fases o
// anel "respira" (ouvindo), gira (pensando/transcrevendo) ou pulsa (autorização).
Canvas {
  id: ring

  property real level: 0.0        // 0..1
  property string mode: "breathe" // "speak" | "mic" | "breathe" | "spin" | "alert" | "still"
  property color color: "#8f96ee"
  property int bars: 56
  property real innerRadius: width * 0.36
  property real maxLength: width * 0.12
  property real baseLength: 3

  property real t: 0

  Timer {
    interval: 33
    running: ring.visible
    repeat: true
    onTriggered: { ring.t += 0.033; ring.requestPaint() }
  }
  onLevelChanged: requestPaint()
  onModeChanged: requestPaint()
  onColorChanged: requestPaint()

  function lengthAt(i, angle) {
    var n = ring.bars, t = ring.t, L = ring.maxLength
    var shape = 0.55 + 0.45 * Math.sin(i * 1.31 + t * 2.4)          // relevo fixo, varia devagar
    var flutter = 0.12 * Math.sin(i * 7.1 + t * 11.0)                 // tremor fino nas sílabas
    switch (ring.mode) {
      case "speak":
      case "mic":
        return ring.baseLength + L * Math.max(0, ring.level * (shape + flutter * ring.level))
      case "breathe":
        return ring.baseLength + L * (0.10 + 0.10 * (0.5 + 0.5 * Math.sin(t * 1.4)) + 0.04 * Math.sin(i * 0.9 + t * 0.8))
      case "spin": {
        var best = 0
        for (var k = 0; k < 3; k++) {
          var head = t * 2.2 + k * Math.PI * 2 / 3
          var d = Math.abs(((angle - head) % (Math.PI * 2) + Math.PI * 3) % (Math.PI * 2) - Math.PI)
          best = Math.max(best, Math.exp(-(d * d) / 0.12))
        }
        return ring.baseLength + L * (0.08 + 0.7 * best)
      }
      case "alert":
        return ring.baseLength + L * (0.15 + 0.55 * Math.abs(Math.sin(t * 4.0)))
      default:
        return ring.baseLength + L * 0.06
    }
  }

  onPaint: {
    var ctx = getContext("2d")
    ctx.reset()
    var cx = width / 2, cy = height / 2
    var r0 = ring.innerRadius
    var c = ring.color
    // guia fraca do círculo interno
    ctx.beginPath()
    ctx.arc(cx, cy, r0 - 4, 0, Math.PI * 2)
    ctx.strokeStyle = Qt.rgba(c.r, c.g, c.b, 0.14)
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.lineCap = "round"
    ctx.lineWidth = Math.max(2, width / 70)
    for (var i = 0; i < ring.bars; i++) {
      var a = i / ring.bars * Math.PI * 2 - Math.PI / 2
      var len = ring.lengthAt(i, a)
      var alpha = 0.45 + 0.55 * Math.min(1, len / ring.maxLength)
      ctx.strokeStyle = Qt.rgba(c.r, c.g, c.b, alpha)
      ctx.beginPath()
      ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0)
      ctx.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len))
      ctx.stroke()
    }
  }
}
