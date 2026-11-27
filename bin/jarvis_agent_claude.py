#!/usr/bin/env python3
"""Claude native TUI in a private tmux session, with invocation-scoped hooks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import jarvis_config
import jarvis_consent
from jarvis_agent_policy import input_text, subscription_env, system_prompt
from jarvis_routing import RouteRequest, ExecutionProfile
import jarvis_sessions as sessions
from jarvis_sessions import SessionRef, SessionUnsupported, SessionBusy

MAX_EVENTS = 32 * 1024 * 1024
MAX_HOOK = 1024 * 1024


def capabilities() -> dict[str, bool]:
    available = bool(shutil.which('claude') and shutil.which('tmux'))
    if available:
        try:
            # The CLI can exit before flushing its help to a pipe. A regular
            # file avoids intermittently truncated capability detection.
            with tempfile.TemporaryFile(mode='w+') as output:
                subprocess.run(['claude', '--help'], stdout=output, stderr=subprocess.DEVNULL, timeout=5)
                output.seek(0)
                help_text = output.read()
            available = (all(flag in help_text for flag in ('--session-id', '--restricted', '--strict-mcp-config', '--settings',
                                                          '--bg', '--tools', '--permission-mode'))
                         and ('--append-system-prompt-file' in help_text or '--append-system-prompt[-file]' in help_text))
        except (OSError, subprocess.TimeoutExpired):
            available = False
    return dict.fromkeys(('start', 'final_answer', 'submit', 'attach', 'interrupt',
                          'access_off', 'access_ask', 'access_full'), available)


def tmux(session: SessionRef, *args: str, input_text: str | None = None) -> str:
    socket = sessions.read_record(session.id)['backend']['socket']
    result = subprocess.run(['tmux', '-S', socket, *args], input=input_text, capture_output=True,
                            text=True, timeout=5, env=subscription_env())
    if result.returncode:
        raise RuntimeError('claude_terminal_unavailable')
    return result.stdout


def claude_argv(session: SessionRef, profile: ExecutionProfile, settings: Path, context: Path,
                prompt: Path) -> list[str]:
    # The system prompt (personal context file included) goes in a private file, not argv,
    # where any local user could read it from the process list.
    argv = ['claude', '--session-id', session.native_id, '--model', profile.model,
            '--effort', profile.effort, '--settings', str(settings),
            '--append-system-prompt-file', str(prompt)]
    if profile.system_access == 'full':
        argv += ['--dangerously-skip-permissions']
    else:
        argv += ['--restricted', '--tools', '', '--strict-mcp-config', '--permission-mode', 'manual']
        servers = {}
        if profile.system_access == 'ask':
            servers['jarvis'] = {'type': 'stdio', 'command': sys.executable,
                                'args': [str(Path(__file__).resolve().with_name('jarvis_consent_mcp.py'))],
                                'env': {'JARVIS_CTX': str(context)}}
            argv += ['--allowedTools', 'mcp__jarvis__run']
        argv += ['--mcp-config', json.dumps({'mcpServers': servers})]
    return argv


PASTE_WRAPPER = re.compile(r'\s*<pasted_content id="([^"]+)">\n(.*)\n</pasted_content id="\1">\s*', re.DOTALL)


def unwrap_paste(prompt: str) -> str:
    # Claude Code 2.1.33x hands a long pasted input to hooks inside <pasted_content> tags.
    match = PASTE_WRAPPER.fullmatch(prompt)
    return match.group(2) if match else prompt


def prompt_ready(pane: str) -> bool:
    # The input line may be empty or show a dimmed placeholder ("❯ Try ...", Claude Code 2.1.33x).
    return re.search(r'^❯(?:\s|$)', pane, re.MULTILINE) is not None


def start(request: RouteRequest, profile: ExecutionProfile) -> SessionRef:
    session = sessions.new_session(request, profile)
    directory = sessions.session_dir(session.id)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    socket = runtime / ('jarvis-claude-' + session.id + '.sock')
    # Claude Code only trusts the home directory one interactive session at a time, so a
    # background session started from ~ is refused; Jarvis's own folder is trusted once.
    work = (request.cwd if profile.system_access == 'full' and request.cwd.resolve() != Path.home().resolve()
            else jarvis_consent.WORKDIR)
    work.mkdir(parents=True, exist_ok=True)
    session = sessions.update_backend(session, {'socket': str(socket), 'effective_cwd': str(work),
                                                'ready': False, 'running': False, 'current_turn': '', 'native_monitor': True})
    hook_command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--hook', session.id,
                              '--root', str(directory.parent)])
    events = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'StopFailure', 'SessionEnd')
    settings = directory / 'claude-settings.json'
    settings.write_text(json.dumps({'hooks': {event: [{'hooks': [{'type': 'command', 'command': hook_command}]}]
                                                   for event in events},
                                    'fastMode': profile.fast}))
    settings.chmod(0o600)
    context = directory / 'consent.json'
    context.write_text(json.dumps({'session_id': session.id, 'sessions_dir': str(directory.parent),
                                   'lang': jarvis_config.load()['language']}))
    context.chmod(0o600)
    prompt = directory / 'system-prompt.md'
    prompt.write_text(system_prompt(profile.system_access))
    prompt.chmod(0o600)
    argv = claude_argv(session, profile, settings, context, prompt)
    # --bg assigns its own native UUID. Resolve the printed background ID before
    # any prompt is sent; --resume on a running background session would fork it.
    index = argv.index('--session-id')
    del argv[index:index + 2]
    argv.append('--bg')
    try:
        created = subprocess.run(argv, cwd=work, env=subscription_env(), capture_output=True,
                                 text=True, timeout=15, check=True)
        match = re.search(r'backgrounded[^\n]*?([0-9a-f]{8})\b', created.stdout)
        if not match:
            raise ValueError('Missing background ID')
        background_id = match.group(1)
        active = subprocess.run(['claude', 'agents', '--json', '--cwd', str(work)],
                                env=subscription_env(), capture_output=True, text=True, timeout=5, check=True)
        candidates = [item for item in json.loads(active.stdout) if item.get('id') == background_id
                      and Path(item.get('cwd', '')).resolve() == work.resolve()]
        if len(candidates) != 1:
            raise ValueError('Ambiguous background session')
        native_id = candidates[0]['sessionId']
        uuid.UUID(native_id)
        session = sessions.update_backend(session, {'background_id': background_id}, native_id=native_id)
        command = ['tmux', '-S', str(socket), 'new-session', '-d', '-s', 'jarvis', '-c', str(work),
                   '-x', '120', '-y', '36', shlex.join(['claude', 'attach', background_id])]
        if shutil.which('systemd-run'):
            command = ['systemd-run', '--user', '--scope', '--quiet', '--collect',
                       '--unit', 'jarvis-session-' + session.id, '--', *command]
        subprocess.Popen(command, env=subscription_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = sessions.read_record(session.id)['backend']
            if state.get('ready') and socket.exists():
                try:
                    pane = tmux(session, 'capture-pane', '-p', '-t', 'jarvis')
                except RuntimeError:
                    pane = ''   # the tmux session is not up yet
                if prompt_ready(pane):
                    return session
            time.sleep(.1)
    except Exception:
        pass
    try:
        stop(session)
    except Exception:
        pass
    raise SessionUnsupported('claude_native_setup_required')


def submit(session: SessionRef, request: RouteRequest) -> str:
    if tmux(session, 'list-clients', '-t', 'jarvis', '-F', '#{client_tty}').strip():
        raise SessionBusy('terminal_owns_input')
    record = sessions.read_record(session.id)
    if record['backend'].get('running'):
        raise SessionBusy('agent_turn_running')
    if record['backend'].get('pending_request_id'):
        raise SessionBusy('previous_input_unconfirmed')
    text = 'User request:\n' + jarvis_consent.safe_text(input_text(request), keep_newlines=True)
    record['backend'].update(pending_request_id=request.request_id,
                             pending_text_sha256=hashlib.sha256(text.strip().encode()).hexdigest())
    sessions.write_record(session.id, record)
    buffer = 'jarvis-' + uuid.uuid4().hex
    tmux(session, 'load-buffer', '-b', buffer, '-', input_text=text)
    tmux(session, 'paste-buffer', '-d', '-p', '-b', buffer, '-t', 'jarvis')
    # Claude's bracketed-paste guard treats an immediate Enter as a newline.
    time.sleep(.6)
    tmux(session, 'send-keys', '-t', 'jarvis', 'Enter')
    return request.request_id


def attach_argv(session: SessionRef) -> list[str]:
    return ['tmux', '-S', sessions.read_record(session.id)['backend']['socket'], 'attach-session', '-t', 'jarvis']


def interrupt(session: SessionRef, turn_id: str) -> None:
    with sessions.locked(session.id):
        record = sessions.read_record(session.id)
        if (record['backend'].get('current_turn') != turn_id
                and record['backend'].get('pending_request_id') != turn_id):
            return
        record['backend']['running'] = False
        record['backend']['interrupted_turn'] = turn_id
        sessions.write_record(session.id, record)
    # Escape in an attached background TUI is not a reliable cancellation.
    # Native stop terminates the worker; attach later restores this exact ID.
    stop(session)
    append_event(session, turn_id, 'interrupted', '')


def ensure_connected(session: SessionRef) -> None:
    backend = sessions.read_record(session.id)['backend']
    if not backend.get('stopped'):
        return
    command = ['tmux', '-S', backend['socket'], 'new-session', '-d', '-s', 'jarvis',
               '-c', backend['effective_cwd'], '-x', '120', '-y', '36',
               shlex.join(['claude', 'attach', backend['background_id']])]
    if shutil.which('systemd-run'):
        command = ['systemd-run', '--user', '--scope', '--quiet', '--collect',
                   '--unit', 'jarvis-session-' + session.id, '--', *command]
    subprocess.Popen(command, env=subscription_env(), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = sessions.read_record(session.id)['backend']
        try:
            pane = tmux(session, 'capture-pane', '-p', '-t', 'jarvis') if state.get('ready') else ''
        except RuntimeError:
            pane = ''   # the tmux session is not up yet
        if prompt_ready(pane):
            sessions.update_backend(session, {'stopped': False})
            # its watchdog left when the session stopped; turns typed from now on need one again
            unit = 'jarvis-watch-' + session.id + '.service'
            if subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit]).returncode:
                sessions.start_watchdog(session)
            return
        time.sleep(.1)
    raise RuntimeError('claude_reconnect_failed')


def append_event(session: SessionRef, turn_id: str, kind: str, text: str, request_id: str = '') -> None:
    path = sessions.session_dir(session.id) / 'events.jsonl'
    if path.exists() and path.stat().st_size > MAX_EVENTS:
        raise RuntimeError('claude_event_limit')
    data = {'event_id': uuid.uuid4().hex, 'session_id': session.native_id, 'turn_id': turn_id,
            'kind': kind, 'text': text[:MAX_HOOK], 'request_id': request_id}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + '\n')


def tool_label(data: dict) -> str:
    # What the tool is doing, for the progress narration: the agent's own description when
    # there is one (Bash), else the command, file or query.
    name = str(data.get('tool_name', 'tool'))
    tool_input = data.get('tool_input')
    if not isinstance(tool_input, dict):
        return name
    for key in ('description', 'command', 'file_path', 'pattern', 'query', 'url'):
        detail = tool_input.get(key)
        if isinstance(detail, str) and detail.strip():
            return (name + ': ' + ' '.join(detail.split()))[:500]
    return name


def record_hook(session: SessionRef, data: dict) -> None:
    incoming = data.get('session_id')
    if not session.native_id and data.get('hook_event_name') == 'SessionStart':
        try:
            uuid.UUID(incoming)
        except (ValueError, TypeError, AttributeError):
            return
    elif incoming != session.native_id:
        return
    with sessions.locked(session.id):
        record = sessions.read_record(session.id)
        if not record['session']['native_id']:
            record['session']['native_id'] = incoming
        backend = record['backend']
        event = data.get('hook_event_name')
        turn_id = backend.get('current_turn', '')
        if event == 'SessionStart':
            backend['ready'] = True
        elif event == 'UserPromptSubmit':
            prompt = str(data.get('prompt', ''))
            digest = hashlib.sha256(unwrap_paste(prompt).strip().encode()).hexdigest()
            request_id = backend.get('pending_request_id', '') if digest == backend.get('pending_text_sha256') else ''
            turn_id = request_id or uuid.uuid4().hex
            backend.update(current_turn=turn_id, running=True, question=prompt, pending_request_id='',
                           pending_text_sha256='', started_at=time.time())
            append_event(session, turn_id, 'user', prompt, request_id)
        elif event in ('PreToolUse', 'PostToolUse') and turn_id:
            append_event(session, turn_id, 'tool', tool_label(data) if event == 'PreToolUse'
                         else str(data.get('tool_name', 'tool')))
        elif event in ('Stop', 'StopFailure') and turn_id:
            backend['running'] = False
            kind = 'result' if event == 'Stop' else 'failed'
            if backend.get('interrupted_turn') == turn_id:
                kind = 'interrupted'
            append_event(session, turn_id, kind, str(data.get('last_assistant_message', '')))
        elif event == 'SessionEnd':
            backend.update(ready=False, running=False)
        sessions.write_record(session.id, record)


def events(session: SessionRef, after_event_id: str | None) -> list[dict]:
    path = sessions.session_dir(session.id) / 'events.jsonl'
    if not path.exists():
        return []
    if path.stat().st_size > MAX_EVENTS:
        raise RuntimeError('claude_event_limit')
    result = []
    with path.open() as stream:
        for line in stream:
            if len(line) > MAX_HOOK * 2:
                raise RuntimeError('claude_event_limit')
            if line.endswith('\n'):
                result.append(json.loads(line))
    return result


def find_turn(session: SessionRef, request_id: str) -> str | None:
    for event in events(session, None):
        if event.get('request_id') == request_id:
            return event['turn_id']
    return None


def consent_context(session: SessionRef) -> dict:
    backend = sessions.read_record(session.id)['backend']
    if not backend.get('running') or not backend.get('current_turn'):
        return {}
    return {'call_id': session.id + '-' + backend['current_turn'], 'question': backend.get('question', '')}


def active_turns(session: SessionRef) -> list[tuple[str, float]]:
    backend = sessions.read_record(session.id)['backend']
    if backend.get('running') and backend.get('current_turn'):
        return [(backend['current_turn'], float(backend['started_at']))]
    return []


def alive(session: SessionRef) -> bool:
    try:
        tmux(session, 'has-session', '-t', 'jarvis')
        return True
    except Exception:
        return False


def stop(session: SessionRef) -> None:
    backend = sessions.read_record(session.id)['backend']
    failed = False
    if backend.get('background_id'):
        try:
            result = subprocess.run(['claude', 'stop', backend['background_id']], capture_output=True, timeout=8,
                                    env=subscription_env())
            failed = result.returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            failed = True
    try:
        tmux(session, 'kill-server')
    except Exception:
        pass
    sessions.update_backend(session, {'stopped': True, 'ready': False, 'running': False,
                                      'pending_request_id': '', 'pending_text_sha256': ''})
    # A worker that already exited makes `claude stop` fail too: only one still listed is an error.
    if failed and background_listed(backend):
        raise RuntimeError('claude_stop_failed')


def background_listed(backend: dict) -> bool:
    try:
        listed = subprocess.run(['claude', 'agents', '--json', '--cwd', backend['effective_cwd']],
                                env=subscription_env(), capture_output=True, text=True, timeout=5, check=True)
        return any(item.get('id') == backend['background_id'] for item in json.loads(listed.stdout))
    except Exception:
        return True   # unknown: report the failure


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--hook', required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    sessions.SESSIONS_DIR = args.root
    raw = sys.stdin.buffer.read(MAX_HOOK + 1)
    if len(raw) <= MAX_HOOK:
        record_hook(sessions.load_session(args.hook), json.loads(raw))


if __name__ == '__main__':
    main()
