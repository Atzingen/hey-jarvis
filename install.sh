#!/bin/bash
# Jarvis installer — idempotent. Run from the plugin/repo folder:
#   bash install.sh              # full install (python env, models, voices, scripts, systemd service)
#   bash install.sh --update     # same, minus system packages: re-applies the lock, re-verifies
#                                # models/voices, re-copies scripts, restarts the service (after git pull)
#   bash install.sh --uninstall  # stop/disable the service, stop model scopes, remove scripts,
#                                # unit and ~/.local/share/jarvis (venv, models, workdir)
#                                # (keeps ~/.config/jarvis and the Piper voices; add --purge to remove them)
#
# Python environment: a dedicated venv at ~/.local/share/jarvis/venv, always.
# Packages come from requirements.lock (complete transitive set, sha256 per
# artifact, installed with --require-hashes --no-deps). GPU wheels (cuBLAS/cuDNN,
# requirements-gpu.lock) are installed when an NVIDIA GPU is detected.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SHARE="$HOME/.local/share/jarvis"
VENV="$SHARE/venv"
VOICES="$HOME/.local/share/piper-voices"
UNIT_DIR="$HOME/.config/systemd/user"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
UPDATE_ONLY=0
[[ ${1:-} == "--update" ]] && UPDATE_ONLY=1

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1; }

# fetch_verified <url> <sha256> <dest>: downloads only when the file is missing
# or its hash does not match; a mismatch after download aborts the install.
fetch_verified() {
  local url="$1" sha="$2" dest="$3"
  if [[ -f "$dest" ]] && echo "$sha  $dest" | sha256sum -c --quiet - 2>/dev/null; then
    return 0
  fi
  [[ -f "$dest" ]] && warn "$(basename "$dest") exists with a different checksum — re-downloading"
  curl -sSL --fail --proto '=https' --tlsv1.2 --max-time 600 -o "$dest.tmp" "$url"
  echo "$sha  $dest.tmp" | sha256sum -c --quiet - || {
    rm -f "$dest.tmp"
    warn "checksum mismatch for $(basename "$dest") — not installed"
    exit 1
  }
  mv "$dest.tmp" "$dest"
}

SCRIPTS=(voice-launcher voice-launcher.py jarvis jarvis_config.py jarvis-config.py jarvis_i18n.py
         jarvis_stt.py jarvis_events.py jarvis_dictate.py jarvis_narrate.py jarvis-window.py jarvis-app.py jarvis-panel.py
         jarvis_consent.py jarvis-consent.py jarvis_consent_mcp.py dev-layout)

if [[ ${1:-} == "--uninstall" ]]; then
  say "Stopping and disabling voice-launcher.service"
  systemctl --user disable --now voice-launcher.service 2>/dev/null || true
  systemctl --user stop jarvis-resume.timer 2>/dev/null || true
  # model calls run in their own scopes (they survive a service restart on purpose)
  systemctl --user list-units --plain --no-legend 'jarvis-model-*' 2>/dev/null | awk '{print $1}' \
    | xargs -r systemctl --user stop 2>/dev/null || true
  rm -f "$UNIT_DIR/voice-launcher.service"; systemctl --user daemon-reload
  say "Removing scripts from $BIN_DIR"
  for f in "${SCRIPTS[@]}"; do rm -f "$BIN_DIR/$f"; done
  rm -f "$HOME/.local/share/applications/jarvis.desktop"
  say "Removing $SHARE (venv, models, panel app, workdir)"
  rm -rf "$SHARE"
  rm -rf "$RUNTIME/jarvis-consent" "$RUNTIME/jarvis-model"
  rm -f "$RUNTIME"/jarvis-state.json "$RUNTIME"/jarvis-state.tmp "$RUNTIME"/jarvis-quit \
        "$RUNTIME"/jarvis-wake-off "$RUNTIME"/jarvis-dictating "$RUNTIME"/jarvis-dictate.cmd \
        "$RUNTIME"/jarvis-meeting-paused "$RUNTIME"/jarvis-resume-at
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
  need alacritty || missing+=(alacritty)   # conversation, authorization and settings windows
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
# Always the plugin's own venv: nothing pre-installed elsewhere becomes service code.
if [[ ! -x "$VENV/bin/python" ]]; then
  say "Creating venv at $VENV"
  python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"
{
  # requirements*.lock are the complete transitive set, every artifact pinned to
  # its sha256 (generated from requirements*.txt with `uv pip compile
  # --generate-hashes`). --require-hashes refuses anything not in the lock and
  # --no-deps disables the resolver, so the index cannot substitute a package
  # after review. pip itself is not upgraded: the interpreter's bundled pip is used.
  say "Installing Python packages (hash-locked)"
  "$PY" -m pip install -q --require-hashes --no-deps -r "$HERE/requirements.lock"
  if need nvidia-smi && nvidia-smi >/dev/null 2>&1; then
    say "NVIDIA GPU detected — installing CUDA libraries for local Whisper (hash-locked)"
    "$PY" -m pip install -q --require-hashes --no-deps -r "$HERE/requirements-gpu.lock"
  fi
}

# --- 3. voices ------------------------------------------------------------------
{
  say "Piper voices (pt-BR + en-US) in $VOICES"
  mkdir -p "$VOICES"
  # Pinned to an immutable revision of rhasspy/piper-voices, and every file is
  # verified against its sha256 — also when it already exists (a stale or
  # tampered file is re-downloaded, never used).
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
    fetch_verified "$base/$spec" "$sha" "$VOICES/${spec##*/}"
  done

  # openWakeWord 0.6 ships no model files: the wake-word and feature models
  # come from a fixed GitHub release of dscripka/openWakeWord and are verified
  # against their sha256 before being placed where the package looks for them
  # (its own resources/models directory inside the env). Only the ONNX files:
  # Jarvis loads them with onnxruntime. The package's own download_models()
  # helper (unpinned, no checksum) is never called.
  OWW_DIR="$("$PY" -c 'import openwakeword, os; print(os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models"))')"
  say "openWakeWord models in $OWW_DIR"
  mkdir -p "$OWW_DIR"
  oww_base="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
  oww_files=(
    "melspectrogram.onnx ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f"
    "embedding_model.onnx 70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"
    "hey_jarvis_v0.1.onnx 94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"
    "alexa_v0.1.onnx 6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e"
    "hey_mycroft_v0.1.onnx c2a311e8fa1338de89c31b3b46dc4dffd4af2f9a8d6ddead48893c2d301b1f18"
    "hey_rhasspy_v0.1.onnx 5a9b3ed3be2910e35780e097905aa9f35a9c10038df47914cf2b3ec4d670f6ea"
  )
  for entry in "${oww_files[@]}"; do
    read -r name sha <<<"$entry"
    fetch_verified "$oww_base/$name" "$sha" "$OWW_DIR/$name"
  done
}

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

say "Installing the panel app to $SHARE/app"
rm -rf "$SHARE/app"
mkdir -p "$SHARE"
cp -rL "$HERE/app" "$SHARE/app"
# the model's working directory in `ask` mode: recreated empty at every install
rm -rf "$SHARE/workdir"; mkdir -p "$SHARE/workdir"

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
