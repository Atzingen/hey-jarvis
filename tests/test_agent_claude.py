from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_agent_claude as claude
import jarvis_sessions as sessions
from jarvis_routing import RouteRequest, ExecutionProfile


class ClaudeAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patch = patch.object(sessions, 'SESSIONS_DIR', self.root / 'sessions')
        self.patch.start()
        self.request = RouteRequest('r1', 'c1', 'Olá $(touch /tmp/not-allowed)', (), self.root)
        self.profile = ExecutionProfile('agent_default', 'agent', 'claude', 'haiku', 'low', 'off')
        self.session = sessions.new_session(self.request, self.profile, 'native-id')
        sessions.update_backend(self.session, {'socket': str(self.root/'tmux'), 'ready': True,
                                              'current_turn': '', 'running': False})

    def tearDown(self) -> None:
        self.patch.stop()
        self.folder.cleanup()

    def test_restricted_native_flags_preserve_subscription(self) -> None:
        for access in ('off', 'ask', 'full'):
            profile = ExecutionProfile('agent_default', 'agent', 'claude', 'haiku', 'low', access)
            argv = claude.claude_argv(self.session, profile, self.root/'settings', self.root/'ctx')
            self.assertNotIn('--bare', argv)
            self.assertIn('--session-id', argv)
            if access != 'full':
                for flag in ('--restricted', '--strict-mcp-config', '--tools', '--permission-mode'):
                    self.assertIn(flag, argv)
                self.assertNotIn('--no-session-persistence', argv)
            else:
                self.assertIn('--dangerously-skip-permissions', argv)

    @patch('jarvis_agent_claude.tmux')
    def test_attached_keyboard_queues_voice_without_touching_input(self, tmux: Mock) -> None:
        tmux.return_value = '/dev/pts/99'
        with self.assertRaises(sessions.SessionBusy):
            claude.submit(self.session, self.request)
        self.assertEqual(tmux.call_count, 1)
        self.assertIn('list-clients', tmux.call_args.args)

    @patch('jarvis_agent_claude.tmux', return_value='')
    def test_unconfirmed_paste_blocks_another_voice_message(self, tmux: Mock) -> None:
        sessions.update_backend(self.session, {'pending_request_id': 'earlier-request'})
        with self.assertRaises(sessions.SessionBusy):
            claude.submit(self.session, self.request)
        self.assertFalse(any('load-buffer' in call.args for call in tmux.call_args_list))

    @patch('jarvis_agent_claude.tmux')
    @patch('jarvis_agent_claude.subprocess.run')
    def test_interrupt_stops_exact_background_worker(self, run: Mock, tmux: Mock) -> None:
        run.return_value.returncode = 0
        sessions.update_backend(self.session, {'background_id': 'abcd1234', 'running': True, 'current_turn': 'turn'})
        claude.interrupt(self.session, 'turn')
        self.assertEqual(run.call_args.args[0], ['claude', 'stop', 'abcd1234'])
        state = sessions.read_record(self.session.id)['backend']
        self.assertTrue(state['stopped'])
        self.assertFalse(state['running'])
        self.assertFalse(any('send-keys' in call.args for call in tmux.call_args_list))

    @patch('jarvis_agent_claude.stop')
    def test_interrupt_recognizes_pending_unacknowledged_request(self, stop: Mock) -> None:
        sessions.update_backend(self.session, {'pending_request_id': 'new', 'current_turn': 'previous', 'running': False})
        claude.interrupt(self.session, 'new')
        stop.assert_called_once_with(self.session)
        self.assertEqual(claude.events(self.session, None)[-1]['kind'], 'interrupted')

    @patch('jarvis_agent_claude.tmux', return_value='')
    def test_literal_paste_keeps_text_out_of_shell(self, tmux: Mock) -> None:
        self.assertEqual(claude.submit(self.session, self.request), 'r1')
        load = [call for call in tmux.call_args_list if 'load-buffer' in call.args][0]
        self.assertIn('$(touch /tmp/not-allowed)', load.kwargs['input_text'])
        self.assertNotIn(self.request.text, load.args)
        self.assertTrue(any('paste-buffer' in call.args for call in tmux.call_args_list))
        self.assertNotIn('--last', claude.attach_argv(self.session))

    def test_hook_ignores_other_sessions_and_preserves_final_answer(self) -> None:
        claude.record_hook(self.session, {'session_id': 'foreign', 'hook_event_name': 'Stop', 'last_assistant_message': 'Wrong'})
        self.assertEqual(claude.events(self.session, None), [])
        claude.record_hook(self.session, {'session_id': 'native-id', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Test'})
        claude.record_hook(self.session, {'session_id': 'native-id', 'hook_event_name': 'Stop', 'last_assistant_message': 'Real result'})
        events = claude.events(self.session, None)
        self.assertEqual(events[-1]['text'], 'Real result')
        self.assertEqual(events[-1]['kind'], 'result')
        self.assertEqual(events[0]['turn_id'], events[-1]['turn_id'])

    def test_consent_is_scoped_to_current_turn(self) -> None:
        claude.record_hook(self.session, {'session_id': 'native-id', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'First'})
        first = claude.consent_context(self.session)
        claude.record_hook(self.session, {'session_id': 'native-id', 'hook_event_name': 'Stop', 'last_assistant_message': 'Done'})
        self.assertEqual(claude.consent_context(self.session), {})
        claude.record_hook(self.session, {'session_id': 'native-id', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Second'})
        second = claude.consent_context(self.session)
        self.assertNotEqual(first['call_id'], second['call_id'])
        self.assertEqual(second['question'], 'Second')
