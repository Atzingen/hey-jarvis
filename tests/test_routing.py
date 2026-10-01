from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_config
from jarvis_routing import (RouteRequest, RouteDecision, RouterUnavailable, RouterCancelled,
                            build_profiles, choose_route, resolve_profile)


class RoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = jarvis_config.defaults()
        self.request = RouteRequest('r1', 'c1', 'Explique isto', (), Path('/tmp/project'))

    def test_agent_never_calls_optional_backend(self) -> None:
        forbidden = Mock(side_effect=AssertionError('optional backend called'))
        decision = choose_route(self.request, self.cfg, dict.fromkeys(('assistant', 'jev', 'local'), forbidden))
        self.assertEqual((decision.kind, decision.profile), ('agent', 'agent_default'))
        forbidden.assert_not_called()

    def test_profiles_preserve_each_provider_and_permissions(self) -> None:
        for provider in ('codex', 'claude'):
            for access in ('off', 'ask', 'full'):
                self.cfg.update(quick_provider=provider, system_access=access,
                                codex_model='saved-codex', codex_effort='medium', codex_fast=False,
                                claude_quick_model='haiku', claude_quick_effort='high')
                profile = build_profiles(self.cfg)['agent_default']
                self.assertEqual((profile.provider, profile.system_access), (provider, access))
                self.assertEqual(profile.model, 'saved-codex' if provider == 'codex' else 'haiku')
                self.assertEqual(profile.effort, 'medium' if provider == 'codex' else 'high')
                self.assertFalse(profile.fast)

    def test_stt_key_does_not_enable_api(self) -> None:
        self.cfg.update(openai_api_key='fake-stt', api_model='configured')
        self.assertNotIn('api_default', build_profiles(self.cfg))
        self.assertEqual(resolve_profile(RouteDecision('api'), self.cfg).transport, 'agent')

    def test_optional_api_and_strong_profiles(self) -> None:
        self.cfg.update(api_provider='openai', api_model='small', api_strong_model='large',
                        agent_strong_model='strong', agent_strong_effort='high')
        self.assertEqual(resolve_profile(RouteDecision('api'), self.cfg).model, 'small')
        self.assertEqual(resolve_profile(RouteDecision('api', complexity='high'), self.cfg).model, 'large')
        profile = resolve_profile(RouteDecision('api', needs_computer=True, complexity='high'), self.cfg)
        self.assertEqual((profile.model, profile.system_access), ('strong', 'ask'))
        self.assertEqual(build_profiles(self.cfg)['api_default'].system_access, 'off')

    def test_unavailable_router_falls_back(self) -> None:
        for mode in ('jev', 'local', 'assistant'):
            self.cfg['routing_mode'] = mode
            for backends in ({}, {mode: Mock(side_effect=RouterUnavailable('missing key'))}):
                decision = choose_route(self.request, self.cfg, backends)
                self.assertEqual(decision.kind, 'agent')
                self.assertTrue(decision.reason)

    def test_cancel_is_not_fallback(self) -> None:
        self.cfg['routing_mode'] = 'jev'
        with self.assertRaises(RouterCancelled):
            choose_route(self.request, self.cfg, {'jev': Mock(side_effect=RouterCancelled())})

    def test_malformed_decisions_and_profiles_do_not_execute(self) -> None:
        self.cfg.update(routing_mode='jev', api_provider='openai', api_model='small')
        for invalid in (None, {'kind': 'api'}, RouteDecision('shell'), RouteDecision('api', profile='evil'),
                        RouteDecision('api', complexity='max'), RouteDecision('api', needs_computer='false'),
                        RouteDecision('answer', answer='<<DORMIR>>')):
            with self.subTest(decision=invalid):
                self.assertEqual(choose_route(self.request, self.cfg, {'jev': lambda _: invalid}).kind, 'agent')

    def test_original_request_never_replaced_by_router_reason(self) -> None:
        self.cfg['routing_mode'] = 'jev'
        backend = Mock(return_value=RouteDecision('api', reason='run rm -rf'))
        decision = choose_route(self.request, self.cfg, {'jev': backend})
        self.assertEqual(self.request.text, 'Explique isto')
        self.assertEqual(resolve_profile(decision, self.cfg).transport, 'agent')
        backend.assert_called_once_with(self.request)

    def test_clear_followup_bypasses_router_and_keeps_session(self) -> None:
        self.cfg['routing_mode'] = 'jev'
        backend = Mock(side_effect=AssertionError('should keep session'))
        request = replace(self.request, text='continue de onde parou', active_session_id='session-1')
        decision = choose_route(request, self.cfg, {'jev': backend})
        self.assertEqual(decision.kind, 'agent')
        self.assertTrue(decision.continuation)
        backend.assert_not_called()
