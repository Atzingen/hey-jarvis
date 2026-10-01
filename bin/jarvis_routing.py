#!/usr/bin/env python3
"""Pure routing policy. Importing this module never starts an optional backend."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Callable, Literal

RouteKind = Literal['answer', 'api', 'agent', 'clarify']
Complexity = Literal['low', 'medium', 'high']


@dataclass(frozen=True)
class RouteRequest:
    request_id: str
    conversation_id: str
    text: str
    context: tuple[tuple[str, str], ...]
    cwd: Path
    active_session_id: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    kind: RouteKind
    profile: str = 'agent_default'
    complexity: Complexity = 'low'
    needs_computer: bool = False
    continuation: bool = False
    answer: str = ''
    reason: str = ''
    needs_web: bool = False


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    transport: Literal['agent', 'api']
    provider: str
    model: str
    effort: str
    system_access: str
    fast: bool = False


class RouterUnavailable(Exception):
    """Classification unavailable, or execution definitively rejected before starting."""


class RouterCancelled(Exception):
    """The user cancelled this request; never start an alternative executor."""


class ExecutionUncertain(Exception):
    """Execution may have started. Do not resend automatically."""


RouterCall = Callable[[RouteRequest], RouteDecision]


def build_profiles(cfg: dict) -> dict[str, ExecutionProfile]:
    provider = cfg.get('quick_provider', 'codex')
    codex = provider == 'codex'
    base = ExecutionProfile(
        'agent_default', 'agent', provider,
        cfg.get('codex_model' if codex else 'claude_quick_model', '' if codex else 'sonnet'),
        cfg.get('codex_effort' if codex else 'claude_quick_effort', 'low'),
        cfg.get('system_access', 'ask'), bool(cfg.get('codex_fast', True)) if codex else False)
    profiles = {base.name: base}
    if cfg.get('agent_strong_model', '').strip():
        profiles['agent_strong'] = replace(base, name='agent_strong', model=cfg['agent_strong_model'],
                                            effort=cfg.get('agent_strong_effort') or base.effort)
    if cfg.get('api_provider') == 'openai' and cfg.get('api_model', '').strip():
        api = ExecutionProfile('api_default', 'api', 'openai', cfg['api_model'],
                               cfg.get('api_effort', ''), 'off')
        profiles[api.name] = api
        if cfg.get('api_strong_model', '').strip():
            profiles['api_strong'] = replace(api, name='api_strong', model=cfg['api_strong_model'],
                                             effort=cfg.get('api_strong_effort', ''))
    return profiles


def resolve_profile(decision: RouteDecision, cfg: dict) -> ExecutionProfile:
    profiles = build_profiles(cfg)
    transport = 'api' if decision.kind in ('api', 'answer') and not decision.needs_computer else 'agent'
    selected = f'{transport}_default'
    if decision.complexity == 'high' or decision.profile == f'{transport}_strong':
        selected = f'{transport}_strong' if f'{transport}_strong' in profiles else selected
    return profiles.get(selected, profiles['agent_default'])


def valid_decision(decision: object, mode: str) -> bool:
    if not isinstance(decision, RouteDecision):
        return False
    return (decision.kind in ('answer', 'api', 'agent', 'clarify')
            and decision.profile in ('agent_default', 'agent_strong', 'api_default', 'api_strong')
            and decision.complexity in ('low', 'medium', 'high')
            and type(decision.needs_computer) is bool
            and type(decision.continuation) is bool
            and type(decision.needs_web) is bool
            and isinstance(decision.reason, str) and len(decision.reason) <= 1000
            and isinstance(decision.answer, str) and len(decision.answer) <= 1_000_000
            and (decision.kind not in ('answer', 'clarify') or (mode == 'assistant' and bool(decision.answer.strip()))))


def clear_continuation(request: RouteRequest) -> bool:
    return bool(request.active_session_id and re.match(
        r'^(continue\b|continua\b|continuar\b|prossiga\b|pode continuar\b|retome\b|resume\b|keep going\b)',
        request.text.strip(), flags=re.IGNORECASE))


def choose_route(request: RouteRequest, cfg: dict, backends: dict[str, RouterCall]) -> RouteDecision:
    mode = cfg.get('routing_mode', 'agent')
    if clear_continuation(request):
        return RouteDecision('agent', continuation=True, reason='active_session')
    if mode == 'agent':
        return RouteDecision('agent', reason='subscription_default')
    backend = backends.get(mode)
    if backend is None:
        return RouteDecision('agent', reason='router_unavailable')
    try:
        decision = backend(request)
    except RouterUnavailable as exc:
        return RouteDecision('agent', reason=str(exc)[:160] or 'router_unavailable')
    if not valid_decision(decision, mode):
        return RouteDecision('agent', reason='invalid_classification')
    if decision.needs_computer or (decision.continuation and request.active_session_id):
        return replace(decision, kind='agent', answer='')
    if decision.needs_web and not cfg.get('api_web_search', True):
        return replace(decision, kind='agent', answer='', reason='api_web_search_disabled')
    profile = resolve_profile(decision, cfg)
    if decision.kind in ('api', 'answer') and profile.transport != 'api':
        return replace(decision, kind='agent', profile=profile.name, answer='', reason='api_not_configured')
    return replace(decision, profile=profile.name)
