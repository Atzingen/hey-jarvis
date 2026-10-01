"""Conversation dispatch. Optional classifiers never replace the direct agent path."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable
import uuid

import jarvis_sessions as sessions
from jarvis_i18n import T
from jarvis_routing import (RouteRequest, RouteDecision, ExecutionProfile, RouterUnavailable, RouterCancelled,
                            ExecutionUncertain, build_profiles, choose_route, clear_continuation, resolve_profile)

ACTIVE_FILE = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'jarvis-active-session.json'


@dataclass(frozen=True)
class Answer:
    text: str
    profile: ExecutionProfile
    route: str
    reason: str = ''
    backend: str = ''
    allow_actions: bool = False
    handoff: dict | None = None


def build_backend(mode: str, cfg: dict, cancel: threading.Event, on_event: Callable):
    if mode == 'assistant':
        from jarvis_router_api import build_api_router
        return build_api_router(cfg, cancel, on_event)
    if mode == 'jev':
        from jarvis_router_jev import build_jev_router
        return build_jev_router(cfg, cancel)
    if mode == 'local':
        from jarvis_router_local import build_local_router
        return build_local_router(cfg, cancel)
    raise RouterUnavailable('router_unavailable')


def remember_session(session: sessions.SessionRef) -> None:
    temporary = ACTIVE_FILE.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'session_id': session.id}, stream)
    os.replace(temporary, ACTIVE_FILE)


def restore_active_session(cfg: dict, cwd: Path) -> sessions.SessionRef | None:
    if cfg.get('agent_session_mode', 'auto') == 'legacy':
        return None
    try:
        session = sessions.load_session(json.loads(ACTIVE_FILE.read_text())['session_id'])
        dev = Path(cfg.get('dev_dir', str(cwd))).expanduser().resolve()
        known_project = (session.cwd.parent == dev and session.cwd.is_dir()
                         and not session.cwd.is_symlink() and not session.cwd.name.startswith('.'))
        if (session.provider == cfg['quick_provider'] and session.system_access == cfg['system_access']
                and (session.cwd == cwd.resolve() or known_project)):
            return session
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def forget_active_session() -> None:
    ACTIVE_FILE.unlink(missing_ok=True)


def profile_label(profile: ExecutionProfile) -> str:
    label = profile.provider + ' ' + (profile.model or 'default')
    if profile.effort:
        label += '/' + profile.effort
    if profile.fast:
        label += '/fast'
    return label


def router_label(cfg: dict) -> str:
    lang = cfg.get('language', 'en')
    return {'jev': 'Jev', 'assistant': T(lang, 'route_initial_model'),
            'local': T(lang, 'route_local_classifier')}.get(cfg.get('routing_mode'), T(lang, 'route_direct'))


def route_failure_label(reason: str, lang: str) -> str:
    if re.fullmatch(r'http_\d{3}', reason):
        return 'HTTP ' + reason[5:]
    if reason in ('jev_key_missing', 'api_key_missing'):
        return T(lang, 'route_key_missing')
    if reason == 'api_not_configured':
        return T(lang, 'route_api_unconfigured')
    if reason == 'api_web_search_disabled':
        return T(lang, 'route_web_disabled')
    return T(lang, 'route_unavailable')


def routing_summary(cfg: dict, decision: RouteDecision, profile: ExecutionProfile) -> str:
    lang = cfg.get('language', 'en')
    target = 'API' if profile.transport == 'api' else T(lang, 'route_agent')
    if decision.reason == 'subscription_default':
        return T(lang, 'route_direct') + ' → ' + target
    if decision.reason == 'active_session':
        return T(lang, 'route_same_session') + ' → ' + target
    source = router_label(cfg)
    normal = ('classified_api', 'classified_web', 'classified_agent', 'assistant_answer', 'assistant_handoff')
    if decision.reason not in normal:
        return source + ' · ' + route_failure_label(decision.reason, lang) + ' → ' + target
    if decision.continuation:
        choice = T(lang, 'route_same_session')
    elif decision.needs_computer:
        choice = T(lang, 'route_computer')
    elif decision.needs_web:
        choice = T(lang, 'route_web')
    else:
        choice = T(lang, 'route_conversation')
    return source + ' → ' + choice + ' → ' + target


def project_directory(text: str, cfg: dict, current: Path) -> Path:
    if not re.search(r'\b(projeto|project)\b', text, re.IGNORECASE):
        return current
    root = Path(cfg.get('dev_dir', str(current))).expanduser()
    try:
        matches = [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
                   and re.search(r'(?<![\w-])' + re.escape(path.name) + r'(?![\w-])', text, re.IGNORECASE)]
    except OSError:
        return current
    return matches[0].resolve() if len(matches) == 1 else current


def reconcile_submission(session: sessions.SessionRef, request: RouteRequest,
                         cancel: threading.Event, on_status: Callable) -> str:
    for attempt in range(3):
        try:
            previous = sessions.read_record(session.id)['requests'].get(request.request_id)
        except (OSError, ValueError, KeyError):
            raise sessions.SubmissionUnknown(session) from None
        if not previous or previous['state'] not in ('submitting', 'unknown', 'submitted'):
            raise sessions.SubmissionUnknown(session)
        on_status('session_reconnecting')
        try:
            # A persisted uncertain request invokes find_turn, never adapter.submit.
            turn_id = sessions.submit_turn(session, request)
        except sessions.SubmissionUnknown:
            if cancel.is_set():
                sessions.adapter_for(session.provider).stop(session)
                raise RouterCancelled() from None
            if attempt == 2:
                raise
            cancel.wait(.15)
        else:
            if cancel.is_set():
                sessions.interrupt_turn(session, turn_id)
                raise RouterCancelled()
            return turn_id
    raise sessions.SubmissionUnknown(session)


class ConversationExecutor:
    def __init__(self, cfg: dict, cwd: Path, legacy: Callable) -> None:
        self.cfg = dict(cfg)
        self.cwd = cwd.resolve()
        self.legacy = legacy
        self.session = restore_active_session(cfg, self.cwd)
        if self.session:
            self.cwd = self.session.cwd
        self.conversation_id = self.session.conversation_id if self.session else uuid.uuid4().hex
        self.native_unavailable = ''

    def ask(self, text: str, context: tuple[tuple[str, str], ...], cancel: threading.Event,
            on_status: Callable, on_event: Callable, on_state: Callable, on_late: Callable) -> Answer:
        cwd = project_directory(text, self.cfg, self.cwd)
        same_project = self.session is not None and self.session.cwd == cwd
        request = RouteRequest(uuid.uuid4().hex, self.conversation_id, text, context, cwd,
                               self.session.id if same_project else None)
        if cancel.is_set():
            raise RouterCancelled()
        mode = self.cfg.get('routing_mode', 'agent')
        lang = self.cfg.get('language', 'en')
        continuing = clear_continuation(request)
        pending = (T(lang, 'route_same_session') if continuing else T(lang, 'route_agent') if mode == 'agent'
                   else T(lang, 'route_selecting', router=router_label(self.cfg)))
        on_state(routing_mode=mode, effective_route='', fallback_reason='', session_id='',
                 session_backend='', can_attach=False, detail='',
                 routing_summary=pending)
        backends = {}
        if mode != 'agent' and not continuing:
            try:
                backends[mode] = build_backend(mode, self.cfg, cancel, on_event)
            except RouterUnavailable:
                pass
        decision = choose_route(request, self.cfg, backends)
        if cancel.is_set():
            raise RouterCancelled()
        profile = resolve_profile(decision, self.cfg)
        if decision.continuation and self.session:
            profile = ExecutionProfile(**sessions.read_record(self.session.id)['profile'])
        state = {'routing_mode': mode, 'effective_route': profile.transport,
                 'fallback_reason': decision.reason, 'session_id': '', 'session_backend': '',
                 'can_attach': False, 'detail': profile_label(profile),
                 'routing_summary': routing_summary(self.cfg, decision, profile)}
        on_state(**state)
        if decision.kind in ('answer', 'clarify'):
            return Answer(decision.answer, profile, 'api', decision.reason, 'api')
        if profile.transport == 'api':
            from jarvis_router_api import execute_api
            try:
                answer = execute_api(request, profile, self.cfg, cancel, on_event)
                return Answer(answer, profile, 'api', decision.reason, 'api')
            except RouterUnavailable as exc:
                decision = RouteDecision('agent', reason=str(exc))
                profile = resolve_profile(decision, self.cfg)
                state.update(effective_route='agent', fallback_reason=decision.reason, detail=profile_label(profile))
                state['routing_summary'] += ' → ' + T(
                    self.cfg.get('language', 'en'), 'route_api_fallback',
                    reason=route_failure_label(str(exc), self.cfg.get('language', 'en')))
                on_state(**state)
        if self.cfg.get('agent_session_mode', 'auto') != 'legacy' and not self.native_unavailable:
            try:
                return self.native(request, profile, decision.reason, cancel, on_status, on_event, on_state, on_late)
            except sessions.SessionUnsupported as exc:
                self.native_unavailable = str(exc)
        state.update(session_backend='legacy', fallback_reason=self.native_unavailable or decision.reason,
                     can_attach=False, session_id='')
        on_state(**state)
        answer, handoff = self.legacy(request, profile, cancel, on_status, on_event)
        return Answer(answer, profile, 'agent', decision.reason, 'legacy', True, handoff)

    def native(self, request: RouteRequest, profile: ExecutionProfile, reason: str, cancel: threading.Event,
               on_status: Callable, on_event: Callable, on_state: Callable, on_late: Callable) -> Answer:
        if self.session and self.session.cwd != request.cwd:
            self.session = None
        self.cwd = request.cwd
        if self.session and self.session.provider == 'claude':
            previous = sessions.read_record(self.session.id)['profile']
            if (previous['model'], previous['effort']) != (profile.model, profile.effort):
                # Claude's attached TUI has one profile. Keep the old native
                # session available and create the new profile before sending.
                self.session = None
        if self.session is None:
            try:
                self.session = sessions.start_session(request, profile, cancel=cancel)
            except sessions.SubmissionUnknown as exc:
                self.session = exc.session
                remember_session(self.session)
                on_state(session_id=self.session.id, session_backend='native', can_attach=True,
                         fallback_reason='submission_unknown')
                reconcile_submission(self.session, request, cancel, on_status)
            remember_session(self.session)
        elif (self.session.provider != profile.provider or self.session.system_access != profile.system_access
              or self.session.cwd != request.cwd):
            raise sessions.SessionMismatch('session_policy_mismatch')
        else:
            adapter = sessions.adapter_for(self.session.provider)
            if hasattr(adapter, 'ensure_connected'):
                adapter.ensure_connected(self.session)
            with sessions.locked(self.session.id):
                record = sessions.read_record(self.session.id)
                record['profile'].update(model=profile.model, effort=profile.effort, fast=profile.fast)
                sessions.write_record(self.session.id, record)
        submitted = sessions.read_record(self.session.id).get('requests', {}).get(request.request_id, {})
        job = {'request': request, 'session': self.session, 'turn_id': submitted.get('turn_id'),
               'started': time.monotonic()}
        on_state(session_id=self.session.id, session_backend='native', can_attach=True,
                 fallback_reason=reason)
        result = self.wait_for_turn(job, profile, reason, cancel, on_status, on_event, background=False)
        if result is not None:
            return result
        try:
            sessions.open_terminal(self.session)
        except (OSError, sessions.SessionUnsupported):
            on_state(fallback_reason='terminal_open_failed')
        def finish() -> None:
            try:
                completed = self.wait_for_turn(job, profile, reason, threading.Event(), on_status, on_event, background=True)
                if completed is not None:
                    on_late(completed)
            except Exception:
                on_state(fallback_reason='session_connection_lost')
        threading.Thread(target=finish, daemon=True, name='jarvis-session-result').start()
        return Answer('', profile, 'agent', reason, 'native', True,
                      {'session_id': self.session.id, 'provider': profile.provider,
                       'label': profile_label(profile), 'native': True})

    def wait_for_turn(self, job: dict, profile: ExecutionProfile, reason: str, cancel: threading.Event,
                      on_status: Callable, on_event: Callable, background: bool) -> Answer | None:
        session = job['session']
        request = job['request']
        while True:
            if cancel.is_set():
                if job['turn_id']:
                    sessions.interrupt_turn(session, job['turn_id'])
                raise RouterCancelled()
            if job['turn_id'] is None:
                try:
                    job['turn_id'] = sessions.submit_turn(session, request)
                except sessions.SubmissionUnknown:
                    job['turn_id'] = reconcile_submission(session, request, cancel, on_status)
                except sessions.SessionBusy as exc:
                    if str(exc) == 'previous_input_unconfirmed':
                        raise ExecutionUncertain('native_input_unconfirmed') from None
                    on_status(str(exc))
                if cancel.is_set():
                    continue
            if job['turn_id']:
                try:
                    for event in sessions.poll_events(session):
                        if event['turn_id'] == job['turn_id'] and event['kind'] not in ('user', 'result'):
                            on_event(event['kind'], event['text'])
                    outcome = sessions.read_record(session.id).get('completed_turns', {}).get(job['turn_id'])
                    if outcome:
                        if outcome['kind'] == 'result':
                            return Answer(outcome['text'], profile, 'agent', reason, 'native', True)
                        raise ExecutionUncertain('agent_' + outcome['kind'])
                except (OSError, TimeoutError, RuntimeError):
                    # The same native session remains the only execution target.
                    on_status('session_reconnecting')
            elapsed = time.monotonic() - job['started']
            if session.provider == 'claude' and elapsed > 10:
                backend = sessions.read_record(session.id)['backend']
                if backend.get('pending_request_id') == request.request_id:
                    raise ExecutionUncertain('native_input_unconfirmed')
            if elapsed > float(self.cfg.get('handoff_max_minutes', 30)) * 60:
                if job['turn_id']:
                    sessions.interrupt_turn(session, job['turn_id'])
                raise ExecutionUncertain('agent_time_limit')
            if not background and elapsed >= float(self.cfg.get('handoff_seconds_quick', 30)):
                return None
            time.sleep(.25)
