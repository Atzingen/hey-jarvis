from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_agent_codex as codex
import jarvis_sessions as sessions
from jarvis_routing import RouteRequest, ExecutionProfile


class CodexAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.root_patch = patch.object(sessions, 'SESSIONS_DIR', self.root/'sessions')
        self.root_patch.start()
        self.request = RouteRequest('request-1', 'conversation-1', 'Olá', (), self.root)
        self.profile = ExecutionProfile('agent_default', 'agent', 'codex', 'model', 'medium', 'off', True)
        self.session = sessions.new_session(self.request, self.profile, 'native-id')
        sessions.update_backend(self.session, {'socket': str(self.root/'socket'), 'effective_cwd': str(self.root)})

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.folder.cleanup()

    def test_safe_policy_has_no_builtin_actions(self) -> None:
        for access in ('off', 'ask'):
            profile = ExecutionProfile('agent_default', 'agent', 'codex', 'model', 'low', access)
            config = codex.policy_config(profile, self.root/'ctx.json')
            self.assertFalse(config['features']['shell_tool'])
            self.assertFalse(config['features']['unified_exec'])
            self.assertFalse(config['include_apply_patch_tool'])
            self.assertEqual(config['web_search'], 'disabled')
            self.assertEqual(config['project_doc_max_bytes'], 0)
            self.assertEqual(bool(config.get('mcp_servers')), access == 'ask')

    @patch('jarvis_agent_codex.rpc')
    def test_submit_uses_original_request_id_and_exact_thread(self, rpc: Mock) -> None:
        rpc.side_effect = [{'thread': {'turns': []}}, {'turn': {'id': 'turn-1'}}]
        self.assertEqual(codex.submit(self.session, self.request), 'turn-1')
        params = rpc.call_args.args[2]
        self.assertEqual(params['threadId'], self.session.native_id)
        self.assertEqual(params['clientUserMessageId'], self.request.request_id)
        self.assertIn(self.request.text, params['input'][0]['text'])
        self.assertFalse(rpc.call_args_list[0].args[2]['includeTurns'])

    @patch('jarvis_agent_codex.rpc')
    def test_active_keyboard_turn_blocks_voice_before_send(self, rpc: Mock) -> None:
        rpc.return_value = {'thread': {'turns': [{'id': 'keyboard-turn', 'status': 'inProgress'}]}}
        with self.assertRaises(sessions.SessionBusy):
            codex.submit(self.session, self.request)
        self.assertEqual(rpc.call_count, 1)

    @patch('jarvis_agent_codex.rpc')
    def test_recover_request_from_native_user_message(self, rpc: Mock) -> None:
        rpc.return_value = {'data': [{'id': 'turn-r', 'items': [
            {'type': 'userMessage', 'clientId': 'request-1'}]}]}
        self.assertEqual(codex.find_turn(self.session, 'request-1'), 'turn-r')
        self.assertIsNone(codex.find_turn(self.session, 'another'))

    def test_attach_uses_exact_remote_session(self) -> None:
        argv = codex.attach_argv(self.session)
        self.assertIn('--remote', argv)
        self.assertEqual(argv[-2:], ['resume', self.session.native_id])
        self.assertNotIn('--last', argv)
        self.assertNotIn(self.request.text, argv)

    @patch('jarvis_agent_codex.rpc')
    def test_empty_thread_preflight_accepts_only_known_unmaterialized_error(self, rpc: Mock) -> None:
        rpc.side_effect = codex.CodexRpcError('thread/turns/list', {
            'code': -32600, 'message': 'thread X is not materialized yet; unavailable before first user message'})
        self.assertEqual(codex.thread(self.session), {'turns': []})
        rpc.side_effect = codex.CodexRpcError('thread/turns/list', {'code': -32601, 'message': 'Method not found'})
        with self.assertRaises(codex.CodexRpcError):
            codex.thread(self.session)

    @patch('jarvis_agent_codex.rpc')
    def test_events_preserve_real_final_answer_and_turn(self, rpc: Mock) -> None:
        rpc.return_value = {'data': [{'id': 't', 'status': 'completed', 'items': [
            {'id': 'a', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Concluído.'}]}]}
        result = codex.events(self.session, None)
        self.assertEqual(result[-1]['kind'], 'result')
        self.assertEqual(result[-1]['text'], 'Concluído.')
        self.assertEqual(result[-1]['session_id'], 'native-id')

    @patch('jarvis_agent_codex.subprocess.run')
    def test_stop_escalates_only_its_own_scope_when_graceful_shutdown_stalls(self, run: Mock) -> None:
        run.side_effect = [subprocess.TimeoutExpired('stop', 8), Mock(returncode=0)]
        codex.stop(self.session)
        argv = run.call_args.args[0]
        self.assertIn('kill', argv)
        self.assertIn('--signal=SIGKILL', argv)
        self.assertEqual(argv[-1], 'jarvis-session-' + self.session.id + '.scope')
