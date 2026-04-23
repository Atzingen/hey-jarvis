# hey-jarvis

Voice-activated dev launcher for Linux + Hyprland. Say **"hey jarvis"**, then either:

- **"abrir `<projeto>`"** → opens a 2×2 Ghostty grid + VS Code + Chrome layout for a project in `~/Desktop/dev/`
- **"pense bem `<pergunta>`"** → asks Claude (Opus, high effort) and speaks the answer
- anything else → asks Claude (Sonnet, low effort) and speaks the answer
- **"dormir"** / **"durma"** → `systemctl suspend`

Always-on wake word detection (~2% CPU), everything runs locally except the Claude call.

## Pipeline

```
mic 16kHz int16
  ↓
openWakeWord (hey_jarvis, ONNX)         ← always-on, ~2% CPU
  ↓ score > 0.5
piper TTS "No que vamos trabalhar, senhor?"
  ↓
record 4s
  ↓
faster-whisper (small / int8 / CPU, pt-BR)
  ↓
route:
  • "dormir"     → systemctl suspend
  • "abrir X"    → dev-layout X
  • "pense bem"  → claude -p --model opus   --effort high → piper TTS
  • else         → claude -p --model sonnet --effort low  → piper TTS
```

## Requirements

**System packages** (Arch/Omarchy):

- `python` (3.11+), `pipewire` + `pipewire-pulse` (for `paplay`)
- `hyprland`, `hyprctl`
- `ghostty` (terminals), `code` (VS Code), `google-chrome-stable` — used by `dev-layout`
- [`claude` CLI](https://docs.claude.com/en/docs/claude-code) — the Claude Code CLI, authenticated with your account
- [`piper`](https://github.com/rhasspy/piper) TTS binary on `$PATH` (pip's `piper-tts` installs it)
- a working microphone

**Python packages** — see `requirements.txt`. Tested on Python 3.11 with a conda env named `voice`.

**Piper voice** — the Portuguese voice used by default:

```bash
mkdir -p ~/.local/share/piper-voices
cd ~/.local/share/piper-voices
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json
```

Any other Piper voice works — just edit the `VOICE` constant at the top of `bin/voice-launcher.py`.

## Install

```bash
git clone https://github.com/Atzingen/hey-jarvis.git
cd hey-jarvis

# 1. Python deps (conda env called 'voice' — matches the wrapper script)
conda create -n voice python=3.11 -y
conda activate voice
pip install -r requirements.txt

# 2. Scripts to ~/.local/bin/
install -Dm755 bin/voice-launcher     ~/.local/bin/voice-launcher
install -Dm755 bin/voice-launcher.py  ~/.local/bin/voice-launcher.py
install -Dm755 bin/dev-layout         ~/.local/bin/dev-layout

# 3. Systemd user unit
install -Dm644 systemd/voice-launcher.service \
  ~/.config/systemd/user/voice-launcher.service

# 4. Enable + start
systemctl --user daemon-reload
systemctl --user enable --now voice-launcher.service
```

The wrapper (`bin/voice-launcher`) assumes the conda env is at `~/miniconda3/envs/voice`. If you use venv/uv/pyenv instead, edit that script to activate your environment and exec `python -u ~/.local/bin/voice-launcher.py "$@"`.

## Usage

Speak **"hey jarvis"** → wait for the short voice cue **"No que vamos trabalhar, senhor?"** → speak your command.

Examples:

| You say | What happens |
|---|---|
| `hey jarvis ... abrir iaprev` | `dev-layout iaprev` — opens the 2×2 terminal grid + editor + browser |
| `hey jarvis ... que horas são em Tóquio agora?` | Sonnet/low answers, TTS speaks it back |
| `hey jarvis ... pense bem, como eu deveria estruturar esse deploy?` | Opus/high answers with more latency |
| `hey jarvis ... dormir` | suspends the machine |

Tune the wake-word sensitivity or whisper model:

```bash
# edit ~/.config/systemd/user/voice-launcher.service
ExecStart=%h/.local/bin/voice-launcher --wake-threshold 0.55 --whisper-model medium
systemctl --user daemon-reload && systemctl --user restart voice-launcher
```

## Controls

```bash
systemctl --user stop       voice-launcher       # mute mic now
systemctl --user start      voice-launcher       # resume
systemctl --user restart    voice-launcher       # after editing the .py
systemctl --user disable    voice-launcher       # don't start on boot
systemctl --user status     voice-launcher
journalctl  --user -u       voice-launcher -f    # live log (wake scores, transcripts, routes)
```

## The `dev-layout` script

`dev-layout <projeto>` is called by the voice launcher for "abrir X", but it also works standalone:

```bash
dev-layout iaprev
```

It picks the lowest free workspace in `1..5` and the lowest free in `6..9`, then spawns:

- **Workspace 1–5** — a 2×2 Ghostty grid. Three of the four terminals auto-run `claude --dangerously-skip-permissions`, one is a plain shell. Top row is resized +206px so the split isn't even.
- **Workspace 6–9** — VS Code in the top half, Chrome in the bottom with three tabs (github.com, github.com, claude.ai/new).

All windows cd into `~/Desktop/dev/<projeto>`. Adjust the paths/layouts inside the script to taste.

## Config

Constants at the top of `bin/voice-launcher.py`:

| Constant | Default | What |
|---|---|---|
| `SAMPLE_RATE` | 16000 | mic sample rate |
| `DEV_DIR` | `~/Desktop/dev` | where `match_project` scans |
| `VOICE` | `~/.local/share/piper-voices/pt_BR-faber-medium.onnx` | piper voice |
| `VOICE_LENGTH_SCALE` | 1.15 | >1 = slower, more formal |
| `WAKE_THRESHOLD` | 0.5 | openWakeWord trigger score |
| `RECORD_SECONDS` | 4.0 | how long to record after wake |
| `CLAUDE_SYSTEM` | (see file) | system prompt for TTS-friendly answers |
| `CLAUDE_TIMEOUT_QUICK` / `CLAUDE_TIMEOUT_DEEP` | 45 / 180 | seconds |

Command line:

```bash
voice-launcher --test                    # dry-run: no layout, no suspend
voice-launcher --wake-threshold 0.6
voice-launcher --whisper-model medium    # tiny|base|small|medium
```

## License

MIT — see `LICENSE`.
