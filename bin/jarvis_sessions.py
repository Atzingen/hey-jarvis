#!/usr/bin/env python3
"""Private native sessions, explicit identities and at-most-once submission."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Iterator
import uuid

from jarvis_routing import ExecutionProfile, RouteRequest, RouterCancelled

SESSIONS_DIR = Path.home() / '.local/share/jarvis/sessions'
REQUIRED_CAPABILITIES = ('start', 'final_answer', 'submit', 'attach', 'interrupt')


@dataclass(frozen=True)
class SessionRef:
    id: str
    conversation_id: str
    provider: str
    native_id: str
    cwd: Path
    system_access: str


class SessionUnsupported(Exception):
    """No user request has been submitted. The legacy executor is safe."""


class SessionMismatch(Exception):
    """The caller tried to reuse another conversation, project or policy."""


class SessionBusy(Exception):
    """No submission: input belongs to another turn or the attached keyboard."""


class SubmissionUnknown(Exception):
    def __init__(self, session: SessionRef, message: str = 'submission_unknown') -> None:
        super().__init__(message)
        self.session = session


def session_dir(identifier: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid Jarvis session ID')
    return SESSIONS_DIR / identifier


def write_record(identifier: str, record: dict) -> None:
    directory = session_dir(identifier)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='.state-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, directory / 'session.json')
    finally:
        Path(path).unlink(missing_ok=True)


def read_record(identifier: str) -> dict:
    path = session_dir(identifier) / 'session.json'
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Session record too large')
    return json.loads(path.read_text())


@contextmanager
def locked(identifier: str) -> Iterator[None]:
    directory = session_dir(identifier)
    fd = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def new_session(request: RouteRequest, profile: ExecutionProfile, native_id: str = '') -> SessionRef:
    SESSIONS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SESSIONS_DIR, 0o700)
    session = SessionRef(uuid.uuid4().hex, request.conversation_id, profile.provider, native_id,
                         request.cwd.resolve(), profile.system_access)
    data = asdict(session)
    data['cwd'] = str(session.cwd)
    write_record(session.id, {'session': data, 'profile': asdict(profile), 'requests': {},
                             'seen_events': [], 'completed_turns': {}, 'state': 'idle', 'created_at': time.time(), 'backend': {}})
    return session


def update_backend(session: SessionRef, values: dict, native_id: str | None = None) -> SessionRef:
    with locked(session.id):
        record = read_record(session.id)
        record['backend'].update(values)
        if native_id is not None:
            record['session']['native_id'] = native_id
        write_record(session.id, record)
    return load_session(session.id)


def load_session(identifier: str) -> SessionRef:
    data = read_record(identifier)['session']
    return SessionRef(**(data | {'cwd': Path(data['cwd'])}))


def adapter_for(provider: str):
    if provider not in ('codex', 'claude'):
        raise SessionUnsupported('unknown_agent_provider')
    return importlib.import_module('jarvis_agent_' + provider)


def start_session(request: RouteRequest, profile: ExecutionProfile, cancel: threading.Event | None = None) -> SessionRef:
    if cancel is not None and cancel.is_set():
        raise RouterCancelled()
    adapter = adapter_for(profile.provider)
    caps = adapter.capabilities()
    if not all(caps.get(key, False) for key in (*REQUIRED_CAPABILITIES, 'access_' + profile.system_access)):
        raise SessionUnsupported('native_capabilities_missing')
    session = adapter.start(request, profile)
    if cancel is not None and cancel.is_set():
        adapter.stop(session)
        raise RouterCancelled()
    try:
        start_watchdog(session)
    except Exception:
        adapter.stop(session)
        raise SessionUnsupported('native_watchdog_unavailable') from None
    if cancel is not None and cancel.is_set():
        adapter.stop(session)
        raise RouterCancelled()
    submit_turn(session, request)
    return session


def submit_turn(session: SessionRef, request: RouteRequest) -> str:
    if session.conversation_id != request.conversation_id or session.cwd != request.cwd.resolve():
        raise SessionMismatch('session_context_mismatch')
    adapter = adapter_for(session.provider)
    with locked(session.id):
        record = read_record(session.id)
        if record['session']['system_access'] != session.system_access:
            raise SessionMismatch('session_policy_mismatch')
        digest = hashlib.sha256(request.text.encode()).hexdigest()
        previous = record['requests'].get(request.request_id)
        if previous:
            if previous['text_sha256'] != digest:
                raise SessionMismatch('request_id_reused')
            if previous.get('turn_id'):
                return previous['turn_id']
            if previous['state'] in ('submitting', 'unknown'):
                recovered = adapter.find_turn(session, request.request_id)
                if recovered:
                    previous.update(state='submitted', turn_id=recovered)
                    write_record(session.id, record)
                    return recovered
                raise SubmissionUnknown(session)
        entry = {'state': 'submitting', 'text_sha256': digest, 'submitted_at': time.time()}
        record['requests'][request.request_id] = entry
        record['state'] = 'submitting'
        write_record(session.id, record)
        try:
            turn_id = adapter.submit(session, request)
            if not isinstance(turn_id, str) or not turn_id:
                raise ValueError('Missing turn ID')
        except SessionBusy:
            entry['state'] = 'queued'
            record['state'] = 'queued'
            write_record(session.id, record)
            raise
        except Exception:
            entry['state'] = 'unknown'
            record['state'] = 'unknown'
            write_record(session.id, record)
            raise SubmissionUnknown(session) from None
        record = read_record(session.id)
        entry = record['requests'][request.request_id]
        entry.update(state='submitted', turn_id=turn_id)
        record['state'] = 'running'
        write_record(session.id, record)
        return turn_id


def poll_events(session: SessionRef) -> list[dict]:
    events = adapter_for(session.provider).events(session, None)
    with locked(session.id):
        record = read_record(session.id)
        seen = set(record['seen_events'])
        fresh = []
        for event in events:
            if (not isinstance(event, dict)
                    or any(not isinstance(event.get(key), str) for key in ('event_id', 'session_id', 'turn_id', 'kind', 'text'))
                    or event['session_id'] != session.native_id or event['event_id'] in seen):
                continue
            seen.add(event['event_id'])
            event = event | {'text': event['text'][:1_000_000]}
            fresh.append(event)
            record['seen_events'].append(event['event_id'])
            if event['kind'] in ('result', 'interrupted', 'failed'):
                record['state'] = 'completed' if event['kind'] == 'result' else event['kind']
                record.setdefault('completed_turns', {})[event['turn_id']] = event
        # Bound the persisted journal independently of CLI transcript size.
        record['seen_events'] = record['seen_events'][-2048:]
        completed = record['completed_turns']
        while len(json.dumps(completed)) > 2_000_000 and len(completed) > 1:
            del completed[next(iter(completed))]
        write_record(session.id, record)
    return fresh


def interrupt_turn(session: SessionRef, turn_id: str) -> None:
    adapter_for(session.provider).interrupt(session, turn_id)


def open_terminal(session: SessionRef) -> None:
    adapter = adapter_for(session.provider)
    if hasattr(adapter, 'ensure_connected'):
        adapter.ensure_connected(session)
    argv = adapter.attach_argv(session)
    if not shutil.which('alacritty'):
        raise SessionUnsupported('terminal_not_installed')
    subprocess.Popen(['alacritty', '--title', 'Jarvis · ' + session.provider,
                      '--working-directory', str(session.cwd), '-e', *argv],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    with locked(session.id):
        record = read_record(session.id)
        record['state'] = 'handed_off'
        write_record(session.id, record)


def enforce_deadlines(session: SessionRef, maximum: float, now: float | None = None) -> list[str]:
    adapter = adapter_for(session.provider)
    now = time.time() if now is None else now
    expired = []
    for turn_id, started_at in adapter.active_turns(session):
        if now - started_at > maximum:
            adapter.interrupt(session, turn_id)
            expired.append(turn_id)
    return expired


def start_watchdog(session: SessionRef) -> None:
    if not read_record(session.id)['backend'].get('native_monitor'):
        return
    if not shutil.which('systemd-run'):
        raise SessionUnsupported('native_watchdog_unavailable')
    import jarvis_config
    maximum = float(jarvis_config.load().get('handoff_max_minutes', 30)) * 60
    result = subprocess.run(['systemd-run', '--user', '--collect', '--quiet',
                             '--unit', 'jarvis-watch-' + session.id,
                             '--property=Type=exec', sys.executable, str(Path(__file__).resolve()),
                             'watch', session.id, '--root', str(SESSIONS_DIR), '--max-seconds', str(maximum)],
                            capture_output=True, timeout=8)
    if result.returncode:
        raise SessionUnsupported('native_watchdog_unavailable')


def watch_session(session: SessionRef, maximum: float) -> None:
    adapter = adapter_for(session.provider)
    failures = 0
    last_success = time.monotonic()
    while True:
        try:
            expired = enforce_deadlines(session, maximum)
            failures = 0
            last_success = time.monotonic()
            if expired:
                time.sleep(.5)
                remaining = {turn for turn, _ in adapter.active_turns(session)}
                if remaining.intersection(expired):
                    adapter.stop(session)
                    return
        except Exception:
            failures += 1
            if failures >= 3:
                if not adapter.alive(session) or time.monotonic() - last_success > maximum:
                    adapter.stop(session)
                    return
        time.sleep(.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['open', 'status', 'stop', 'watch'])
    parser.add_argument('session_id')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--max-seconds', type=float, default=1800)
    args = parser.parse_args()
    global SESSIONS_DIR
    if args.root:
        SESSIONS_DIR = args.root
    session = load_session(args.session_id)
    if args.action == 'open':
        open_terminal(session)
    elif args.action == 'watch':
        watch_session(session, args.max_seconds)
    elif args.action == 'stop':
        adapter_for(session.provider).stop(session)
        subprocess.run(['systemctl', '--user', 'stop', 'jarvis-watch-' + session.id + '.service'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        from jarvis_conversation_routing import ACTIVE_FILE, forget_active_session
        try:
            if json.loads(ACTIVE_FILE.read_text()).get('session_id') == session.id:
                forget_active_session()
        except (OSError, ValueError):
            pass
    else:
        record = read_record(session.id)
        print(json.dumps({'id': session.id, 'provider': session.provider, 'native_id': session.native_id,
                          'cwd': str(session.cwd), 'state': record['state'], 'system_access': session.system_access}))


if __name__ == '__main__':
    # Adapters import this module too; share the CLI's selected session root.
    sys.modules['jarvis_sessions'] = sys.modules[__name__]
    main()
