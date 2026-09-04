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
- `~/.local/share/jarvis/` — the dedicated Python venv (always; no pre-existing environment is reused), the panel app QML, `models/whisper/` (Whisper weights, see *Network endpoints*), and `workdir/` (the empty working directory the model CLI and every authorized command run in under `system_access = "ask"`)
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

Python dependencies are installed with pip **into the dedicated venv** from
`requirements.lock` / `requirements-gpu.lock`: the **complete transitive set,
every artifact pinned to its sha256**, installed with `--require-hashes
--no-deps` (no resolver, nothing outside the lock can be fetched). pip is not
upgraded; the interpreter's bundled pip is used. The locks are generated from
`requirements*.txt` with `uv pip compile --universal --generate-hashes`
(`requirements-overrides.txt` drops `tflite-runtime`, which openwakeword
declares but Jarvis does not use — it loads the wake-word model with
onnxruntime).

## Service management (capability: `service-management`)

The `jarvis` CLI starts/stops/pauses `voice-launcher.service` — a **user**
unit, never system-wide. Subprocesses (apps, model CLIs) are launched in their
own systemd scopes so they survive service restarts; model-call scopes are
named so a cancelled question stops everything it spawned (see below). Runtime
state (conversation state, dictation marker, consent requests, the per-call
answer/event/stderr files, the TTS wave file) lives in `$XDG_RUNTIME_DIR`
(owner-only; the unit runs with `UMask=0077`, `NoNewPrivileges`,
`LockPersonality`, `RestrictSUIDSGID`), never in shared `/tmp`. Files of a
call that was handed to a scratch terminal are deleted when that terminal
finishes, and leftovers older than a day are swept at service start.

The journal (`jarvis log`) records timings, sizes, phases and the decisions of
the authorization window. **Transcriptions, dictated text and answer text are
not logged** unless `log_transcripts = true`.

## Network endpoints

