# Changelog

All notable changes to Jarvis. The version is the `version` field of `manifest.json`; the
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the numbering is
explained in [RELEASING.md](RELEASING.md).

## [Unreleased]

### Fixed

- **Picoh reconnects without replugging**: when the robot's Pico does not answer the first
  handshake (still booting, port busy), Jarvis tries it again every 30 s for up to 5 minutes;
  before, the robot stayed dark until it was unplugged. Other serial boards are still opened
  only once, because opening the port can reset them.

## [2.5.0] — 2026-11-27

### Added

- **Optional routing** (`routing_mode`): `agent` stays the default and the fallback;
  `assistant` (an initial answers-API model), `jev` (TypeSafe Jev classifier) and `local`
  (an experimental CPU classifier, installed separately with `jarvis router install-local`).
  API answers require an explicit `api_provider = "openai"` plus a model. Each answer shows
  the model and the route that handled it.
- **Persistent native agent sessions**: Codex app-server and Claude background sessions over a
  private tmux socket, attachable in a real terminal without resending the request
  (`jarvis session status | open | stop`). `agent_session_mode = "legacy"` keeps the previous
  executor.
- **Local Nemotron speech recognition** (optional, `jarvis stt install-nemotron`): streaming
  NVIDIA Nemotron 3.5 ASR on CPU, CUDA or Metal, with provisional text while you speak.
- **Write while dictating** (`dictation_live`): confirmed phrases are inserted into the active
  window at each pause, with modifier-key and focus guards.
- **`Esc` skips the dictation polish** and delivers the raw transcript (`jarvis dictate skip`).
- **Personal vocabulary** for speech recognition (`stt_vocabulary`, `stt_vocabulary_enabled`,
  `nemotron_vocabulary_boost`): Nemotron `speech_contexts`, OpenAI `keywords`, Whisper
  `hotwords`.
- **Personal context file** (`context_file`, default `~/.config/jarvis/contexto.md`), appended
  to the system prompt when the model has computer access.
- **Claude fast mode** (`claude_fast`), like the existing `codex_fast`.
- `narration_first_seconds`: the first progress sentence comes as soon as the model starts
  acting; in Claude sessions it is written from the tool's own description.
- `agent_session_max_hours`: native sessions are renewed after this age, and also when the
  system prompt or the context file change.
- Documentation: English `README.md` again (Portuguese in `README.pt-BR.md`), this changelog,
  `RELEASING.md`, and the missing settings in `docs/reference.en.md`.
- README GIFs of a question and its answer and of the settings screen (English and Portuguese),
  a new marketplace preview, and refreshed screenshots of the settings screen and the panel.

### Changed

- “Think hard” / “pense bem” are ordinary text: they no longer switch to the deep model. The
  `deep_model`, `deep_effort`, `handoff_seconds_deep` and `narration_interval_deep` keys are
  still read so a rollback works, but are no longer active.
- An answer that arrives after the long-task notice is spoken at the next pause instead of
  only appearing in the window.
- Picoh: eye brightness is capped in a low band in every phase; listening, recording and
  dictation use the “glasses” eyes.

### Fixed

- Codex: an unloaded conversation is resumed before a request is sent, and every Codex turn
  keeps the access mode Jarvis configured.
- Claude Code 2.1.33x: native sessions open again (new prompt placeholder; a background
  session no longer starts in the home folder — Jarvis's work folder has to be trusted once).
- Claude Code 2.1.33x: long follow-ups are recognized again (pasted-content wrapper in hooks).
- The settings screen in English showed some labels in Portuguese (form of address, window
  style, music ducking, narration) and said the Jev key falls back to `OPENAI_API_KEY`; it is
  `JEV_API_KEY`. Long Portuguese labels no longer get cut.
- The panel's voice guide no longer says the dev layout uses Ghostty.
- Bar icon, panel and conversation window colors with Qt 6.12: a bare `Color` now resolved to
  QtQuick's own type, so every themed color came out undefined (black icon in the bar, colorless
  `jarvis app`). The shared QML now uses the qualified `Commons.Color`.
- Speech longer than 60 seconds is no longer cut off (the playback ceiling follows the audio length).
- A native session whose Codex app-server or Claude terminal died is replaced at the next
  request instead of failing every following one; Codex turns without a start time no longer
  escape the turn deadline; watchdog units stop with their session instead of running until reboot.
