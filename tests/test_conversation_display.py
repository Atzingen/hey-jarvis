from __future__ import annotations

from contextlib import ExitStack
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BIN = Path(__file__).resolve().parent.parent / 'bin'
sys.path.insert(0, str(BIN))
import jarvis_config
import jarvis_conversation_routing as routing
from jarvis_routing import RouteDecision, RouterUnavailable

spec = importlib.util.spec_from_file_location('display_launcher', BIN / 'voice-launcher.py')
vl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vl)


class ConversationDisplayTest(unittest.TestCase):
    def run_conversation(self, mode: str = 'jev', api_failure: bool = False) -> tuple[dict, list[dict]]:
        cfg = jarvis_config.defaults() | {
            'language': 'pt-BR', 'routing_mode': mode, 'api_provider': 'openai',
            'api_model': 'gpt-6-luna', 'api_effort': '', 'codex_model': 'gpt-6-astra',
            'agent_session_mode': 'legacy', 'window_enabled': True,
        }
        window = vl.JarvisWindow(enabled=True)
        observed: list[dict] = []

        def classify(request: object) -> RouteDecision:
            observed.append(copy.deepcopy(window.state))
            return RouteDecision('api', needs_web=True, reason='classified_web')

        stt = Mock()
        stt.begin.return_value.finish.side_effect = ['Qual a previsão do tempo?', 'fim']
        listener = Mock(fired=False, speech_chunks=None)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            state_file = Path(folder) / 'state.json'
            for target, value in [
                ('CFG', cfg), ('LANG', 'pt-BR'), ('STATE_FILE', state_file),
                ('QUIT_FLAG', Path(folder) / 'quit'),
            ]:
                stack.enter_context(patch.object(vl, target, value))
            for target, kwargs in [
                ('JarvisWindow', {'return_value': window}),
                ('launch_detached', {}), ('tts', {'return_value': False}), ('chime', {}),
                ('flush_stream', {}), ('dictate_requested', {'return_value': False}),
                ('record_until_silence', {'side_effect': [[0], [0]]}),
                ('BargeInListener', {'return_value': listener}),
                ('legacy_routed_call', {'return_value': ('Resposta do agente.', None)}),
            ]:
                stack.enter_context(patch.object(vl, target, **kwargs))
            stack.enter_context(patch.object(routing, 'ACTIVE_FILE', Path(folder) / 'active.json'))
            stack.enter_context(patch.object(routing, 'build_backend', return_value=classify))
            stack.enter_context(patch.object(vl.jarvis_narrate, 'resolve_mode', return_value=('templates', 'test')))
            stack.enter_context(patch.object(vl.jarvis_consent, 'pending', return_value=None))
            stack.enter_context(patch('jarvis_router_api.execute_api', return_value='Resposta da API.',
                                      side_effect=RouterUnavailable('http_429') if api_failure else None))
            vl.run_conversation(Mock(), Mock(), stt, Mock(), SimpleNamespace(test=False, wake_threshold=0.5))
            return json.loads(state_file.read_text()), observed

    def test_api_reply_uses_executed_model_in_bubble_and_sidebar(self) -> None:
        state, observed = self.run_conversation()
        exchange = state['exchanges'][0]
        self.assertEqual(exchange['label'], 'openai gpt-6-luna')
        self.assertEqual(exchange['a'], 'Resposta da API.')
        self.assertEqual(state['detail'], exchange['label'])
        self.assertIn('Jev', exchange.get('routing_summary', ''))
        self.assertIn('busca web', exchange.get('routing_summary', ''))
        self.assertIn('API', exchange.get('routing_summary', ''))
        self.assertEqual(observed[0]['detail'], '')
        self.assertEqual(observed[0]['exchanges'][0]['label'], '')
        self.assertIn('Jev', observed[0].get('routing_summary', ''))

    def test_api_rejection_records_fallback_with_actual_agent(self) -> None:
        state, _ = self.run_conversation(api_failure=True)
        exchange = state['exchanges'][0]
        self.assertEqual(exchange['a'], 'Resposta do agente.')
        self.assertIn('codex gpt-6-astra', exchange['label'])
        summary = exchange.get('routing_summary', '')
        for text in ('Jev', 'API', '429', 'assinatura'):
            self.assertIn(text, summary)

    def test_subscription_only_does_not_claim_jev_routing(self) -> None:
        state, _ = self.run_conversation(mode='agent')
        summary = state['exchanges'][0].get('routing_summary', '')
        self.assertIn('assinatura', summary)
        self.assertNotIn('Jev', summary)

    def test_late_route_update_does_not_change_newer_response(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(vl, 'STATE_FILE', Path(folder) / 'state.json'):
            window = vl.JarvisWindow(enabled=True)
            window.add_exchange('Primeira', 'Resposta anterior', 'codex gpt-6-astra')
            window.add_exchange('Segunda', 'Resposta nova', 'openai gpt-6-luna')
            window.update(detail='openai gpt-6-luna', routing_summary='Jev → API')
            window.update_routing_at(0, detail='claude sonnet', routing_summary='Jev → agente')
            window.set_answer_at(0, 'Resposta tardia', 'claude sonnet')
            state = json.loads(vl.STATE_FILE.read_text())
            self.assertEqual(state['detail'], 'openai gpt-6-luna')
            self.assertEqual(state['routing_summary'], 'Jev → API')
            self.assertEqual(state['exchanges'][0]['label'], 'claude sonnet')
            self.assertEqual(state['exchanges'][0]['routing_summary'], 'Jev → agente')
            self.assertEqual(state['exchanges'][1]['a'], 'Resposta nova')
