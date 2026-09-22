import QtQuick

// Nível da voz do Jarvis (0..1) a partir do envelope que tts() publica em
// $XDG_RUNTIME_DIR/jarvis-tts.json ({t0, step, env[0..10]}), alinhado ao
// relógio de parede — o mesmo arquivo que move a boca do Picoh. Sem envelope
// (ou fora dele) o nível cai a zero com um decaimento curto, pra não cortar seco.
Item {
  id: meter

  property var tts: null          // JSON do jarvis-tts.json ou null
  property real level: 0.0        // 0..1, suavizado
  property int rate: 40           // ms entre amostras

  readonly property bool speaking: tts !== null && rawAt(Date.now() / 1000) >= 0

  function rawAt(now) {
    if (!tts || !tts.env || !tts.env.length) return -1
    var step = Number(tts.step) || 0.05
    var idx = Math.floor((now - Number(tts.t0 || 0)) / step)
    if (idx < 0 || idx >= tts.env.length) return -1
    return Math.max(0, Math.min(10, Number(tts.env[idx]) || 0)) / 10
  }

  Timer {
    interval: meter.rate
    running: true
    repeat: true
    onTriggered: {
      var raw = meter.rawAt(Date.now() / 1000)
      var target = raw < 0 ? 0.0 : raw
      // ataque rápido, decaimento mais lento — a boca/anel acompanha as sílabas sem tremer
      meter.level = target > meter.level ? meter.level + (target - meter.level) * 0.6
                                         : meter.level + (target - meter.level) * 0.25
      if (meter.level < 0.005) meter.level = 0.0
    }
  }
}
