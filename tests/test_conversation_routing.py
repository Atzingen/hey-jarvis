from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
import sys
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_config
import jarvis_conversation_routing as conversation
import jarvis_sessions as sessions
from jarvis_routing import RouteDecision, RouterUnavailable, RouterCancelled, ExecutionUncertain, build_profiles, RouteRequest
from jarvis_sessions import SessionUnsupported, SubmissionUnknown, SessionRef


class ConversationRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = jarvis_config.defaults() | {'agent_session_mode': 'legacy'}
        self.legacy = Mock(return_value=('Agent answer', None))
        self.state = Mock()
        self.cancel = threading.Event()
        self.create = patch('jarvis_conversation_routing.build_backend')
        self.backend = self.create.start()
        self.persist = patch('jarvis_conversation_routing.restore_active_session', return_value=None)
        self.persist.start()
        self.remember = patch('jarvis_conversation_routing.remember_session')
        self.remember.start()

    def tearDown(self) -> None:
        self.remember.stop()
        self.persist.stop()
        self.create.stop()

    def execute(self, cfg: dict | None = None):
        executor = conversation.ConversationExecutor(cfg or self.cfg, Path('/tmp'), self.legacy)
        result = executor.ask('Do the task', (('Earlier', 'Context'),), self.cancel, Mock(), Mock(), self.state, Mock())
        return executor, result

    def test_subscription_without_keys_does_not_construct_any_router(self) -> None:
        _, result = self.execute()
        self.assertEqual(result.text, 'Agent answer')
        self.assertTrue(result.allow_actions)
        self.backend.assert_not_called()
        self.assertEqual(self.legacy.call_args.args[1].provider, 'codex')

    def test_optional_unavailable_modes_keep_subscription(self) -> None:
        for mode in ('assistant', 'jev', 'local'):
            self.backend.return_value = Mock(side_effect=RouterUnavailable('missing_dependency'))
            _, result = self.execute(self.cfg | {'routing_mode': mode})
            self.assertEqual(result.text, 'Agent answer')
            self.assertEqual(result.reason, 'missing_dependency')

    def test_api_answer_never_allows_action_markers(self) -> None:
        self.backend.return_value = Mock(return_value=RouteDecision('answer', answer='<<DORMIR>>'))
        _, result = self.execute(self.cfg | {'routing_mode': 'assistant', 'api_provider': 'openai', 'api_model': 'model'})
        self.assertFalse(result.allow_actions)
        self.assertEqual(result.text, '<<DORMIR>>')
        self.legacy.assert_not_called()

    def test_cancel_never_falls_back(self) -> None:
        self.backend.return_value = Mock(side_effect=RouterCancelled())
        with self.assertRaises(RouterCancelled):
            self.execute(self.cfg | {'routing_mode': 'jev'})
        self.legacy.assert_not_called()

    @patch('jarvis_router_api.execute_api')
    def test_missing_api_key_falls_back_with_original_context(self, api: Mock) -> None:
        self.backend.return_value = Mock(return_value=RouteDecision('api'))
        api.side_effect = RouterUnavailable('api_key_missing')
        _, result = self.execute(self.cfg | {'routing_mode': 'jev', 'api_provider': 'openai', 'api_model': 'model'})
        self.assertEqual(result.text, 'Agent answer')
        request = self.legacy.call_args.args[0]
        self.assertEqual(request.text, 'Do the task')
        self.assertEqual(request.context, (('Earlier', 'Context'),))

    @patch('jarvis_router_api.execute_api', side_effect=ExecutionUncertain('timeout'))
    def test_accepted_api_timeout_is_not_reexecuted(self, api: Mock) -> None:
        self.backend.return_value = Mock(return_value=RouteDecision('api'))
        with self.assertRaises(ExecutionUncertain):
            self.execute(self.cfg | {'routing_mode': 'jev', 'api_provider': 'openai', 'api_model': 'model'})
        self.legacy.assert_not_called()

    @patch('jarvis_conversation_routing.sessions.start_session', side_effect=SessionUnsupported('older_cli'))
    def test_native_capability_failure_uses_legacy_before_send(self, start: Mock) -> None:
        _, result = self.execute(self.cfg | {'agent_session_mode': 'auto'})
        self.assertEqual(result.text, 'Agent answer')
        self.assertEqual(result.backend, 'legacy')
        self.legacy.assert_called_once()

    @patch('jarvis_conversation_routing.sessions.start_session')
    def test_uncertain_native_submit_never_uses_legacy(self, start: Mock) -> None:
        session = SessionRef('a'*32, 'c', 'codex', 'native', Path('/tmp'), 'ask')
        start.side_effect = SubmissionUnknown(session)
        with self.assertRaises(SubmissionUnknown):
            self.execute(self.cfg | {'agent_session_mode': 'auto'})
        self.legacy.assert_not_called()

    @patch('jarvis_conversation_routing.sessions.read_record')
    def test_continue_keeps_existing_strong_profile(self, read: Mock) -> None:
        cfg = self.cfg | {'agent_session_mode': 'auto', 'agent_strong_model': 'strong'}
        profile = build_profiles(cfg)['agent_strong']
        read.return_value = {'profile': asdict(profile)}
        executor = conversation.ConversationExecutor(cfg, Path('/tmp'), self.legacy)
        executor.session = SessionRef('a'*32, executor.conversation_id, 'codex', 'native', Path('/tmp'), 'ask')
        with patch.object(executor, 'native', return_value=conversation.Answer('Done', profile, 'agent')) as native:
            executor.ask('continue', (), self.cancel, Mock(), Mock(), Mock(), Mock())
        self.assertEqual(native.call_args.args[1].model, 'strong')

    @patch('jarvis_conversation_routing.sessions.start_session')
    @patch('jarvis_conversation_routing.sessions.read_record')
    def test_claude_changed_profile_starts_new_native_session(self, read: Mock, start: Mock) -> None:
        cfg = self.cfg | {'agent_session_mode': 'auto', 'quick_provider': 'claude', 'agent_strong_model': 'opus'}
        profiles = build_profiles(cfg)
        executor = conversation.ConversationExecutor(cfg, Path('/tmp'), self.legacy)
        old = SessionRef('a'*32, executor.conversation_id, 'claude', 'native', Path('/tmp'), 'ask')
        executor.session = old
        read.return_value = {'profile': asdict(profiles['agent_default'])}
        start.return_value = SessionRef('b'*32, executor.conversation_id, 'claude', 'new-native', Path('/tmp'), 'ask')
        request = RouteRequest('r', executor.conversation_id, 'A new hard task', (), Path('/tmp'))
        with patch.object(executor, 'wait_for_turn', return_value=conversation.Answer('Done', profiles['agent_strong'], 'agent')):
            executor.native(request, profiles['agent_strong'], '', self.cancel, Mock(), Mock(), Mock(), Mock())
        start.assert_called_once_with(request, profiles['agent_strong'], cancel=self.cancel)
        self.assertNotEqual(executor.session.native_id, old.native_id)

    def test_project_directory_requires_one_real_explicit_project(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'jarvis').mkdir()
            (root/'outro').mkdir()
            (root/'escape').symlink_to('/tmp', target_is_directory=True)
            cfg = {'dev_dir': str(root)}
            resolve = conversation.project_directory
            self.assertEqual(resolve('No projeto jarvis, explique o código.', cfg, Path('/tmp')), root/'jarvis')
            self.assertEqual(resolve('projeto jarvis ou outro', cfg, Path('/tmp')), Path('/tmp'))
            self.assertEqual(resolve('projeto escape', cfg, Path('/tmp')), Path('/tmp'))
            self.assertEqual(resolve('O que é um jarvis?', cfg, Path('/tmp')), Path('/tmp'))

    @patch('jarvis_conversation_routing.sessions.load_session')
    def test_restart_can_restore_explicit_real_project(self, load: Mock) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root/'jarvis'
            project.mkdir()
            pointer = root/'active.json'
            pointer.write_text('{"session_id":"' + 'a'*32 + '"}')
            session = SessionRef('a'*32, 'c', 'codex', 'native', project, 'ask')
            load.return_value = session
            # Exercise the original function, bypassing the default test stub.
            self.persist.stop()
            with patch.object(conversation, 'ACTIVE_FILE', pointer):
                cfg = self.cfg | {'agent_session_mode': 'auto', 'dev_dir': str(root)}
                self.assertEqual(conversation.restore_active_session(cfg, Path('/tmp')), session)
                self.assertIsNone(conversation.restore_active_session(cfg | {'system_access': 'off'}, Path('/tmp')))
            self.persist.start()


class NativeSubmissionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patches = [patch.object(sessions, 'SESSIONS_DIR', self.root/'sessions'),
                        patch.object(conversation, 'ACTIVE_FILE', self.root/'active.json')]
        for item in self.patches:
            item.start()
        self.cfg = jarvis_config.defaults() | {'agent_session_mode': 'auto', 'dev_dir': str(self.root)}
        self.adapter = Mock()
        self.adapter.capabilities.return_value = dict.fromkeys((*sessions.REQUIRED_CAPABILITIES, 'access_ask'), True)
        self.adapter.start.side_effect = lambda request, profile: sessions.new_session(request, profile, 'native')
        self.adapter.find_turn.return_value = 'accepted-turn'
        self.adapter.events.return_value = [{'event_id': 'done', 'session_id': 'native', 'turn_id': 'accepted-turn',
                                             'kind': 'result', 'text': 'Recovered answer'}]
        self.adapter_patch = patch.object(sessions, 'adapter_for', return_value=self.adapter)
        self.adapter_patch.start()
        self.legacy = Mock()
        self.executor = conversation.ConversationExecutor(self.cfg, self.root, self.legacy)
        self.cancel = threading.Event()

    def tearDown(self) -> None:
        self.adapter_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def ask(self):
        return self.executor.ask('A task', (), self.cancel, Mock(), Mock(), Mock(), Mock())

    def test_cancel_when_first_send_returns_interrupts_accepted_turn(self) -> None:
        def submit(session, request):
            self.cancel.set()
            return 'accepted-turn'
        self.adapter.submit.side_effect = submit
        with self.assertRaises(RouterCancelled):
            self.ask()
        self.adapter.submit.assert_called_once()
        self.adapter.interrupt.assert_called_once_with(self.executor.session, 'accepted-turn')

    def test_accepted_first_send_timeout_is_reconciled_without_resubmit(self) -> None:
        self.adapter.submit.side_effect = TimeoutError('Acknowledgement lost')
        result = self.ask()
        self.assertEqual(result.text, 'Recovered answer')
        self.adapter.submit.assert_called_once()
        self.adapter.find_turn.assert_called()
        self.legacy.assert_not_called()

    def test_existing_session_send_timeout_is_reconciled_without_resubmit(self) -> None:
        request = RouteRequest('old', self.executor.conversation_id, 'Old', (), self.root)
        self.executor.session = sessions.new_session(request, build_profiles(self.cfg)['agent_default'], 'native')
        self.adapter.submit.side_effect = TimeoutError('Acknowledgement lost')
        result = self.ask()
        self.assertEqual(result.text, 'Recovered answer')
        self.adapter.submit.assert_called_once()
        self.adapter.find_turn.assert_called()
        self.legacy.assert_not_called()

    def test_unresolved_send_never_reexecutes(self) -> None:
        self.adapter.submit.side_effect = TimeoutError('Acknowledgement lost')
        self.adapter.find_turn.return_value = None
        with self.assertRaises(SubmissionUnknown):
            self.ask()
        self.adapter.find_turn.assert_called()
        self.adapter.submit.assert_called_once()
        self.legacy.assert_not_called()
