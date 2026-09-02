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
- `~/.local/share/jarvis/` — Python venv (unless a conda env `voice` exists), the panel app QML, and `workdir/` (the empty working directory the model CLI runs in under `system_access = "ask"`)
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
own systemd scopes so they survive service restarts; model-call scopes are
named so a cancelled question stops everything it spawned (see below). Runtime
state (conversation state, dictation marker, consent requests) lives in
`$XDG_RUNTIME_DIR` (owner-only), not in shared `/tmp`.

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

Answers come from the coding agents the user already has (Codex CLI and/or
Claude Code), authenticated with the user's own accounts — this repository
ships no credentials and no API keys. Because the input to those agents is
**speech** (which can be misrecognized, or spoken by someone else in the room)
and whatever the model reads, the agents are never given unattended machine
access by default.

| `system_access` | What the model can do |
|---|---|
| **`ask`** (default) | Sandboxed, with per-action consent. **Claude Code** runs with `--permission-mode default` (explicit, so a user's own `bypassPermissions` setting does not leak in), `--permission-prompt-tool mcp__jarvis__approve` and `--strict-mcp-config` pointing at `bin/jarvis_consent_mcp.py`; read-only tools and Claude Code's built-in read-only command set run directly, every other tool call is shown to the user with its **exact input** (the full command line, file path, URL) and runs only after `y`. The CLI's working directory is an empty, plugin-owned folder, so reading files elsewhere also asks. **Codex** runs with `--sandbox read-only -c approval_policy=never` (Codex's OS sandbox: no writes, no network, no Docker socket); anything beyond that must be *proposed* as `<<RODAR: command>>`, which the launcher shows verbatim in the same window and executes only with consent. |
| `full` | The 2.1 behaviour: Codex `--dangerously-bypass-approvals-and-sandbox`, Claude `--dangerously-skip-permissions`. Opt-in only (`jarvis config set system_access full`); the panel shows the mode in the accent "urgent" colour. |
| `off` | Knowledge-only: Claude `--tools ""`, Codex read-only sandbox without the `<<RODAR>>` protocol. |

Consent mechanics (`bin/jarvis_consent.py`, `bin/jarvis-consent.py`):

- A request is a JSON file in `$XDG_RUNTIME_DIR/jarvis-consent/` (mode 0700);
  the window writes the decision next to it. No sockets, no daemons.
- The window displays the question the user asked, the exact pending command
  or tool input, and the working directory. Keys: `y` allow once, `a` allow
  the rest of *this question only*, `n`/Esc deny. **Timeout = deny** (90 s; the
  Claude Code side is capped by `CLAUDE_CODE_APPROVAL_TIMEOUT_MS`).
- "Allow the rest of this question" is held in memory by the process serving
  that one model call and dies with it. **Nothing is ever persisted** — no
  allow rules are written to any settings file.
- Jarvis announces a pending request by voice and switches the conversation
  window to the AUTHORIZATION phase, so a request can't wait unnoticed.
- The built-in desktop actions (open project, open app, suspend, end) remain an
  **allowlisted broker** in the launcher: the model only emits a marker; the
  launcher matches it against installed desktop entries / the projects folder
  and launches through `uwsm-app`. Suspend is the only one with system-wide
  effect and only fires on an explicit request.

Process bounds: each model call runs in its **own named systemd scope**
(`jarvis-model-<pid>-<n>.scope`). Cancelling the question (barge-in, `q` in
the window, dictation hotkey) stops that scope — the CLI, any command it
spawned, the consent server — and denies every open consent request. Restarting
`voice-launcher.service` intentionally leaves already-launched scopes alive
(a long answer being followed in a scratch terminal), which is documented
behaviour; nothing in those scopes holds a bypass grant, since in `ask` mode a
pending action cannot proceed without a fresh `y`.

Existing configurations with the 2.1 boolean (`system_access = true|false`)
are read as `full`/`off`.

## Remote build (capability: `remote-build`)

The manual install path is `git clone` of **this repository** followed by
`install.sh` from the checkout — no code is fetched from third-party
repositories at install or at runtime. The only external artifacts are the
pinned, checksummed Piper voice files above.

## Reporting a concern

Open an issue at <https://github.com/Atzingen/hey-jarvis/issues>, or use
GitHub's private vulnerability reporting on this repository for anything
sensitive.
