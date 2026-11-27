from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_consent
import jarvis_consent_mcp as mcp


class SessionConsentTest(unittest.TestCase):
    def test_cancel_kills_command_group(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(.15, cancel.set)
        timer.start()
        started = time.monotonic()
        rc, text = jarvis_consent.execute_brokered('sleep 20 & wait', should_cancel=cancel.is_set)
        timer.join()
        self.assertEqual(rc, 130)
        self.assertIn('cancel', text)
        self.assertLess(time.monotonic() - started, 2)

    @patch('jarvis_consent_mcp.current_context')
    @patch('jarvis_consent_mcp.jarvis_consent.execute_brokered')
    @patch('jarvis_consent_mcp.jarvis_consent.ask', return_value='allow')
    def test_unknown_native_turn_never_executes(self, ask: Mock, execute: Mock, context: Mock) -> None:
        context.return_value = None
        self.assertIn('DENIED', mcp.run_tool({'command': 'echo test'}))
        ask.assert_not_called()
        execute.assert_not_called()

    def native_session(self):
        import tempfile
        import jarvis_sessions
        from jarvis_routing import RouteRequest, ExecutionProfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = patch.object(jarvis_sessions, 'SESSIONS_DIR', Path(folder.name) / 'sessions')
        root.start()
        self.addCleanup(root.stop)
        request = RouteRequest('r1', 'c1', 'q', (), Path(folder.name))
        profile = ExecutionProfile('agent_default', 'agent', 'claude', 'haiku', 'low', 'ask')
        return jarvis_sessions, jarvis_sessions.new_session(request, profile, 'native-1')

    @patch('jarvis_consent_mcp.current_context')
    @patch('jarvis_consent_mcp.jarvis_consent.has_grant', return_value=False)
    @patch('jarvis_consent_mcp.jarvis_consent.grant_allow_all')
    @patch('jarvis_consent_mcp.jarvis_consent.execute_brokered', return_value=(0, 'ok'))
    @patch('jarvis_consent_mcp.jarvis_consent.ask', return_value='allow_all')
    def test_grants_bind_to_each_native_turn(self, ask: Mock, execute: Mock, grant: Mock,
                                           has_grant: Mock, context: Mock) -> None:
        _, session = self.native_session()
        for turn in ('turn1', 'turn2'):
            call = session.id + '-' + turn
            context.return_value = {'call_id': call, 'session_id': session.id, 'started_at': time.time(),
                                    'question': 'question', 'native': True, 'lang': 'en'}
            mcp.run_tool({'command': 'echo ok'})
            grant.assert_called_with(call)
            self.assertTrue(callable(execute.call_args.kwargs['should_cancel']))
        self.assertEqual(ask.call_count, 2)

    @patch('jarvis_consent_mcp.current_context')
    @patch('jarvis_consent_mcp.jarvis_consent.has_grant', return_value=False)
    @patch('jarvis_consent_mcp.jarvis_consent.grant_allow_all')
    @patch('jarvis_consent_mcp.jarvis_consent.execute_brokered', return_value=(0, 'ok'))
    @patch('jarvis_consent_mcp.jarvis_consent.ask', return_value='allow_all')
    def test_allow_rest_answered_after_handoff_allows_only_once(self, ask: Mock, execute: Mock, grant: Mock,
                                                                has_grant: Mock, context: Mock) -> None:
        # A window still open at the handoff must not recreate the grant the handoff revoked,
        # even after a newer question changed the session state; a turn started after the
        # handoff may be granted again.
        sessions, session = self.native_session()
        with sessions.locked(session.id):
            record = sessions.read_record(session.id)
            record.update(handoff_at=1000.0, state='submitting')
            sessions.write_record(session.id, record)
        context.return_value = {'call_id': session.id + '-old', 'session_id': session.id, 'started_at': 900.0,
                                'question': 'q', 'native': True, 'lang': 'en'}
        self.assertIn('exit 0', mcp.run_tool({'command': 'echo ok'}))
        grant.assert_not_called()
        context.return_value = dict(context.return_value, call_id=session.id + '-new', started_at=1100.0)
        mcp.run_tool({'command': 'echo ok'})
        grant.assert_called_once_with(session.id + '-new')
