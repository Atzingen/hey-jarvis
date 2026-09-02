import QtQuick
import Quickshell.Io

// Estado do Jarvis pro painel (compartilhado entre o widget da bar e a janela
// standalone rodando em quickshell): estado do serviço via `jarvis status`,
// marcador de ditado em curso, e as chaves de config mostradas como chips.
Item {
  id: poller

  property string serviceState: "off"   // "on" | "off" | "paused"
  property string detailText: ""
  property bool dictating: false
  property bool installed: true
  property string lang: "en"
  property var config: ({})

  function probeNow() { if (!probeProc.running) probeProc.running = true }

  function applyStatus(raw) {
    var lines = String(raw).trim().split("\n")
    poller.dictating = lines.indexOf("DICTATING") >= 0
    var data
    try { data = JSON.parse(lines[0]) } catch (e) { return }
    serviceState = data.alt === "on" ? "on" : data.alt === "manual" ? "manual" : data.alt === "paused" ? "paused" : "off"
    // 2nd tooltip line of the CLI carries the useful detail ("desde <ts>", "volta em Xmin").
    var tip = String(data.tooltip || "").split("\\n")
    if (tip.length < 2) tip = String(data.tooltip || "").split("\n")
    var detail = tip.length > 1 ? tip.slice(1).join(" · ")
      : (tip[0] || "").indexOf("(") >= 0 ? tip[0].replace(/^[^(]*/, "") : ""
    // systemd timestamps ("Sun 2026-08-30 15:55:40 -03") → just the time.
    detailText = detail.replace(/\w{3} \d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2}(?: [-+]\d{2,4}| \w+)?/, "$1")
  }

  // `jarvis` on PATH == voice service installed. Also reads the config keys
  // shown as chips (language first — it drives every text of the panel).
  Process {
    id: probeProc
    command: ["bash", "-c",
      "command -v jarvis >/dev/null || { echo missing; exit 0; }; echo installed; " +
      "for k in language stt_provider quick_provider deep_model system_access; do printf '%s=%s\\n' \"$k\" \"$(jarvis config get \"$k\" 2>/dev/null)\"; done"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text).trim().split("\n")
        poller.installed = lines[0] === "installed"
        if (!poller.installed) return
        var cfg = {}
        for (var i = 1; i < lines.length; i++) {
          var eq = lines[i].indexOf("=")
          if (eq > 0) cfg[lines[i].substring(0, eq)] = lines[i].substring(eq + 1).trim()
        }
        poller.config = cfg
        if (cfg.language) poller.lang = cfg.language
      }
    }
  }

  // Service state (JSON from the CLI) + whether a dictation is being recorded
  // (the runtime marker jarvis_dictate.py keeps while the mic is open).
  Process {
    id: statusProc
    command: ["bash", "-c", "jarvis status 2>/dev/null; test -f \"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/jarvis-dictating\" && echo DICTATING"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: poller.applyStatus(text) }
  }

  Timer {
    interval: 2000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: if (poller.installed && !statusProc.running) statusProc.running = true
  }

  // Config rarely changes: probe on start, then every 30 s (and on demand).
  Timer {
    interval: 30000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: poller.probeNow()
  }
}
