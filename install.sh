#!/bin/bash
# Jarvis installer — idempotent. Run from the plugin/repo folder:
#   bash install.sh            # full install (python env, voices, scripts, systemd service)
#   bash install.sh --update   # re-copy scripts + restart the service (after git pull)
#
# Python environment: reuses a conda env named `voice` if it exists, otherwise
# creates a venv at ~/.local/share/jarvis/venv. GPU wheels (cuBLAS/cuDNN) are
# installed when an NVIDIA GPU is detected, so local Whisper runs on CUDA.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
VENV="$HOME/.local/share/jarvis/venv"
VOICES="$HOME/.local/share/piper-voices"
UNIT_DIR="$HOME/.config/systemd/user"
UPDATE_ONLY=0
[[ ${1:-} == "--update" ]] && UPDATE_ONLY=1

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1; }

# --- 1. system dependencies ---------------------------------------------------
if (( ! UPDATE_ONLY )); then
  say "Checking system dependencies"
  missing=()
  need python3 || missing+=(python)
  need ghostty || need xdg-terminal-exec || missing+=(ghostty)
  need pactl || missing+=(pipewire-pulse)
  ldconfig -p 2>/dev/null | grep -q libportaudio || missing+=(portaudio)
  if (( ${#missing[@]} )); then
    if need omarchy; then
      say "Installing: ${missing[*]} (via omarchy pkg add)"
      omarchy pkg add "${missing[@]}"
    else
      warn "install these packages and re-run: ${missing[*]}"
      exit 1
    fi
  fi
  need codex || warn "Codex CLI not found — fast questions need it (or set quick_provider = \"claude\")."
  need claude || warn "Claude Code CLI not found — 'think hard' questions need it."
fi

# --- 2. python environment ------------------------------------------------------
if (( ! UPDATE_ONLY )); then
  if [[ -x "$HOME/miniconda3/envs/voice/bin/python" ]]; then
    say "Using existing conda env 'voice'"
    PY="$HOME/miniconda3/envs/voice/bin/python"
  else
    say "Creating venv at $VENV"
    python3 -m venv "$VENV"
    PY="$VENV/bin/python"
  fi
  say "Installing Python packages"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r "$HERE/requirements.txt"
  if need nvidia-smi && nvidia-smi >/dev/null 2>&1; then
    say "NVIDIA GPU detected — installing CUDA libraries for local Whisper"
    "$PY" -m pip install -q -r "$HERE/requirements-gpu.txt"
  fi
fi

# --- 3. voices ------------------------------------------------------------------
if (( ! UPDATE_ONLY )); then
  say "Downloading Piper voices (pt-BR + en-US) into $VOICES"
  mkdir -p "$VOICES"
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main"
  for spec in "pt/pt_BR/faber/medium/pt_BR-faber-medium" "en/en_US/lessac/medium/en_US-lessac-medium"; do
    name="${spec##*/}"
    for ext in onnx onnx.json; do
      [[ -f "$VOICES/$name.$ext" ]] || curl -sL -o "$VOICES/$name.$ext" "$base/$spec.$ext"
    done
  done
fi

# --- 4. scripts -----------------------------------------------------------------
say "Installing scripts to $BIN_DIR"
mkdir -p "$BIN_DIR"
for f in voice-launcher voice-launcher.py jarvis jarvis_config.py jarvis-config.py jarvis_i18n.py \
         jarvis_stt.py jarvis_events.py jarvis-window.py dev-layout; do
  install -Dm755 "$HERE/bin/$f" "$BIN_DIR/$f"
done
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH — the bar widget and keybindings need it." ;; esac

# --- 5. systemd user service ---------------------------------------------------
say "Installing voice-launcher.service"
install -Dm644 "$HERE/systemd/voice-launcher.service" "$UNIT_DIR/voice-launcher.service"
systemctl --user daemon-reload
systemctl --user enable voice-launcher.service >/dev/null 2>&1 || true
if systemctl --user is-active --quiet voice-launcher.service; then
  systemctl --user restart voice-launcher.service
else
  systemctl --user start voice-launcher.service
fi
sleep 2
if systemctl --user is-active --quiet voice-launcher.service; then
  say "Jarvis is running. Say \"hey jarvis\"."
else
  warn "service failed to start — check: journalctl --user -u voice-launcher -n 50"
fi

# --- 6. keybindings hint --------------------------------------------------------
if [[ -f "$HOME/.config/hypr/bindings.lua" ]] && ! grep -q "jarvis" "$HOME/.config/hypr/bindings.lua"; then
  cat <<EOF

Optional Hyprland keybindings — add to ~/.config/hypr/bindings.lua:
  o.bind("CTRL + SHIFT + J", "Toggle Jarvis",  { exec = "jarvis toggle-notify" })
  o.bind("CTRL + SHIFT + H", "Jarvis push-to-talk", { exec = "systemctl --user kill -s SIGUSR1 voice-launcher.service" })
EOF
fi
say "Settings: jarvis config   |   Logs: jarvis log"
