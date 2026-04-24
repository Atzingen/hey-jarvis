# hey-jarvis

> Voice-activated dev launcher for Linux + Hyprland. Say **"hey jarvis"**, then tell it what you want.

Always-on wake word detection (~2% CPU). Everything runs locally except the LLM calls for open-ended questions.

---

## What you can say

| Phrase | Action |
|---|---|
| **"abrir `<projeto>`"** *(also `abra`, `abre`)* | Spawns a 2×2 Ghostty grid + VS Code + Chrome layout for `~/Desktop/dev/<projeto>` |
| **"pense bem `<pergunta>`"** | Asks Claude (Opus / high effort), speaks + shows the answer |
| *anything else* | Asks a fast model (Codex `gpt-5.4` / low, ~5–7 s by default) and speaks the answer |
| **"dormir"** / **"durma"** | `systemctl suspend` |

Saying **"hey jarvis"** *again* while it's answering interrupts the TTS and the pending model call.

---

## Pipeline

```
mic 16 kHz int16
  │
  ▼
openWakeWord  (hey_jarvis, ONNX)     ← always-on, ~2% CPU
  │ score > 0.5
  ▼
piper TTS  "No que vamos trabalhar, senhor?"
  │
  ▼
record 4 s
  │
  ▼
faster-whisper  (small / int8 / CPU, pt-BR)
  │
  ▼
route:
  • "dormir"     → systemctl suspend
  • "abrir X"    → dev-layout X
  • "pense bem"  → claude -p --model opus --effort high
  • else         → codex exec -c model_reasoning_effort=low --ephemeral
                   (or claude sonnet/low if QUICK_PROVIDER = "claude")
  │
  ▼
piper TTS + floating Ghostty overlay
```

During the busy phase (TTS + model call), an `InterruptListener` thread keeps reading the mic with a slightly higher wake threshold. Re-triggering `hey_jarvis` cancels the in-flight response cleanly.

---

## Install

```bash
git clone https://github.com/Atzingen/hey-jarvis.git
cd hey-jarvis

# 1. Python environment
conda create -n voice python=3.11 -y
conda activate voice
pip install -r requirements.txt

# 2. Scripts to ~/.local/bin/
install -Dm755 bin/voice-launcher     ~/.local/bin/voice-launcher
install -Dm755 bin/voice-launcher.py  ~/.local/bin/voice-launcher.py
install -Dm755 bin/dev-layout         ~/.local/bin/dev-layout
install -Dm755 bin/jarvis             ~/.local/bin/jarvis

# 3. Systemd user unit
install -Dm644 systemd/voice-launcher.service \
  ~/.config/systemd/user/voice-launcher.service

# 4. Enable + start
systemctl --user daemon-reload
systemctl --user enable --now voice-launcher.service
```

The wrapper `bin/voice-launcher` assumes the conda env is at `~/miniconda3/envs/voice`. If you use venv / uv / pyenv, edit that one line.

---

## Requirements

**System packages** *(Arch names)*

