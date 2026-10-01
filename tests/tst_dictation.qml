import QtQuick
import QtTest
import "../app/qs"

TestCase {
  name: "DictationEscape"
  when: windowShown
  width: 700
  height: 500

  ConversationContent {
    id: content
    anchors.fill: parent
  }
  SignalSpy { id: skip; target: content; signalName: "skipPolishRequested" }
  SignalSpy { id: quit; target: content; signalName: "quitRequested" }

  function init() {
    skip.clear()
    quit.clear()
    content.forceActiveFocus()
  }

  function test_escape_skips_polish() {
    content.state = { mode: "dictation", phase: "polishing", final: "texto bruto" }
    keyClick(Qt.Key_Escape)
    compare(skip.count, 1)
    compare(quit.count, 0)
  }

  function test_live_preview_and_delivery_notice() {
    content.state = { mode: "dictation", phase: "dictating", partial: "texto ao vivo",
                      detail: "nemotron 3.5 0.6b/cpu", dictation_notice: "texto confirmado nas pausas" }
    compare(content.partial, "texto ao vivo")
    compare(content.hint, "texto confirmado nas pausas")
    content.state = { mode: "dictation", phase: "dictating", partial: "texto recuperado",
                      detail: "whisper small/cpu", dictation_notice: "foco mudou" }
    compare(content.detail, "whisper small/cpu")
    compare(content.hint, "foco mudou")
  }

  function test_escape_closes_other_phases() {
    content.state = { mode: "dictation", phase: "dictating" }
    keyClick(Qt.Key_Escape)
    compare(skip.count, 0)
    compare(quit.count, 1)
  }

  function test_q_still_closes_while_polishing() {
    content.state = { mode: "dictation", phase: "polishing", final: "texto bruto" }
    keyClick(Qt.Key_Q)
    compare(skip.count, 0)
    compare(quit.count, 1)
  }
}
