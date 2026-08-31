#!/bin/bash
# Jarvis installer — idempotent. Run from the plugin/repo folder:
#   bash install.sh              # full install (python env, voices, scripts, systemd service)
#   bash install.sh --update     # re-copy scripts + restart the service (after git pull)
#   bash install.sh --uninstall  # stop/disable the service, remove scripts, unit and venv
#                                # (keeps ~/.config/jarvis and the Piper voices; add --purge to remove them)
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

SCRIPTS=(voice-launcher voice-launcher.py jarvis jarvis_config.py jarvis-config.py jarvis_i18n.py
         jarvis_stt.py jarvis_events.py jarvis_dictate.py jarvis-window.py jarvis-app.py jarvis-panel.py dev-layout)

if [[ ${1:-} == "--uninstall" ]]; then
  say "Stopping and disabling voice-launcher.service"
  systemctl --user disable --now voice-launcher.service 2>/dev/null || true
  rm -f "$UNIT_DIR/voice-launcher.service"; systemctl --user daemon-reload
  say "Removing scripts from $BIN_DIR"
  for f in "${SCRIPTS[@]}"; do rm -f "$BIN_DIR/$f"; done
  rm -f "$HOME/.local/share/applications/jarvis.desktop"
  rm -rf "$HOME/.local/share/jarvis/app"
  [[ -d $VENV ]] && { say "Removing $VENV"; rm -rf "$VENV"; }
  rm -f "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/jarvis-state.json "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/jarvis-quit /tmp/jarvis-state.json /tmp/jarvis-quit
  if [[ ${2:-} == "--purge" ]]; then
    say "Removing ~/.config/jarvis and Piper voices"
    rm -rf "$HOME/.config/jarvis" "$VOICES"
  else
    say "Kept ~/.config/jarvis (settings, profiles) and $VOICES — pass --purge to remove them"
  fi
  say "Uninstalled. Remove the bar widget with: omarchy plugin remove atzingen.jarvis"
  exit 0
fi

# --- 1. system dependencies ---------------------------------------------------
if (( ! UPDATE_ONLY )); then
  say "Checking system dependencies"
  missing=()
  need python3 || missing+=(python)
  need ghostty || need xdg-terminal-exec || missing+=(ghostty)
  need pactl || missing+=(pipewire-pulse)
  need wl-copy || missing+=(wl-clipboard)
  need wtype || missing+=(wtype)
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
  # Pinned to an immutable revision of rhasspy/piper-voices, and every file is
  # verified against its sha256 before being kept (no mutable-branch downloads).
  PIPER_REV="39ab474be869e9181350af6a65e4953eef67aaa0"
  base="https://huggingface.co/rhasspy/piper-voices/resolve/$PIPER_REV"
  voice_files=(
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx 858555e3a064209c57088fe6bd70c4c3dc54d03eaa00c45d5ecaf43a33f95aa7"
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json 7e694de195ae3fc36dd732c445eb04fb49b649854893cb5506b978f0d50a1d6f"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx 5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"
  )
  for entry in "${voice_files[@]}"; do
    read -r spec sha <<<"$entry"
    name="${spec##*/}"
    [[ -f "$VOICES/$name" ]] && continue
    curl -sL -o "$VOICES/$name.tmp" "$base/$spec"
    echo "$sha  $VOICES/$name.tmp" | sha256sum -c --quiet - || {
      rm -f "$VOICES/$name.tmp"
      warn "checksum mismatch for $name — voice not installed"
      exit 1
    }
    mv "$VOICES/$name.tmp" "$VOICES/$name"
  done
fi

# --- 4. scripts -----------------------------------------------------------------
say "Installing scripts to $BIN_DIR"
mkdir -p "$BIN_DIR"
for f in "${SCRIPTS[@]}"; do
  install -Dm755 "$HERE/bin/$f" "$BIN_DIR/$f"
done
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH — the bar widget and keybindings need it." ;; esac

# --- 5. systemd user service ---------------------------------------------------
say "Installing voice-launcher.service"
install -Dm644 "$HERE/systemd/voice-launcher.service" "$UNIT_DIR/voice-launcher.service"

say "Installing the panel app to $HOME/.local/share/jarvis/app"
rm -rf "$HOME/.local/share/jarvis/app"
mkdir -p "$HOME/.local/share/jarvis"
cp -rL "$HERE/app" "$HOME/.local/share/jarvis/app"

say "Installing desktop entry (Jarvis in the app launcher)"
mkdir -p "$HOME/.local/share/applications"
sed "s|@BIN@|$BIN_DIR|" "$HERE/integrations/jarvis.desktop" | grep -v '^#' \
  > "$HOME/.local/share/applications/jarvis.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" || true
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

Optional Hyprland keybindings (toggle, push-to-talk, dictation with Ctrl+Shift+K/L):
  see integrations/hypr-bindings.lua — append it to ~/.config/hypr/bindings.lua
EOF
fi
say "Settings: jarvis config   |   Logs: jarvis log"