| Group | Packages |
|---|---|
| core | `python` (3.11+), `pipewire`, `pipewire-pulse` |
| compositor | `hyprland` — `dev-layout` uses `hyprctl` |
| layout apps | `ghostty`, `code`, `google-chrome-stable` |
| LLM CLIs | [`claude`](https://docs.claude.com/en/docs/claude-code) (Claude Code) — used for "pense bem" and for the fast path when `QUICK_PROVIDER="claude"` |
| | [`codex`](https://github.com/openai/codex) — OpenAI Codex CLI, authenticated via `codex login` (default fast path) |
| TTS | [`piper`](https://github.com/rhasspy/piper) — `pip install piper-tts` already installs the binary |
| UI | [`gum`](https://github.com/charmbracelet/gum) — renders the floating answer overlay |
| hardware | a working microphone |

**Python packages** — see `requirements.txt`. Tested on Python 3.11 with a conda env named `voice`.

**Piper voice** *(Portuguese default)*

```bash
mkdir -p ~/.local/share/piper-voices
cd ~/.local/share/piper-voices
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json
```

Any other Piper voice works — edit `VOICE` at the top of `bin/voice-launcher.py`.

---

## The `jarvis` CLI

A small control wrapper around the systemd service. Use it manually, from keybinds, or from waybar:

| Command | Effect |
|---|---|
| `jarvis on` | start the service (cancels any pending resume timer) |
| `jarvis off` | stop the service (cancels any pending resume timer) |
| `jarvis toggle` | flip state |
| `jarvis toggle-notify` | toggle + `notify-send` with new state *(used by keybinds and waybar)* |
| `jarvis pause <duration>` | stop now, start again later. Accepts `30s`, `45m`, `1h`, `2h30m`, … |
| `jarvis pause-notify [dur]` | pause + notify. Defaults to 30 min if `dur` omitted *(used by waybar right-click)* |
| `jarvis status` | JSON for waybar: `{text, alt, class, tooltip}`. States: `on` / `off` / `paused` |
| `jarvis status-short` | one of `on` \| `off` (for scripts) |
| `jarvis log` | `journalctl --user -u voice-launcher -f` |

The `paused` state differentiates between a manual pause-timer and an auto-pause (future: meeting watcher) via a marker file in `$XDG_RUNTIME_DIR`.

---

## Hyprland keybinding

Append to `~/.config/hypr/bindings.conf`:

```
bindd = SUPER CTRL, J, Toggle Jarvis (voice launcher), exec, jarvis toggle-notify
```

Then <kbd>Super</kbd> + <kbd>Ctrl</kbd> + <kbd>J</kbd> toggles the service and flashes a notification. See `integrations/hypr-binding.conf`.

---

## Waybar module

Drop the module definition into `~/.config/waybar/config.jsonc` and add `"custom/jarvis"` to `modules-center` (or wherever you like):

```jsonc
"custom/jarvis": {
  "exec": "jarvis status",
  "return-type": "json",
  "interval": 2,
  "format": "{icon}",
  "format-icons": {
    "on":     "󰋋",
    "off":    "󰟎",
    "paused": "󰂛"
  },
  "tooltip": true,
  "on-click":       "jarvis toggle-notify",
  "on-click-right": "jarvis pause-notify 30m"
}
```

Ready-to-copy files are in `integrations/waybar/module.jsonc` and `integrations/waybar/style.css`.

**Controls**

| | Action |
|---|---|
| Hover | tooltip with state + timestamp / ETA |
| Left click | toggle on/off |
| Right click | pause for 30 min *(meeting shortcut)* |

Icons are Material Design Nerd Font glyphs — `U+F02CB` (headphones), `U+F07CE` (headphones-off), `U+F009B` (sleep).

---

## Runtime controls (raw)

```bash
systemctl --user stop    voice-launcher   # mute mic now
systemctl --user start   voice-launcher
systemctl --user restart voice-launcher   # after editing the .py
systemctl --user disable voice-launcher   # stop auto-start on boot
systemctl --user status  voice-launcher
journalctl  --user -u    voice-launcher -f   # live log (wake scores, transcripts, routes)
```

Or any of the `jarvis` commands above.

---

## The `dev-layout` script

Called by the voice launcher for `"abrir X"`, but works standalone too:

```bash
dev-layout iaprev
```

It picks the lowest free workspace in `1..5` and in `6..9`, then spawns:

- **Workspace 1–5** — 2×2 Ghostty grid. Three terminals auto-run `claude --dangerously-skip-permissions`; one is a plain shell. Top row resized by +206 px.
- **Workspace 6–9** — VS Code in the top half, Chrome in the bottom with three tabs (github.com, github.com, claude.ai/new).

All windows `cd` into `~/Desktop/dev/<projeto>`. Tweak the script to taste.

---

## Configuration

Constants at the top of `bin/voice-launcher.py`:

| Constant | Default | What |
|---|---|---|
| `SAMPLE_RATE` | `16000` | mic sample rate |
| `DEV_DIR` | `~/Desktop/dev` | where `match_project` scans |
| `VOICE` | `~/.local/share/piper-voices/pt_BR-faber-medium.onnx` | piper voice |
| `VOICE_LENGTH_SCALE` | `1.15` | >1 = slower, more formal |
| `WAKE_THRESHOLD` | `0.5` | openWakeWord trigger score |
| `RECORD_SECONDS` | `4.0` | how long to record after wake |
| `QUICK_PROVIDER` | `"codex"` | fast path: `"codex"` (gpt-5.4/low, ~5–7 s) or `"claude"` (sonnet/low, ~10 s) |
| `CODEX_TIMEOUT_QUICK` | `45` | seconds |
| `CLAUDE_TIMEOUT_QUICK` / `CLAUDE_TIMEOUT_DEEP` | `45 / 180` | seconds |
| `CLAUDE_SYSTEM` | *(see file)* | system prompt shared by both providers, forces TTS-friendly output |
| `INTERRUPT_THRESHOLD_BOOST` | `0.2` | added to wake threshold during busy phase (reduces TTS-bleed false positives) |
| `OVERLAY_ENABLED` | `True` | show the floating Ghostty overlay on model answers |
| `OVERLAY_AUTOCLOSE_SECONDS` | `20` | auto-close delay |

**Command-line overrides**

```bash
voice-launcher --test                 # dry-run: don't open layouts, don't suspend
voice-launcher --wake-threshold 0.6
voice-launcher --whisper-model medium # tiny | base | small | medium
```

---

## Repository layout

```
hey-jarvis/
├── bin/
│   ├── voice-launcher          wrapper that activates the conda env
│   ├── voice-launcher.py       main loop (wake → STT → route → TTS/action)
│   ├── dev-layout              Hyprland 2×2 grid + VS Code + Chrome
│   └── jarvis                  CLI to control the systemd service
├── systemd/
│   └── voice-launcher.service  user unit
├── integrations/
│   ├── hypr-binding.conf       Super+Ctrl+J toggle
│   └── waybar/
│       ├── module.jsonc        waybar module definition
│       └── style.css           optional colors per state
├── requirements.txt
├── README.md
└── LICENSE                     MIT
```

---

## License

MIT — see `LICENSE`.
