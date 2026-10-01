from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_config
from jarvis_routing import RouteRequest, RouterUnavailable, RouterCancelled, ExecutionUncertain, resolve_profile
from jarvis_router_jev import build_jev_router


def result(route: str = 'api', complexity: str = 'low', continuation: str = 'new') -> dict:
    return {'answers': {key: {'type': 'choice', 'choice': value} for key, value in
                        [('route', route), ('complexity', complexity), ('continuation', continuation)]}}


class JevRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = jarvis_config.defaults() | {'routing_mode': 'jev', 'jev_api_key': 'fake-key'}
        self.request = RouteRequest('r', 'c', 'Qual é a capital do Brasil?', (), Path('/tmp/project'))
        self.cancel = threading.Event()

    @patch('jarvis_router_jev.post_json')
    def test_jev_needs_no_openai_key(self, post: Mock) -> None:
        post.return_value = result()
        with patch.dict('os.environ', {}, clear=True):
            decision = build_jev_router(self.cfg, self.cancel)(self.request)
        self.assertEqual(resolve_profile(decision, self.cfg).transport, 'agent')
        self.cfg.update(api_provider='openai', api_model='small')
        self.assertEqual(resolve_profile(decision, self.cfg).transport, 'api')
        body = post.call_args.args[2]
        self.assertEqual(body['model'], 'jev-latest')
        self.assertEqual(body['state']['text'], self.request.text)
        self.assertEqual(set(body['questions']), {'route', 'complexity', 'continuation'})

    @patch('jarvis_router_jev.post_json')
    def test_missing_key_never_calls(self, post: Mock) -> None:
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RouterUnavailable):
            build_jev_router(self.cfg | {'jev_api_key': ''}, self.cancel)(self.request)
        post.assert_not_called()

    @patch('jarvis_router_jev.post_json')
    def test_failure_is_only_classification_and_can_fallback(self, post: Mock) -> None:
        for error in (RouterUnavailable('http_401'), RouterUnavailable('http_429'),
                      ExecutionUncertain('http_500'), ExecutionUncertain('timeout')):
            post.side_effect = error
            with self.assertRaises(RouterUnavailable):
                build_jev_router(self.cfg, self.cancel)(self.request)
        post.side_effect = RouterCancelled()
        with self.assertRaises(RouterCancelled):
            build_jev_router(self.cfg, self.cancel)(self.request)

    @patch('jarvis_router_jev.post_json')
    def test_malformed_output_never_selects_arbitrary_model(self, post: Mock) -> None:
        for data in ({}, result('shell'), result(complexity='ultra'), result(continuation='probably'),
                     {'answers': {'route': 'api'}}, {'answers': []}):
            post.return_value = data
            with self.subTest(data=data), self.assertRaises(RouterUnavailable):
                build_jev_router(self.cfg, self.cancel)(self.request)

    @patch('jarvis_router_jev.post_json')
    def test_computer_and_continuation(self, post: Mock) -> None:
        post.return_value = result('agent', 'high', 'continue')
        decision = build_jev_router(self.cfg, self.cancel)(self.request)
        self.assertTrue(decision.needs_computer)
        self.assertTrue(decision.continuation)
        self.assertEqual(decision.complexity, 'high')
