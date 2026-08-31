# Security model

This page is written for marketplace reviewers and for anyone deciding whether
to trust Jarvis. It maps everything the plugin does to the capabilities the
[Omarchy marketplace security baseline](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SECURITY.md)
detects, and states exactly what is written, executed, and contacted.

## What runs where

| Component | Process | Started by |
|---|---|---|
| Bar widget + hover panel (`BarWidget.qml`, `app/qs/`) | inside the Omarchy shell (quickshell) | the shell, when the plugin is enabled |
| Voice service (`voice-launcher.py`) | `voice-launcher.service`, a **user** systemd unit | `install.sh` (opt-in — see below) |
| Conversation / dictation window, settings screen, `jarvis app` | floating terminal or window | the user (hotkey, click, or CLI) |

Adding the plugin (`omarchy plugin add`) installs **only the bar widget**.
The voice service is a separate, explicit step: the **Install** button on the
panel (which opens `install.sh` in a visible terminal) or running `install.sh`
by hand. Nothing is installed silently.

## What `install.sh` writes (capability: `installer`)

- `~/.local/bin/` — the scripts listed in `SCRIPTS` (this repository's `bin/`)
- `~/.local/share/jarvis/` — Python venv (unless a conda env `voice` exists), the panel app QML
- `~/.local/share/piper-voices/` — TTS voices (pinned + checksummed, see below)
- `~/.local/share/applications/jarvis.desktop` — launcher entry
- `~/.config/systemd/user/voice-launcher.service` — the user unit
- `~/.config/jarvis/` — created at runtime for `config.toml` (mode 0600)

It never edits Hyprland or Omarchy configuration; keybindings and the
bar-accent hook are opt-in snippets the user adds themselves.
`install.sh --uninstall [--purge]` removes everything above.

## Privileges

**No sudo or pkexec is required** by any script in this repository. When a
system dependency is missing, `install.sh` delegates to `omarchy pkg add`
(capability: `package-manager`), which manages its own privilege prompting —
and only for the packages it names (`portaudio`, `pipewire-pulse`,
`wl-clipboard`, `wtype`, a terminal). Outside Omarchy the installer just
prints the missing package names and exits.

Python dependencies are installed with pip **into the dedicated venv**, from
`requirements.txt` with every version pinned.

## Service management (capability: `service-management`)

The `jarvis` CLI starts/stops/pauses `voice-launcher.service` — a **user**
unit, never system-wide. Subprocesses (apps, model CLIs) are launched in their
own systemd scopes so they survive service restarts. Runtime state
(conversation state, dictation marker) lives in `$XDG_RUNTIME_DIR` (owner-only),
not in shared `/tmp`.

## Network endpoints

| Endpoint | When |
|---|---|
| `pypi.org` | install only — pinned `requirements.txt` |
| `huggingface.co/rhasspy/piper-voices` | install only — **pinned to an immutable revision, every file verified against its sha256** before use |
| `api.openai.com` | only if the user sets `stt_provider = "openai"` (default is local Whisper) |
| `127.0.0.1:11434` (Ollama) | only if the user enables `dictation_polish` |

Wake word detection, voice activity detection, local transcription and TTS all
run on the machine. Nothing is recorded or sent anywhere until the wake word
fires, and with the default local STT the audio never leaves the machine.

`jarvis wake off` (or the “hey jarvis” switch on the panel) disables the
continuous listening entirely: the microphone stream is **closed** while idle
and only opens on an explicit user action — push-to-talk or dictation hotkeys.

## Model machine access (`system_access`)

By design, answers come from the coding agents the user already has: Codex CLI
(`--dangerously-bypass-approvals-and-sandbox`) and/or Claude Code
(`--dangerously-skip-permissions`). This is the plugin's core feature — "how
many containers are running?" is answered by actually running `docker ps` —
and it is documented, on by default, and **fully toggleable**:
`jarvis config set system_access false` switches both CLIs to knowledge-only
mode (read-only sandbox / no tools). The CLIs authenticate with the user's own
accounts; this repository ships no credentials and no API keys.

## Remote build (capability: `remote-build`)

The manual install path is `git clone` of **this repository** followed by
`install.sh` from the checkout — no code is fetched from third-party
repositories at install or at runtime. The only external artifacts are the
pinned, checksummed Piper voice files above.

## Reporting a concern

Open an issue at <https://github.com/Atzingen/hey-jarvis/issues>, or use
GitHub's private vulnerability reporting on this repository for anything
sensitive.
