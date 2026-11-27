# Security model

This page is written for marketplace reviewers and for anyone deciding whether
to trust Jarvis. It maps everything the plugin does to the capabilities the
[Omarchy marketplace security baseline](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/SECURITY.md)
detects, and states exactly what is written, executed, and contacted.

## What runs where

| Component | Process | Started by |
|---|---|---|
| Bar widget + panel (`BarWidget.qml`, `app/qs/`) | inside the Omarchy shell (quickshell) | the shell, when the plugin is enabled |
| Voice service (`voice-launcher.py`) | `voice-launcher.service`, a **user** systemd unit | `install.sh` (opt-in — see below) |
| Conversation / dictation window, settings screen, `jarvis app` | floating terminal or window | the user (hotkey, click, or CLI) |
| Picoh robot face (`jarvis_picoh.py`, optional) | child process of the voice service; idle unless a Picoh answers on a USB serial port | the voice service, when `picoh = auto` (default) |
| Nemotron STT (`jarvis_nemotron.py`, optional) | owned native child, loopback only, random port and per-process bearer token (passed in the server's environment, not on its command line; the server gets no API keys) | the voice service, only with `stt_provider = "nemotron"` and prior installation |

Adding the plugin (`omarchy plugin add`) installs **only the bar widget**.
The voice service is a separate, explicit step: the **Install** button on the
panel (which opens `install.sh` in a visible terminal) or running `install.sh`
by hand. Nothing is installed silently.

## What `install.sh` writes (capability: `installer`)

- `~/.local/bin/` — the scripts listed in `SCRIPTS` (this repository's `bin/`)
- `~/.local/share/jarvis/` — the dedicated Python venv (always; no pre-existing environment is reused), the panel app QML, `models/whisper/` (Whisper weights, see *Network endpoints*), `workdir/` (the empty working directory the model CLI and every authorized command run in under `system_access = "ask"`), and `router-tools/` (a copy of the optional classifier installer and its lock files, run only by `jarvis router install-local`)
- `~/.local/share/piper-voices/` — TTS voices (pinned + checksummed, see below). Every download `install.sh` makes itself (`fetch_verified`: voices, openWakeWord models) is **size-capped before verification**: curl refuses an announced size above the ceiling and the stream is cut at the ceiling when no size is announced (128 MiB per voice file, 32 MiB per wake-word model), so a remote endpoint cannot write more than that to disk regardless of how long the 600 s transfer window is
- `~/.local/share/applications/jarvis.desktop` — launcher entry
- `~/.config/systemd/user/voice-launcher.service` — the user unit
- `~/.config/jarvis/` — created at runtime for `config.toml` (mode 0600)
- `/etc/udev/rules.d/60-jarvis-picoh.rules` — **only after an explicit `y`** to the question `install.sh` asks in the terminal (or the `--picoh-udev` flag); non-interactive runs and any other answer skip it and print the command instead. The rule (`integrations/60-jarvis-picoh.rules`, 4 lines) adds `uaccess` to tty devices of two USB vendor ids (Raspberry Pi Pico `2e8a`, CH340 `1a86`) so the logged-in user can open the Picoh's serial port without joining `uucp`/`dialout`. It is the only root-owned file the project can touch and it is never required: without it the robot simply stays unreachable.

It never edits Hyprland or Omarchy configuration; keybindings and the
bar-accent hook are opt-in snippets the user adds themselves.
`install.sh --uninstall` removes everything above except `~/.config/jarvis`
(settings and profiles) and the Piper voices, which `--purge` also removes, and the
root-owned udev rule, which it never touches without the user: when the rule exists
it prints the `sudo` command that removes it.

### The Picoh robot is optional

The [Picoh](https://www.ohbot.co.uk/picoh.html) support is a spectator: the
daemon reads the same two runtime files the conversation window reads
(`jarvis-state.json`, `jarvis-tts.json`) and writes **only** to the robot's
serial port. It has no other input, does not talk to the network and nothing in
the voice service waits on it. With `picoh = auto` it probes USB serial ports
(`/dev/ttyACM*`, `/dev/ttyUSB*`, or just `picoh_port` when set) with the
official library's handshake (`v` → `v2`); a port that does not answer is closed
and left alone (the probe can reset a board that auto-resets on DTR, which is
why `picoh_port` exists). With `picoh = off` no port is ever opened. Without a
robot, with the daemon disabled, or on a machine without `pyserial`, every other
feature behaves exactly the same. `pyserial` is hash-locked like every other
dependency.

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

`jarvis stt install-nemotron` is a separate, explicit installation. It downloads
NeMo-Speech.cpp 0.1.0 and the Q8_0 Nemotron 3.5 ASR model into
`~/.local/share/jarvis/nemotron/`. Both downloads have fixed versions/revisions,
SHA-256 checks and streamed size limits. Regular installation and service startup
never download Nemotron. The server is a child of the voice service, bypasses
HTTP proxies on loopback and stops with the service. Missing installation or a
failed transcription falls back to local Whisper; selecting Nemotron never
implicitly selects a paid API.

| Endpoint | When |
|---|---|
| `pypi.org` | install only — hash-locked `requirements.lock` (`--require-hashes --no-deps`) |
| `github.com/NVIDIA/NeMo-Speech.cpp/releases`, `huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b` | only `jarvis stt install-nemotron` — pinned, checksummed native runtime and model |
| `huggingface.co/rhasspy/piper-voices` | install only — **pinned to an immutable revision, every file verified against its sha256** before use |
| `github.com/dscripka/openWakeWord/releases` | install only — the ONNX wake-word/feature models, **fixed release `v0.5.1`, every file verified against its sha256** before use (openwakeword's own unpinned `download_models()` is never called) |
| `huggingface.co` (`Systran/faster-whisper-*`, `dropbox-dash/faster-whisper-large-v3-turbo`) | **when the voice service starts** with local recognition, or the first time Nemotron/OpenAI fall back to it — Whisper weights, fetched at a **fixed commit** (`jarvis_stt.WHISPER_REVISIONS`) into `~/.local/share/jarvis/models/whisper/`; CTranslate2 loads `model.bin` as data, no Python is executed from it |
| Anthropic / OpenAI backends of the **user's own** `claude` / `codex` CLIs | requests routed to the agent — the question, the conversation history and (in `ask`/`full`) whatever the model reads on the machine go to the provider the user is logged into |
| `api.openai.com` | (1) `stt_provider = "openai"`, or `auto` **without a CUDA GPU and with** `openai_api_key` set: the speech audio, plus the vocabulary hints as `keywords` (only with `gpt-live-transcribe` and while `stt_vocabulary_enabled` is on): the user's `stt_vocabulary`, or, when it is empty, the built-in terms and the names of the project folders in `dev_dir`; (2) progress narration when `narration = "openai"`, or `auto` **without** a local Ollama+GPU and **with** a key: the question and the model's tool-event summaries. Never contacted without a key |
| `127.0.0.1:11434` (Ollama) | `dictation_polish`, and progress narration when `narration = "local"`/`auto` with a local model + GPU (a warm-up request at conversation start) |

The OpenAI and Nemotron transcription sockets treat the server as untrusted input
(`jarvis_stt.OpenAISession._handle_frame`): the WebSocket keeps a finite
`max_size` (256 KiB per frame), and every frame is checked *before* anything is
retained — text frames only, a valid JSON object with string fields, at most
20 000 events and 16 MiB (UTF-8 bytes) per session, and an accumulated transcript (partial or
final) of at most 32 000 characters. Crossing any ceiling, or a nonconforming
event, closes the socket fail-closed; the speech then falls back to the local
Whisper. The connect deadline is 6 s; the final-transcript deadline is 8 s for
OpenAI and 15 s for Nemotron.

Wake word detection, voice activity detection, local transcription and TTS all
run on the machine. Nothing is recorded or sent anywhere until the wake word
fires, and with the default local STT the audio never leaves the machine.

`jarvis wake off` (or the “hey jarvis” switch on the panel) disables the
continuous listening entirely: the microphone stream is **closed** while idle
and only opens on an explicit user action — push-to-talk or dictation hotkeys.

With optional `dictation_live`, provisional words remain in the overlay and
confirmed phrases are inserted into the original active window using the selected
paste/type mode, with newlines converted to spaces. Modifier keys must be released
before insertion; an unavailable key-state query or a 10-second wait stops insertion.
The window identity and the paste shortcut come from one query made immediately
before insertion; a focus change between that query and the key event itself
remains possible. A focus
change blocks further insertion for that dictation; the complete transcript is
still copied to the clipboard at the end. Cancellation stops pending insertion
but does not erase text already inserted. This guard identifies a window, not
an individual input field within it.

## Optional routing and persistent sessions

`routing_mode = "agent"` and `api_provider = "none"` are the defaults for new
and existing configurations. The direct route never constructs a Jev, response
API or local-classifier backend. A key configured for speech recognition or
narration does not enable response API calls. Those existing subsystems retain
their own settings.

| Additional endpoint/process | When |
|---|---|
| `https://api.typesafe.ai/v1/systemone` | Explicit `jev` mode plus Jev key; receives the current text and conversation context for closed-category classification. It returns no executable commands. |
| `https://api.openai.com/v1/responses` | Explicit `api_provider = "openai"`, configured model and key, with an API route selected. Optional web search uses the provider's search tool. Responses do not execute launcher action markers. |
| Hugging Face `fastino/GLiNER2.5-multi-Decide` | Only `jarvis router install-local`; fixed revision `a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f`. The installer downloads a fixed list of five files, each checked against its exact size (the transfer stops beyond it) and SHA-256 before it is kept. |
| `pypi.org`, `download.pytorch.org/whl/cpu` | Only `jarvis router install-local`: the classifier's own venv from `requirements-router-local.lock` (`--require-hashes --no-deps`; PyTorch CPU wheels come from the PyTorch index). |
| CPU classifier child | Explicit `local` mode and prior installation. `CUDA_VISIBLE_DEVICES` is empty; PyTorch is a CPU wheel; model loading uses local files with Hugging Face offline mode. The process exits after inactivity. |
| Local Codex app-server / Claude background session and tmux | Native agent execution using the existing subscription login. No response API credential is required. Sessions have explicit provider, conversation, directory and permission identities. |

The optional classifier environment has its own complete hash-locked dependency
set, `requirements-router-local.lock`; it is not installed by `install.sh` and
does not modify the voice environment. The installer supports Python 3.11 on
Linux x86_64. Local classification is experimental; measured routing mistakes
are documented in `docs/routing-validation.md`, and cannot elevate permissions.

Routing selects only configured execution profiles. It never supplies arbitrary
model names, changes `system_access`, rewrites the original user request, or
adds tools. API text containing action markers remains text. Only a validated
handoff can reach the agent executor. HTTP bodies/responses are bounded;
credentials are passed to the cancellable HTTP child over stdin, not argv or
error messages, and redirects are refused, so the bearer never reaches another
URL (the OpenAI narration call refuses them too, and the realtime WebSocket client
drops the `Authorization` header on a cross-origin redirect). The Jev key goes only
to that HTTP child: the agent CLIs never receive `JEV_API_KEY`. Cancellation stops
the child.

Missing optional dependencies and definitive pre-execution failures may fall
back to the selected subscription agent. Every native submission records its
request ID before calling the CLI. Once submission is attempted, an ambiguous
connection loss does not trigger resubmission or a different executor. The
same native session is queried by ID; an unresolved outcome is shown as
uncertain. Opening a terminal only attaches; it does not send the prompt again.

Session records live under `~/.local/share/jarvis/sessions` (directories 0700,
files 0600), with atomic replacement and per-session locks. The CLI also stores
its normal conversation history. Restricted Codex sessions use an isolated
`CODEX_HOME`, linked only to the user's subscription authentication file, an
empty work directory, disabled native execution tools and project instruction
loading disabled. Codex still merges the system-wide `/etc/codex/config.toml`
into every run, isolated or not; when that file defines MCP servers, Jarvis
refuses Codex in `ask` and `off` (native and legacy alike) instead of running with
tools outside the consent broker. Full-access sessions keep the user's normal CLI
setup. Claude uses invocation-scoped settings (the hooks, and `fastMode` set from
`claude_fast`) and the same restricted tool configuration as the legacy path; its
system prompt, personal context included, is read from a private file in the
session directory, not passed on the command line. It never uses `--bare`; native background IDs are resolved
exactly, and it never resumes a running background session by creating a copy.
Voice input is literal bracketed paste over a private tmux socket; terminal
control characters are escaped and text never becomes shell code. Voice waits
while an attached terminal owns the input.

A Claude native session runs in the request directory only under
`system_access = "full"` and when that directory is not the home directory;
otherwise it runs in `~/.local/share/jarvis/workdir`. Claude Code asks the user
to trust a folder once; Jarvis never writes Claude's trust settings itself.
Until the user accepts that question for the work directory, the native session
does not start and Jarvis uses the compatibility executor.

A native session is replaced when the system prompt or the context file change,
and after `agent_session_max_hours` (12 by default), and a session whose
app-server or terminal died is retired before the next request. The replaced
session is stopped when nothing is running in it; a running turn keeps its own
deadline. Stopping a session also stops its watchdog unit, and a watchdog exits
by itself once its session is gone with nothing running.

The consent broker resolves the **current native turn** before authorizing a
command. Grants belong to that turn, and a lost/changed turn cancels consent and
kills a running brokered command group. Independent user watchdog units enforce
turn deadlines across voice-service restarts, without expiring idle sessions.
`jarvis session stop <id>` stops only that identified Jarvis session.

`agent_session_mode = "legacy"` remains an explicit rollback option. A CLI
without all required native capabilities also uses that path before the first
request is sent. See the main README for configuration and the validation
matrix for which integrations were exercised live.

## Model machine access (`system_access`)

Answers come from the coding agents the user already has (Codex CLI and/or
Claude Code), authenticated with the user's own accounts — this repository
ships no credentials and no API keys. Because the input to those agents is
**speech** (which can be misrecognized, or spoken by someone else in the room)
and whatever the model reads, the agents are never given unattended machine
access by default, and in the default mode they have **no execution, read or
write tool of their own at all**.

The flags in the table are the legacy executor's. Native sessions (the default,
`agent_session_mode = "auto"`) keep the same tool boundary: Claude runs with
`--restricted --tools "" --strict-mcp-config --permission-mode manual` and the
same single `run` tool, and Codex runs an app-server with the isolated
`CODEX_HOME`, the same disabled features and MCP configuration, and a read-only
sandbox per turn. Unlike the legacy flags, both keep their session history
(no `--no-session-persistence` / `--ephemeral`), so a terminal can attach to the
exact conversation.

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
  a terminal (the legacy viewer, a native session, the window's button or
  `jarvis session open`), and at service start. A decision still pending when a
  native turn is handed off counts as allow-once: it creates no grant. **Nothing is ever persisted** —
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
ignored. A late answer — one that finishes after the long-task notice, when nobody
may be watching — runs no desktop action at all unless `system_access = "full"`.

Legacy process bounds: each model call runs in its **own named systemd scope**
(`jarvis-model-<pid>-<n>.scope`), and authorized commands run inside it.
When the answer is delivered the scope is stopped, so nothing an authorized
command left in the background outlives the question. Cancelling the question
(barge-in, `q` in the window, dictation hotkey) stops that scope, denies every
open consent request and revokes the grant. Restarting
`voice-launcher.service` intentionally leaves already-launched scopes alive
(a long answer being followed in a scratch terminal), which is documented
behaviour; the grant does not survive (revoked at hand-off and at start), so
every further command in that scope needs a fresh `y`.

Legacy output bounds: the CLI and everything it runs are treated as an untrusted
producer. Its stdout (events JSONL) and stderr are **pipes** read by the
launcher, which writes them to the call's files only up to their ceilings —
32 MiB and 4 MiB (`pump()`). At the ceiling the launcher closes the pipe (the
producer gets EPIPE/SIGPIPE on its next write), **stops the whole scope**,
truncates the files and leaves the reason as the answer: nothing beyond the
ceiling ever reaches disk. The answer file, which the CLI writes itself, is
watched every 0.2 s (1 MiB) by a thread that lives until the process exits and
applies the same stop. That watchdog also gives a handed-off call a finite
total lifetime (`handoff_max_minutes`, default 30): when it expires the scope
is stopped. (An `RLIMIT_FSIZE` on the scope was tried and rejected: it also
applies to the CLI's own state databases, which grow past any sane cap, and
kills it on start.) Nothing the model wrote is ever read whole: event lines are consumed
only when complete and never beyond 1 MiB (a longer unterminated line stops
the scope), the answer and stderr are read up to their ceilings, and the
scratch-terminal follower uses the same bounded reader. The files live in
`$XDG_RUNTIME_DIR/jarvis-model/` (mode 0600, dir 0700) and are removed when the
call ends.

What an approval means: an authorized command runs with the user's full
privileges (there is no privilege separation between Jarvis and the user), so
the user must read the whole command before pressing `y`. The plugin's job is
to guarantee that what is shown is exactly what runs, that nothing runs
without that keystroke, and that no approval outlives the question.

**Personal context file.** `context_file` (default `~/.config/jarvis/contexto.md`,
in the user's own configuration directory) is appended to the system prompt only
when the model has computer access (`ask` or `full`), so its text goes to the
provider with every request. It is plain text for the model: it grants no tool,
changes no permission and is never executed. A missing file changes nothing.
Native Claude sessions read it from a private file; the legacy executor passes
the question, the recent history and the system prompt (this file included) on
the CLI command line, where other local users can see them in the process list
on systems without `hidepid`.

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