| Endpoint | When |
|---|---|
| `pypi.org` | install only — hash-locked `requirements.lock` (`--require-hashes --no-deps`) |
| `huggingface.co/rhasspy/piper-voices` | install only — **pinned to an immutable revision, every file verified against its sha256** before use |
| `github.com/dscripka/openWakeWord/releases` | install only — the ONNX wake-word/feature models, **fixed release `v0.5.1`, every file verified against its sha256** before use (openwakeword's own unpinned `download_models()` is never called) |
| `huggingface.co` (`Systran/faster-whisper-*`, `dropbox-dash/faster-whisper-large-v3-turbo`) | **first conversation** — Whisper weights, fetched at a **fixed commit** (`jarvis_stt.WHISPER_REVISIONS`; the hub verifies each file's sha256 against that commit) into `~/.local/share/jarvis/models/whisper/`; CTranslate2 loads `model.bin` as data, no Python is executed from it |
| Anthropic / OpenAI backends of the **user's own** `claude` / `codex` CLIs | every question — the question, the conversation history and (in `ask`/`full`) whatever the model reads on the machine go to the provider the user is logged into |
| `api.openai.com` | (1) `stt_provider = "openai"`, or `auto` **without a CUDA GPU and with** `openai_api_key` set: the speech audio; (2) progress narration when `narration = "openai"`, or `auto` **without** a local Ollama+GPU and **with** a key: the question and the model's tool-event summaries. Never contacted without a key |
| `127.0.0.1:11434` (Ollama) | `dictation_polish`, and progress narration when `narration = "local"`/`auto` with a local model + GPU (a warm-up request at conversation start) |

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
access by default, and in the default mode they have **no execution, read or
write tool of their own at all**.

| `system_access` | What the model can do |
|---|---|
| **`ask`** (default) | The CLI is launched with **every built-in tool removed** and **none of the user's own configuration**: Claude Code `--restricted --tools "" --strict-mcp-config --permission-mode manual --no-session-persistence` (ignores `~/.claude/settings.json`, project/local settings, hooks and plugins; refuses `bypassPermissions`); Codex `--ignore-user-config --ignore-rules --ephemeral --sandbox read-only -c approval_policy=never -c web_search="disabled" -c agents.max_depth=0 -c include_apply_patch_tool=false --disable shell_tool --disable unified_exec --disable view_image --disable multi_agent --disable plugins --disable memories --disable skill_search --disable apps --disable image_generation --disable computer_use --disable browser_use` (no shell tool, no user MCP servers, no sub-agents; Codex's OS sandbox still underneath). The **only** tool either CLI has is `run(command)` from `bin/jarvis_consent_mcp.py` (a 150-line stdio MCP server in this repository): every call — including *reading* a file — shows the **exact bash command line** in the authorization window and runs only after `y`. The command runs as `bash -c` with the text the user saw, in `~/.local/share/jarvis/workdir`, with a **minimal environment** (`PATH`, `HOME`, `XDG_*`, display/session variables only — no API keys or tokens from the service environment), a 60 s timeout, output capped at 64 KB, inside the model call's systemd scope. The launcher refuses to start a model call in this mode if the installed CLI lacks the flags above (it answers "computer access unavailable" instead of falling back to a less restricted invocation). |
| `full` | The user's own tool with the user's own configuration and no confirmation: Codex `--dangerously-bypass-approvals-and-sandbox`, Claude `--dangerously-skip-permissions`. Opt-in only (`jarvis config set system_access full`); the panel shows the mode in the accent "urgent" colour. Also the only mode in which the `dev-layout` terminals start `claude` with `--dangerously-skip-permissions`. |
| `off` | Knowledge-only: the same restricted invocations as `ask` but **without** the `run` tool (Claude: `--mcp-config '{"mcpServers":{}}'`; Codex: no MCP server). Desktop-action markers other than `<<FIM>>` are ignored. |

Consent mechanics (`bin/jarvis_consent.py`, `bin/jarvis-consent.py`):

- A request is a JSON file (mode 0600) in `$XDG_RUNTIME_DIR/jarvis-consent/`
  (mode 0700, ownership checked); the window writes the decision next to it.
  No sockets, no daemons. The request text is written by the MCP server from
  the tool call itself, so what is displayed is what is executed.
- The window displays the question the user asked, the **complete command
  line** (scrolling when it does not fit — `y` is only accepted after the user
  has scrolled to the end), and the working directory. Every character that
  came from the model or the transcription is rendered through `safe_text()`:
  C0/C1 control characters, DEL and Unicode format characters (bidi overrides,
  zero-width) are shown as visible escapes, so a payload cannot redraw, clear
  or retitle the window (`tests/test_consent.py`). The same renderer is used
  by the conversation window and the scratch terminal.
- Keys: **only `y`** allows (Enter does not), `a` allows the rest of *this
  model call*, `n`/Esc deny. Keys typed before the window opened are flushed
  and input is ignored for the first 0.7 s, so a window that takes focus
  cannot be approved by a keystroke already in flight. **Timeout = deny**
  (90 s); no terminal available to open the window = deny immediately.
- "Allow the rest of this question" is a grant file bound to the model call's
  scope name, valid for 15 minutes at most, revoked when the answer is
  delivered, when the question is cancelled, when a long answer is handed to
  the scratch terminal, and at service start. **Nothing is ever persisted** —
  no allow rules are written to any settings file, and the CLIs cannot write
  their own settings (they have no write tool).
- Jarvis announces a pending request by voice and switches the conversation
  window to the AUTHORIZATION phase, so a request can't wait unnoticed.

Desktop actions (`voice-launcher.py`): the model can end the conversation,
suspend the machine, open a project layout or open an application only by
emitting a marker (`<<FIM>>`, `<<DORMIR>>`, `<<ABRIR_PROJETO: name>>`,
`<<ABRIR_APP: name>>`) as a **whole trailing line** of its answer — markers
quoted anywhere else (for example inside a file the model read) are text, not
actions. `<<DORMIR>>` additionally requires the user's own transcript to
contain a sleep/suspend word. `<<ABRIR_APP>>` matches **only** desktop entries
of `Type=Application` in `/usr/share/applications` and
`~/.local/share/applications` (no PATH fallback: `poweroff`, `reboot`,
`systemctl` … are not launchable). `<<ABRIR_PROJETO>>` accepts only a real
sub-directory (no symlink) of `dev_dir`, and `dev-layout` re-checks that
before opening anything. In `off` mode every marker except `<<FIM>>` is
ignored.

Process bounds: each model call runs in its **own named systemd scope**
(`jarvis-model-<pid>-<n>.scope`), and authorized commands run inside it.
When the answer is delivered the scope is stopped, so nothing an authorized
command left in the background outlives the question. Cancelling the question
(barge-in, `q` in the window, dictation hotkey) stops that scope, denies every
open consent request and revokes the grant. Restarting
`voice-launcher.service` intentionally leaves already-launched scopes alive
(a long answer being followed in a scratch terminal), which is documented
behaviour; the grant does not survive (revoked at hand-off and at start), so
every further command in that scope needs a fresh `y`.

What an approval means: an authorized command runs with the user's full
privileges (there is no privilege separation between Jarvis and the user), so
the user must read the whole command before pressing `y`. The plugin's job is
to guarantee that what is shown is exactly what runs, that nothing runs
without that keystroke, and that no approval outlives the question.

Existing configurations with the 2.1 boolean (`system_access = true|false`)
are read as `ask`/`off`: the no-confirmation mode requires an explicit opt-in
in this version.

## Remote build (capability: `remote-build`)

The manual install path is `git clone` of **this repository** followed by
`install.sh` from the checkout — no code is fetched from third-party
repositories at install or at runtime. The external artifacts are the pinned,
checksummed Piper voice and openWakeWord model files above, and the Whisper
weights fetched at a fixed commit on first use — all data files, no code.

## Reporting a concern

Open an issue at <https://github.com/Atzingen/hey-jarvis/issues>, or use
GitHub's private vulnerability reporting on this repository for anything
sensitive.
