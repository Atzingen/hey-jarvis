from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_config
from jarvis_routing import RouteRequest, RouterUnavailable, RouterCancelled, ExecutionUncertain, build_profiles
from jarvis_router_api import build_api_router, execute_api


def answer(text: str) -> dict:
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def handoff(**args: object) -> dict:
    return {'output': [{'type': 'function_call', 'name': 'handoff', 'arguments': json.dumps(args)}]}


class ApiRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = jarvis_config.defaults()
        self.cfg.update(api_provider='openai', api_model='test-model', openai_api_key='not-a-real-key')
        self.request = RouteRequest('r', 'c', 'Explique este resultado', (('pergunta', 'resposta'),), Path('/tmp/project'))
        self.cancel = threading.Event()
        self.event = Mock()

    @patch('jarvis_router_api.post_json')
    def test_answer_once_and_keep_original_context(self, post: Mock) -> None:
        post.return_value = answer('Resposta com <<DORMIR>> como exemplo de texto.')
        result = build_api_router(self.cfg, self.cancel, self.event)(self.request)
        self.assertEqual(result.kind, 'answer')
        self.assertIn('<<DORMIR>>', result.answer)
        post.assert_called_once()
        body = post.call_args.args[2]
        self.assertEqual(body['input'][-1]['content'], self.request.text)
        self.assertEqual(body['input'][0]['content'], 'pergunta')
        self.assertNotIn('api_key', json.dumps(body))

    @patch('jarvis_router_api.post_json')
    def test_disabled_missing_model_or_key_never_calls(self, post: Mock) -> None:
        for changes in ({'api_provider': 'none'}, {'api_model': ''}, {'openai_api_key': ''}):
            with patch.dict('os.environ', {}, clear=True):
                cfg = self.cfg | changes
                with self.assertRaises(RouterUnavailable):
                    build_api_router(cfg, self.cancel, self.event)(self.request)
        post.assert_not_called()

    @patch('jarvis_router_api.post_json')
    def test_handoff_agent_and_one_strong_escalation(self, post: Mock) -> None:
        post.return_value = handoff(target='agent', needs_computer=True, complexity='high')
        route = build_api_router(self.cfg, self.cancel, self.event)(self.request)
        self.assertEqual((route.kind, route.needs_computer), ('agent', True))
        self.cfg['api_strong_model'] = 'strong'
        post.return_value = handoff(target='api_strong', needs_computer=False, complexity='high')
        route = build_api_router(self.cfg, self.cancel, self.event)(self.request)
        self.assertEqual((route.kind, route.profile), ('api', 'api_strong'))
        post.return_value = answer('Final')
        self.assertEqual(execute_api(self.request, build_profiles(self.cfg)['api_strong'], self.cfg, self.cancel, self.event), 'Final')
        body = post.call_args.args[2]
        self.assertEqual(body['input'][-1]['content'], self.request.text)
        self.assertNotIn('handoff', [t.get('name') for t in body['tools']])

    @patch('jarvis_router_api.post_json')
    def test_unconfigured_strong_or_computer_route_goes_agent(self, post: Mock) -> None:
        for needs in (False, True):
            post.return_value = handoff(target='api_strong', needs_computer=needs, complexity='high')
            self.assertEqual(build_api_router(self.cfg, self.cancel, self.event)(self.request).kind, 'agent')

    @patch('jarvis_router_api.post_json')
    def test_invalid_output_is_never_an_action(self, post: Mock) -> None:
        for data in ({}, answer(''), handoff(target='shell', needs_computer=True, complexity='high'),
                     handoff(target='agent', needs_computer='false', complexity='low'),
                     handoff(target='agent', needs_computer=False, complexity='low', command='rm')):
            post.return_value = data
            with self.subTest(data=data), self.assertRaises(ExecutionUncertain):
                build_api_router(self.cfg, self.cancel, self.event)(self.request)

    @patch('jarvis_router_api.post_json')
    def test_rejection_uncertainty_and_cancellation_propagate(self, post: Mock) -> None:
        for error in (RouterUnavailable('http_401'), RouterUnavailable('http_429'),
                      ExecutionUncertain('timeout'), RouterCancelled()):
            post.side_effect = error
            with self.assertRaises(type(error)):
                build_api_router(self.cfg, self.cancel, self.event)(self.request)

    @patch('jarvis_router_api.post_json')
    def test_citations_and_web_toggle(self, post: Mock) -> None:
        data = answer('Fato verificado.')
        data['output'][0]['content'][0]['annotations'] = [
            {'type': 'url_citation', 'url': 'https://example.org/fact', 'title': 'Fonte'}]
        post.return_value = data
        result = build_api_router(self.cfg, self.cancel, self.event)(self.request)
        self.assertIn('https://example.org/fact', result.answer)
        self.cfg['api_web_search'] = False
        build_api_router(self.cfg, self.cancel, self.event)(self.request)
        self.assertNotIn('web_search', [t['type'] for t in post.call_args.args[2]['tools']])
