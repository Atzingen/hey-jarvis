from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_sessions as sessions
from jarvis_routing import RouteRequest, ExecutionProfile, RouterCancelled


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.root_patch = patch.object(sessions, 'SESSIONS_DIR', self.root / 'sessions')
        self.root_patch.start()
        self.request = RouteRequest('r1', 'c1', 'Hello', (), self.root)
        self.profile = ExecutionProfile('agent_default', 'agent', 'codex', 'test', 'low', 'ask')
        self.adapter = Mock()
        self.adapter.capabilities.return_value = dict.fromkeys(
            ('start', 'final_answer', 'submit', 'attach', 'interrupt', 'access_off', 'access_ask', 'access_full'), True)
        self.adapter.start.side_effect = lambda request, profile: sessions.new_session(request, profile, 'native-1')
        self.adapter.submit.return_value = 'turn-1'
        self.adapter.find_turn.return_value = None
        self.adapter.events.return_value = []
        self.adapter.attach_argv.return_value = ['codex', 'resume', 'native-1']
        self.patch = patch.object(sessions, 'adapter_for', return_value=self.adapter)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.root_patch.stop()
        self.folder.cleanup()

    def test_start_once_and_attach_never_resends(self) -> None:
        session = sessions.start_session(self.request, self.profile)
        self.assertEqual(sessions.submit_turn(session, self.request), 'turn-1')
        with patch('jarvis_sessions.subprocess.Popen') as launch, patch('jarvis_sessions.shutil.which', return_value='/usr/bin/alacritty'):
            sessions.open_terminal(session)
            argv = launch.call_args.args[0]
            self.assertNotIn('TUI.float', argv)
            self.assertIn('native-1', argv)
        self.adapter.start.assert_called_once()
        self.adapter.submit.assert_called_once()
        self.assertEqual(sessions.load_session(session.id), session)
        path = sessions.session_dir(session.id) / 'session.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_adapter_metadata_survives_submission(self) -> None:
        def submit(session, request):
            record = sessions.read_record(session.id)
            record['backend']['pending_request_id'] = request.request_id
            sessions.write_record(session.id, record)
            return 'turn-1'
        self.adapter.submit.side_effect = submit
        session = sessions.start_session(self.request, self.profile)
        self.assertEqual(sessions.read_record(session.id)['backend']['pending_request_id'], 'r1')

    def test_unknown_submission_does_not_retry_and_can_reconcile(self) -> None:
        self.adapter.submit.side_effect = TimeoutError('after send')
        with self.assertRaises(sessions.SubmissionUnknown) as error:
            sessions.start_session(self.request, self.profile)
        session = error.exception.session
        with self.assertRaises(sessions.SubmissionUnknown):
            sessions.submit_turn(session, self.request)
        self.adapter.submit.assert_called_once()
        self.adapter.find_turn.return_value = 'recovered-turn'
        self.assertEqual(sessions.submit_turn(session, self.request), 'recovered-turn')
        self.adapter.submit.assert_called_once()

    def test_missing_capabilities_falls_back_before_start(self) -> None:
        for key in ('submit', 'attach', 'final_answer', 'access_ask'):
            self.adapter.capabilities.return_value[key] = False
            with self.assertRaises(sessions.SessionUnsupported):
                sessions.start_session(self.request, self.profile)
        self.adapter.start.assert_not_called()
        self.adapter.submit.assert_not_called()

    def test_cancel_during_native_setup_prevents_submission(self) -> None:
        cancel = threading.Event()
        def create(request, profile):
            result = sessions.new_session(request, profile, 'native')
            cancel.set()
            return result
        self.adapter.start.side_effect = create
        with self.assertRaises(RouterCancelled):
            sessions.start_session(self.request, self.profile, cancel=cancel)
        self.adapter.submit.assert_not_called()
        self.adapter.stop.assert_called_once()

    def test_cross_project_or_conversation_is_rejected(self) -> None:
        session = sessions.start_session(self.request, self.profile)
        for request in (replace(self.request, request_id='r2', cwd=self.root/'other'),
                        replace(self.request, request_id='r3', conversation_id='other')):
            with self.assertRaises(sessions.SessionMismatch):
                sessions.submit_turn(session, request)
        self.adapter.submit.assert_called_once()

    def test_events_are_deduplicated_and_late_result_is_real(self) -> None:
        session = sessions.start_session(self.request, self.profile)
        event = {'event_id': 'e1', 'session_id': session.native_id, 'turn_id': 'turn-1', 'kind': 'result', 'text': 'Finished'}
        self.adapter.events.return_value = [event, event]
        self.assertEqual(sessions.poll_events(session), [event])
        self.assertEqual(sessions.poll_events(session), [])
        self.adapter.events.return_value += [event | {'event_id': 'foreign', 'session_id': 'another'}]
        self.assertEqual(sessions.poll_events(session), [])

    def test_busy_terminal_is_queued_without_submission(self) -> None:
        self.adapter.submit.side_effect = sessions.SessionBusy('terminal owns input')
        session = self.adapter.start(self.request, self.profile)
        with self.assertRaises(sessions.SessionBusy):
            sessions.submit_turn(session, self.request)
        self.assertEqual(sessions.read_record(session.id)['requests']['r1']['state'], 'queued')
        self.adapter.submit.side_effect = None
        self.assertEqual(sessions.submit_turn(session, self.request), 'turn-1')

    def test_untrusted_id_cannot_escape_session_directory(self) -> None:
        with self.assertRaises(ValueError):
            sessions.load_session('../../etc/passwd')

    def test_watchdog_only_interrupts_expired_running_turns(self) -> None:
        session = self.adapter.start(self.request, self.profile)
        self.adapter.active_turns.return_value = [('old-turn', 100.0), ('new-turn', 195.0)]
        expired = sessions.enforce_deadlines(session, 20, now=200.0)
        self.assertEqual(expired, ['old-turn'])
        self.adapter.interrupt.assert_called_once_with(session, 'old-turn')
        self.adapter.active_turns.return_value = []
        self.assertEqual(sessions.enforce_deadlines(session, 20, now=900.0), [])
