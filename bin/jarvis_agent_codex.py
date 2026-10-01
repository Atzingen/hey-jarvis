"""Codex native app-server sessions over a private Unix WebSocket."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import jarvis_config
from jarvis_agent_policy import CODEX_DISABLED_FEATURES, input_text, subscription_env, system_prompt
from jarvis_routing import RouteRequest, ExecutionProfile
from jarvis_sessions import (SessionRef, SessionUnsupported, SessionBusy, new_session, update_backend,
                             read_record, session_dir)


class CodexRpcError(RuntimeError):
    def __init__(self, method: str, error: dict) -> None:
        super().__init__('codex_rpc_rejected:' + method)
        self.code = error.get('code')
        self.not_materialized = (self.code == -32600 and
                                 'is not materialized yet' in str(error.get('message', '')))


def capabilities() -> dict[str, bool]:
    available = bool(shutil.which('codex') and importlib.util.find_spec('websockets'))
    if available:
        try:
            help_text = subprocess.run(['codex', '--help'], capture_output=True, text=True, timeout=5).stdout
            available = '--remote' in help_text and 'app-server' in help_text
        except (OSError, subprocess.TimeoutExpired):
            available = False
    return dict.fromkeys(('start', 'final_answer', 'submit', 'attach', 'interrupt',
                          'access_off', 'access_ask', 'access_full'), available)


def policy_config(profile: ExecutionProfile, context: Path) -> dict:
    if profile.system_access == 'full':
        return {}
    config = {'features': {key: False for key in CODEX_DISABLED_FEATURES},
              'include_apply_patch_tool': False, 'web_search': 'disabled', 'project_doc_max_bytes': 0,
              'agents': {'max_depth': 0}, 'mcp_servers': {}}
    if profile.system_access == 'ask':
        config['mcp_servers']['jarvis'] = {
            'command': sys.executable, 'args': [str(Path(__file__).resolve().with_name('jarvis_consent_mcp.py'))],
            'env': {'JARVIS_CTX': str(context)}, 'default_tools_approval_mode': 'approve', 'required': True}
    return config


def server_argv(socket: Path, config: dict) -> list[str]:
    argv = ['codex', 'app-server', '--listen', 'unix://' + str(socket)]
    for key, value in config.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                if isinstance(subvalue, dict):
                    for child, leaf in subvalue.items():
                        if isinstance(leaf, dict):
                            encoded = '{' + ','.join(f'{k}={json.dumps(v)}' for k, v in leaf.items()) + '}'
                        else:
                            encoded = json.dumps(leaf)
                        argv += ['-c', f'{key}.{subkey}.{child}={encoded}']
                else:
                    argv += ['-c', f'{key}.{subkey}={json.dumps(subvalue)}']
        else:
            argv += ['-c', f'{key}={json.dumps(value)}']
    return argv


def launch_server(session: SessionRef) -> None:
    backend = read_record(session.id)['backend']
    env = subscription_env()
    if backend.get('codex_home'):
        env['CODEX_HOME'] = backend['codex_home']
    command = backend['argv']
    if shutil.which('systemd-run'):
        command = ['systemd-run', '--user', '--scope', '--quiet', '--collect',
                   '--unit', 'jarvis-session-' + session.id, '--', *command]
    subprocess.Popen(command, env=env, cwd=backend['effective_cwd'], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if Path(backend['socket']).exists():
            return
        time.sleep(.1)
    raise SessionUnsupported('codex_server_unavailable')


def rpc(session: SessionRef, method: str, params: dict, timeout: float = 10) -> dict:
    from websockets.sync.client import unix_connect
    backend = read_record(session.id)['backend']
    with unix_connect(backend['socket'], open_timeout=timeout, max_size=4 * 1024 * 1024,
                      close_timeout=1) as connection:
        def receive(identifier: str) -> dict:
            deadline = time.monotonic() + timeout
            while True:
                data = json.loads(connection.recv(timeout=max(.01, deadline - time.monotonic())))
                if data.get('id') == identifier:
                    if 'error' in data:
                        raise CodexRpcError(method, data['error'])
                    return data['result']
                if 'id' in data and 'method' in data:
                    connection.send(json.dumps({'id': data['id'], 'error': {'code': -32601, 'message': 'Unsupported request'}}))
        init_id = uuid.uuid4().hex
        connection.send(json.dumps({'id': init_id, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'jarvis', 'version': '1'}, 'capabilities': {'experimentalApi': True}}}))
        receive(init_id)
        connection.send(json.dumps({'method': 'initialized', 'params': {}}))
        identifier = uuid.uuid4().hex
        connection.send(json.dumps({'id': identifier, 'method': method, 'params': params}))
        return receive(identifier)


def start(request: RouteRequest, profile: ExecutionProfile) -> SessionRef:
    session = new_session(request, profile)
    directory = session_dir(session.id)
    work = request.cwd
    codex_home = None
    if profile.system_access != 'full':
        work = directory / 'workdir'
        work.mkdir(mode=0o700)
        home = directory / 'codex-home'
        home.mkdir(mode=0o700)
        auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
        if not auth.is_file():
            raise SessionUnsupported('codex_subscription_auth_unavailable')
        (home / 'auth.json').symlink_to(auth)
        codex_home = str(home)
    context = directory / 'consent.json'
    context.write_text(json.dumps({'session_id': session.id, 'sessions_dir': str(directory.parent),
                                   'lang': jarvis_config.load()['language']}))
    context.chmod(0o600)
    config = policy_config(profile, context)
    socket = directory / 'codex.sock'
    session = update_backend(session, {'socket': str(socket), 'codex_home': codex_home,
                                       'effective_cwd': str(work), 'argv': server_argv(socket, config), 'native_monitor': True})
    try:
        launch_server(session)
        account = rpc(session, 'account/read', {'refreshToken': False})
        if (account.get('account') or {}).get('type') != 'chatgpt':
            raise SessionUnsupported('codex_subscription_login_required')
        params = {'cwd': str(work), 'approvalPolicy': 'never',
                  'sandbox': 'danger-full-access' if profile.system_access == 'full' else 'read-only',
                  'baseInstructions': system_prompt(profile.system_access), 'ephemeral': False, 'historyMode': 'legacy',
                  'experimentalRawEvents': False, 'serviceTier': 'fast' if profile.fast else 'default',
                  'config': config}
        if profile.model:
            params['model'] = profile.model
        result = rpc(session, 'thread/start', params)
        native_id = result['thread']['id']
        if not isinstance(native_id, str) or not native_id:
            raise ValueError('No thread ID')
        session = update_backend(session, {}, native_id=native_id)
        thread(session)  # Required event interface, checked before any user input.
        return session
    except SessionUnsupported:
        stop(session)
        raise
    except Exception:
        stop(session)
        raise SessionUnsupported('codex_session_setup_failed') from None


def thread(session: SessionRef) -> dict:
    try:
        page = rpc(session, 'thread/turns/list', {'threadId': session.native_id, 'limit': 8,
                                                    'sortDirection': 'desc', 'itemsView': 'full'})
    except CodexRpcError as exc:
        if exc.not_materialized:
            return {'turns': []}
        raise
    return {'turns': list(reversed(page['data']))}


def submit(session: SessionRef, request: RouteRequest) -> str:
    state = rpc(session, 'thread/read', {'threadId': session.native_id, 'includeTurns': False})['thread']
    if state.get('status', {}).get('type') == 'active' or any(
            turn.get('status') == 'inProgress' for turn in state.get('turns', [])):
        raise SessionBusy('agent_turn_running')
    profile = read_record(session.id)['profile']
    params = {'threadId': session.native_id, 'clientUserMessageId': request.request_id,
              'input': [{'type': 'text', 'text': input_text(request), 'text_elements': []}],
              'effort': profile['effort'], 'serviceTier': 'fast' if profile['fast'] else 'default'}
    if profile['model']:
        params['model'] = profile['model']
    return rpc(session, 'turn/start', params)['turn']['id']


def find_turn(session: SessionRef, request_id: str) -> str | None:
    for turn in thread(session).get('turns', []):
        if any(item.get('type') == 'userMessage' and item.get('clientId') == request_id for item in turn.get('items', [])):
            return turn['id']
    return None


def attach_argv(session: SessionRef) -> list[str]:
    return ['codex', '--remote', 'unix://' + read_record(session.id)['backend']['socket'], 'resume', session.native_id]


def interrupt(session: SessionRef, turn_id: str) -> None:
    rpc(session, 'turn/interrupt', {'threadId': session.native_id, 'turnId': turn_id})


def events(session: SessionRef, after_event_id: str | None) -> list[dict]:
    result = []
    for turn in thread(session).get('turns', []):
        tid = turn['id']
        final = []
        for index, item in enumerate(turn.get('items', [])):
            kind, text = '', ''
            if item.get('type') == 'agentMessage':
                text = item.get('text', '')
                if item.get('phase') == 'final_answer':
                    final.append(text)
                else:
                    kind = 'thinking'
            elif item.get('type') in ('commandExecution', 'mcpToolCall', 'fileChange'):
                kind = 'tool'
                text = str(item.get('command') or item.get('tool') or item.get('type'))[:500]
            if kind and text:
                result.append({'event_id': f'{tid}:{index}:{kind}', 'session_id': session.native_id,
                               'turn_id': tid, 'kind': kind, 'text': text})
        status = turn.get('status')
        if status in ('completed', 'interrupted', 'failed'):
            result.append({'event_id': tid + ':' + status, 'session_id': session.native_id, 'turn_id': tid,
                           'kind': 'result' if status == 'completed' else status, 'text': '\n'.join(final)})
    return result


def consent_context(session: SessionRef) -> dict:
    for turn in reversed(thread(session).get('turns', [])):
        if turn.get('status') == 'inProgress':
            questions = [block.get('text', '') for item in turn.get('items', [])
                         if item.get('type') == 'userMessage' for block in item.get('content', [])
                         if block.get('type') == 'text']
            return {'call_id': session.id + '-' + turn['id'], 'question': '\n'.join(questions)}
    return {}


def active_turns(session: SessionRef) -> list[tuple[str, float]]:
    return [(turn['id'], float(turn.get('startedAt') or time.time()))
            for turn in thread(session).get('turns', []) if turn.get('status') == 'inProgress']


def alive(session: SessionRef) -> bool:
    try:
        rpc(session, 'thread/read', {'threadId': session.native_id, 'includeTurns': False}, timeout=2)
        return True
    except Exception:
        return False


def stop(session: SessionRef) -> None:
    unit = 'jarvis-session-' + session.id + '.scope'
    try:
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    except subprocess.TimeoutExpired:
        subprocess.run(['systemctl', '--user', 'kill', '--kill-whom=all', '--signal=SIGKILL', unit],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