- Write while dictating decides the target window and the paste shortcut from one focus query, so
  a focus change in between can no longer paste into another window.
- An OpenAI final transcript in an unexpected shape falls back to local Whisper instead of
  dropping the speech.
- The voice service no longer turns off the restricted Claude path until its next restart when
  `claude --help` is read incompletely at boot; `jarvis` without a known command lists every command.

### Security

- In `ask` and `off`, Codex is refused when `/etc/codex/config.toml` defines MCP servers: Codex
  merges that file into every run, and its tools would bypass the consent window.
- "Allow the rest of this question" now ends for native sessions as it did for the legacy viewer:
  when the answer arrives, on cancel, and whenever a terminal is opened on the session; a decision
  still pending at that moment allows only once.
- An answer that arrives after the long-task notice runs no desktop action unless
  `system_access = "full"`.
- Authenticated API calls (answers API, Jev, OpenAI narration) refuse redirects, so the key never
  reaches another URL.
- `config.toml` and profiles are written through a temporary file created `0600` (the key was
  briefly readable with a `022` umask); the native Claude system prompt, personal context
  included, and the Nemotron bearer token no longer appear on any command line; `JEV_API_KEY`
  is not passed to the agent CLIs (native or legacy); a failed clipboard or paste no longer writes
  the dictated text to the journal.
- `jarvis router install-local` downloads a fixed list of model files, each checked against its
  size and SHA-256.
- `SECURITY.md` describes the native and legacy invocations separately and matches the code on
  endpoints, deadlines, limits and uninstall.

## [2.4.0] — 2026-09-22

### Added

- Graphic conversation window with Jarvis's face and a ring that pulses with the voice
  (Quickshell on Omarchy, PySide6 elsewhere, terminal fallback; `window_style`).
- Picoh robot as Jarvis's face (optional). The udev rule is installed only after an explicit
  confirmation.
- Bar widget: tooltip on hover and a panel on click; switches respond instantly.
- `address` setting for how Jarvis addresses you; Codex model list in the settings.

### Changed

- Dictation shows a tall mirrored waveform instead of a one-line meter.

### Security

- Ceilings on the OpenAI Realtime WebSocket (fail-closed to local Whisper), on model output
  and on installer downloads (marketplace review).

### Fixed

- Shipped to `stable` on 2026-09-23 without a version bump: model output now goes through
  bounded pipes. The previous `prlimit --fsize` killed Codex at start whenever its own state
  databases were larger than the limit.
- Conversation window border visible on all four sides with fractional scaling.

## [2.3.0] — 2026-09-04

### Added

- Progress narration while the model works (local Ollama, OpenAI, the model itself, or fixed
  phrases).

### Changed

- `ask` mode gives the model CLI no built-in tools and none of the user's configuration; its
  only tool is the consent `run(command)`.
- Python dependencies are hash-locked and installed into a dedicated venv; wake-word and
  Whisper models are pinned.

### Fixed

- Long dictations no longer lose speech (the Whisper prompt overflowed 448 tokens).

## [2.2.0] — 2026-09-02

### Added

- Hotkeys-only mode: wake-word listening can be turned off with the microphone fully closed.
- Dictation lowers other audio while recording (toggle only).
- `SECURITY.md`, pinned Piper voices, runtime state moved out of `/tmp`.

### Changed

- `system_access` is `ask` (per-command consent) by default; `full` is opt-in; model calls run
  in named systemd scopes.
- Terminals use Alacritty while Ghostty crashes with Claude Code (ghostty#11504).
- `voice-launcher.service` starts with the graphical session.

## [2.1.0] — 2026-08-30

### Added

- Card-based bar panel, shared with a standalone `jarvis app` window.
- Dictation: `Ctrl+Shift+K` speech-to-text into the active window.
- `install.sh --uninstall [--purge]`, third-party licenses and the marketplace preview.

## [2.0.0] — 2026-08-30

### Added

- Natural conversation with follow-up and barge-in; the model decides intents through an
  action protocol; Omarchy shell plugin.

[Unreleased]: https://github.com/Atzingen/hey-jarvis/compare/v2.5.0...main
[2.5.0]: https://github.com/Atzingen/hey-jarvis/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/Atzingen/hey-jarvis/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/Atzingen/hey-jarvis/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/Atzingen/hey-jarvis/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/Atzingen/hey-jarvis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Atzingen/hey-jarvis/releases/tag/v2.0.0
