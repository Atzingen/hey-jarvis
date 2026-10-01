import QtQuick
import QtTest
import "../app/qs"

TestCase {
  name: "RoutingSession"
  when: windowShown
  visible: true
  width: 900
  height: 600
  ConversationContent { id: content; anchors.fill: parent }
  SignalSpy { id: opened; target: content; signalName: "openSessionRequested" }
  function init() { opened.clear() }

  function test_native_button_opens_exact_session() {
    content.state = { mode: "conversation", phase: "thinking", can_attach: true,
                      session_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", session_backend: "native" }
    var button = findChild(content, "openSessionButton")
    verify(button.visible)
    wait(50)
    mouseClick(button)
    compare(opened.count, 1)
    compare(opened.signalArguments[0][0], content.state.session_id)
  }
  function test_no_terminal_on_api_or_legacy() {
    for (var backend of ["api", "legacy"]) {
      content.state = { mode: "conversation", session_backend: backend, can_attach: false }
      verify(!findChild(content, "openSessionButton").visible)
    }
  }
  function test_dictation_never_opens_agent() {
    content.state = { mode: "dictation", phase: "polishing", can_attach: true, session_id: "a" }
    verify(!findChild(content, "openSessionButton").visible)
  }
  function test_routing_is_visible_per_response_and_in_sidebar() {
    content.state = {
      mode: "conversation", phase: "speaking", detail: "codex gpt-6-astra/medium/fast",
      routing_summary: "Jev → computador → Agente · assinatura",
      exchanges: [
        {q: "Qual a previsão?", a: "Previsão encontrada.", label: "openai gpt-6-luna",
         routing_summary: "Jev → busca web → API"},
        {q: "Leia meu arquivo.", a: "Arquivo lido.", label: "codex gpt-6-astra/medium/fast",
         routing_summary: "Jev → computador → Agente · assinatura"}
      ]
    }
    wait(50)
    var first = findChild(content, "assistantBubble0")
    verify(first !== null)
    compare(first.meta, "openai gpt-6-luna")
    var trace = findChild(first, "messageRouting")
    verify(trace.visible)
    compare(trace.text, "Jev → busca web → API")
    compare(findChild(content, "currentRouting").text, content.state.routing_summary)
    compare(findChild(content, "assistantBubble1").meta, "codex gpt-6-astra/medium/fast")
  }
  function test_fallback_wraps_inside_bubble() {
    content.state = {
      mode: "conversation", phase: "speaking",
      exchanges: [{q: "Olá", a: "Olá.", label: "codex gpt-6-astra/medium/fast",
                   routing_summary: "Jev → busca web → API → falha (HTTP 429) → agente · assinatura"}]
    }
    wait(50)
    var bubble = findChild(content, "assistantBubble0")
    verify(bubble !== null)
    var trace = findChild(bubble, "messageRouting")
    verify(trace.visible)
    verify(trace.width <= bubble.width)
    verify(trace.y + trace.height <= bubble.height)
    verify(trace.lineCount > 1)
  }
}
