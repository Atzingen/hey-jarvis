from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))

import jarvis_config
import jarvis_stt


class SessionSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.events = iter(['session.created', 'session.updated'])

    def recv(self, **kwargs) -> str:
        return json.dumps({'type': next(self.events)})

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def close(self) -> None:
        pass


class VocabularyTest(unittest.TestCase):
    def test_personal_vocabulary_and_disable_survive_config_save(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            cfg = jarvis_config.defaults()
            cfg.update(stt_vocabulary='Claude Code\nPiracicaba', stt_vocabulary_enabled=False,
                       nemotron_vocabulary_boost=1.5)
            jarvis_config.save(cfg, path)
            saved = jarvis_config.load(path)
            self.assertEqual(saved.get('stt_vocabulary'), 'Claude Code\nPiracicaba')
            self.assertIs(saved.get('stt_vocabulary_enabled'), False)
            self.assertEqual(saved.get('nemotron_vocabulary_boost'), 1.5)

    def test_custom_terms_replace_defaults_and_reach_both_streaming_sessions(self) -> None:
        for provider in ('nemotron', 'openai'):
            with self.subTest(provider=provider):
                session = self.connect(provider, ' Claude Code \nclaude code\n<invalid>\nPiracicaba, Jev',
                                       nemotron_vocabulary_boost=1.5)
                if provider == 'nemotron':
                    self.assertEqual(session.get('speech_contexts'), [
                        {'phrases': ['Claude Code', 'Piracicaba', 'Jev'], 'boost': 1.5}])
                else:
                    self.assertEqual(session['audio']['input']['transcription'].get('keywords'),
                                     ['Claude Code', 'Piracicaba', 'Jev'])

    def test_disabled_vocabulary_sends_no_hints_even_with_saved_terms(self) -> None:
        for provider in ('nemotron', 'openai'):
            with self.subTest(provider=provider):
                session = self.connect(provider, 'Claude Code', stt_vocabulary_enabled=False)
                if provider == 'nemotron':
                    self.assertNotIn('speech_contexts', session)
                else:
                    self.assertNotIn('keywords', session['audio']['input']['transcription'])

    def test_blank_vocabulary_preserves_existing_terms(self) -> None:
        session = self.connect('openai', '')
        self.assertEqual(session['audio']['input']['transcription']['keywords'], ['Jarvis', 'Docker'])

    def test_custom_terms_are_capped_after_deduplication(self) -> None:
        session = self.connect('nemotron', 'Jarvis\njarvis\n' + '\n'.join(f'term-{i}' for i in range(120)))
        phrases = session.get('speech_contexts', [{}])[0].get('phrases', [])
        self.assertEqual(len(phrases), 100)
        self.assertEqual(phrases[:2], ['Jarvis', 'term-0'])
        self.assertEqual(phrases[-1], 'term-98')

    def test_legacy_model_keeps_streaming_without_unsupported_boosting(self) -> None:
        session = self.connect('nemotron', 'Claude Code', supports_vocabulary=False)
        self.assertEqual(session['language'], 'pt-BR')
        self.assertNotIn('speech_contexts', session)

    def test_zero_boost_disables_nemotron_hints(self) -> None:
        session = self.connect('nemotron', 'Claude Code', nemotron_vocabulary_boost=0.0)
        self.assertNotIn('speech_contexts', session)

    def connect(self, provider: str, vocabulary: str, supports_vocabulary: bool = True,
                **settings: object) -> dict:
        socket = SessionSocket()
        server = SimpleNamespace(start=lambda: None, device='cpu', url='ws://127.0.0.1:1',
                                 token='test-only-token', supports_vocabulary=supports_vocabulary)
        cfg = {'stt_provider': provider, 'openai_api_key': 'test-only-key',
               'stt_vocabulary': vocabulary, **settings}
        with (patch('jarvis_nemotron.NemotronServer', return_value=server),
              patch('websockets.sync.client.connect', return_value=socket)):
            backend = jarvis_stt.build_transcriber(cfg, ['Jarvis', 'Docker'], language='pt')
            backend.connect()
        return socket.sent[0]['session']
