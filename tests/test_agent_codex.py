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

    @patch('jarvis_agent_codex.thread')
    def test_turn_without_started_at_keeps_its_first_seen_time(self, thread: Mock) -> None:
        # With startedAt null every check used to restart the clock, so the deadline never hit.
        thread.return_value = {'turns': [{'id': 'turn-1', 'status': 'inProgress', 'startedAt': None}]}
        with patch.object(codex.time, 'time', return_value=1000.0):
            first = codex.active_turns(self.session)
        with patch.object(codex.time, 'time', return_value=5000.0):
            later = codex.active_turns(self.session)
        self.assertEqual(first, [('turn-1', 1000.0)])
        self.assertEqual(later, [('turn-1', 1000.0)])

    def test_system_mcp_servers_disable_restricted_modes(self) -> None:
        # /etc/codex/config.toml is merged into every Codex run; its tools would skip consent.
        system = self.root / 'system-config.toml'
        system.write_text('[mcp_servers.files]\ncommand = "server"\n')
        with (patch.object(codex, 'codex_system_mcp_servers', return_value=['files']),
              patch.object(codex.shutil, 'which', return_value='/usr/bin/codex'),
              patch.object(codex.importlib.util, 'find_spec', return_value=object()),
              patch.object(codex.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '--remote app-server', ''))):
            caps = codex.capabilities()
        self.assertTrue(caps['access_full'])
        self.assertFalse(caps['access_ask'])
        self.assertFalse(caps['access_off'])
        import jarvis_agent_policy
        with patch.object(jarvis_agent_policy, 'CODEX_SYSTEM_CONFIG', system):
            self.assertEqual(jarvis_agent_policy.codex_system_mcp_servers(), ['files'])

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
    def test_each_turn_reapplies_the_jarvis_access_policy(self, rpc: Mock) -> None:
        for access, expected in [('full', 'dangerFullAccess'), ('ask', 'readOnly'), ('off', 'readOnly')]:
            for status in ('idle', 'notLoaded'):
                with self.subTest(access=access, status=status):
                    profile = ExecutionProfile('agent_default', 'agent', 'codex', 'model', 'low', access)
                    session = sessions.new_session(self.request, profile, 'native-policy')
                    replies = [{'thread': {'status': {'type': status}, 'turns': []}}]
                    if status == 'notLoaded':
                        replies.append({'thread': {'status': {'type': 'idle'}, 'turns': []}})
                    rpc.side_effect = replies + [{'turn': {'id': 'turn-policy'}}]
                    self.assertEqual(codex.submit(session, self.request), 'turn-policy')
                    params = rpc.call_args.args[2]
                    self.assertEqual(params.get('sandboxPolicy'), {'type': expected})
                    self.assertEqual(params.get('approvalPolicy'), 'never')

    @patch('jarvis_agent_codex.rpc')
    def test_unloaded_thread_is_resumed_before_sending_new_request(self, rpc: Mock) -> None:
        rpc.side_effect = [
            {'thread': {'id': 'native-id', 'status': {'type': 'notLoaded'}, 'turns': []}},
            {'thread': {'id': 'native-id', 'status': {'type': 'idle'}, 'turns': []}},
            {'turn': {'id': 'turn-1'}},
        ]
        self.assertEqual(sessions.submit_turn(self.session, self.request), 'turn-1')
        calls = rpc.call_args_list
        self.assertEqual([call.args[1] for call in calls],
                         ['thread/read', 'thread/resume', 'turn/start'])
        self.assertEqual(calls[1].args[2], {'threadId': 'native-id', 'excludeTurns': True})
        self.assertEqual(calls[2].args[2]['clientUserMessageId'], 'request-1')
        record = sessions.read_record(self.session.id)
        self.assertEqual(record['requests']['request-1']['state'], 'submitted')
        self.assertEqual(record['session']['native_id'], 'native-id')

    @patch('jarvis_agent_codex.rpc')
    def test_resumed_thread_with_active_turn_does_not_receive_voice_request(self, rpc: Mock) -> None:
        rpc.side_effect = [
            {'thread': {'id': 'native-id', 'status': {'type': 'notLoaded'}, 'turns': []}},
            {'thread': {'id': 'native-id', 'status': {'type': 'active'}, 'turns': []}},
        ]
        with self.assertRaises(sessions.SessionBusy):
            sessions.submit_turn(self.session, self.request)
        self.assertEqual([call.args[1] for call in rpc.call_args_list],
                         ['thread/read', 'thread/resume'])
        self.assertEqual(sessions.read_record(self.session.id)['requests']['request-1']['state'],
                         'queued')

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
        run.side_effect = [subprocess.TimeoutExpired('stop', 8), Mock(returncode=0), Mock(stdout='inactive\n')]
        codex.stop(self.session)
        argv = run.call_args_list[1].args[0]
        self.assertIn('kill', argv)
        self.assertIn('--signal=SIGKILL', argv)
        self.assertEqual(argv[-1], 'jarvis-session-' + self.session.id + '.scope')

    @patch('jarvis_agent_codex.subprocess.run')
    def test_stop_reports_a_scope_that_is_still_running(self, run: Mock) -> None:
        # stop_session keeps the watchdog only when the adapter reports the failure.
        run.side_effect = [Mock(returncode=1), Mock(stdout='active\n')]
        with self.assertRaises(RuntimeError):
            codex.stop(self.session)
