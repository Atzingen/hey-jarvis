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

    @patch('jarvis_consent_mcp.current_context')
    @patch('jarvis_consent_mcp.jarvis_consent.has_grant', return_value=False)
    @patch('jarvis_consent_mcp.jarvis_consent.grant_allow_all')
    @patch('jarvis_consent_mcp.jarvis_consent.execute_brokered', return_value=(0, 'ok'))
    @patch('jarvis_consent_mcp.jarvis_consent.ask', return_value='allow_all')
    def test_grants_bind_to_each_native_turn(self, ask: Mock, execute: Mock, grant: Mock,
                                           has_grant: Mock, context: Mock) -> None:
        for turn in ('session-turn1', 'session-turn2'):
            context.return_value = {'call_id': turn, 'question': 'question', 'native': True, 'lang': 'en'}
            mcp.run_tool({'command': 'echo ok'})
            grant.assert_called_with(turn)
            self.assertTrue(callable(execute.call_args.kwargs['should_cancel']))
        self.assertEqual(ask.call_count, 2)
