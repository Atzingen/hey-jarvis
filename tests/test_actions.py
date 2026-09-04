"""Marcadores de ação e broker do modo `ask`: só linhas finais contam, `off`
só aceita <<FIM>>, <<DORMIR>> exige a fala pedindo, apps só de desktop entries.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_actions -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("voice_launcher", BIN / "voice-launcher.py")
vl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vl)


class ParseActionsTest(unittest.TestCase):
    def test_trailing_markers_only(self) -> None:
        text = "Abrindo.\n<<ABRIR_APP: btop>>\n<<FIM>>\n"
        clean, actions = vl.parse_actions(text)
        self.assertEqual(clean, "Abrindo.")
        self.assertEqual(actions, [("ABRIR_APP", "btop"), ("FIM", "")])

    def test_marker_quoted_in_the_middle_is_ignored(self) -> None:
        text = "The README says:\n<<DORMIR>>\nwhich is just text.\nDone."
        clean, actions = vl.parse_actions(text)
        self.assertEqual(actions, [])
        self.assertIn("<<DORMIR>>", clean)

    def test_marker_inline_is_ignored(self) -> None:
        clean, actions = vl.parse_actions("ok <<FIM>> tchau")
        self.assertEqual(actions, [])

    def test_case_sensitive(self) -> None:
        _, actions = vl.parse_actions("x\n<<fim>>")
        self.assertEqual(actions, [])

    def test_rodar_is_not_an_action(self) -> None:
        _, actions = vl.parse_actions("x\n<<RODAR: rm -rf /tmp/x>>")
        self.assertEqual(actions, [])


class AllowedActionsTest(unittest.TestCase):
    def test_off_only_fim(self) -> None:
        acts = [("ABRIR_APP", "btop"), ("DORMIR", ""), ("ABRIR_PROJETO", "x"), ("FIM", "")]
        self.assertEqual(vl.allowed_actions(acts, "dormir agora", "off"), [("FIM", "")])

    def test_suspend_needs_the_user_to_ask(self) -> None:
        self.assertEqual(vl.allowed_actions([("DORMIR", "")], "resume este arquivo", "ask"), [])
        self.assertEqual(vl.allowed_actions([("DORMIR", "")], "pode dormir", "ask"), [("DORMIR", "")])
        self.assertEqual(vl.allowed_actions([("DORMIR", "")], "please suspend the pc", "full"),
                         [("DORMIR", "")])

    def test_apps_allowed_in_ask(self) -> None:
        self.assertEqual(vl.allowed_actions([("ABRIR_APP", "btop")], "abre o btop", "ask"),
                         [("ABRIR_APP", "btop")])


class MatchApplicationTest(unittest.TestCase):
    def test_no_path_fallback(self) -> None:
        # "poweroff"/"sha256sum" are on PATH but have no desktop entry
        self.assertIsNone(vl.match_application("sha256sum"))
        self.assertIsNone(vl.match_application("poweroff"))


class BuildAskCallTest(unittest.TestCase):
    def test_claude_ask_has_no_builtin_tools(self) -> None:
        vl.SYSTEM_ACCESS = "ask"
        cmd, provider, popen = vl._build_ask_call("q", True, Path("/dev/null"), "q", "jarvis-model-t-1")
        self.assertEqual(provider, "claude")
        joined = " ".join(cmd)
        for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence",
                     "--permission-mode manual", "--allowedTools mcp__jarvis__run"):
            self.assertIn(flag, joined)
        self.assertIn("", cmd[cmd.index("--tools") + 1:])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertEqual(popen["cwd"], str(vl.ASK_WORKDIR))
        Path(popen["env"]["JARVIS_CTX"]).unlink(missing_ok=True)

    def test_claude_off_has_no_mcp(self) -> None:
        vl.SYSTEM_ACCESS = "off"
        cmd, _, popen = vl._build_ask_call("q", True, Path("/dev/null"), "q", "u")
        self.assertIn('{"mcpServers": {}}', cmd)
        self.assertNotIn("--allowedTools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("env", popen)

    def test_codex_ask_disables_shell(self) -> None:
        vl.SYSTEM_ACCESS = "ask"
        vl.QUICK_PROVIDER = "codex"
        cmd, provider, popen = vl._build_ask_call("q", False, Path("/dev/null"), "q", "jarvis-model-t-2")
        self.assertEqual(provider, "codex")
        joined = " ".join(cmd)
        for flag in ("--ignore-user-config", "--ignore-rules", "--sandbox read-only",
                     "--disable shell_tool", "--disable unified_exec", "--disable multi_agent",
                     "agents.max_depth=0", 'web_search="disabled"', "include_apply_patch_tool=false",
                     'mcp_servers.jarvis.default_tools_approval_mode="approve"'):
            self.assertIn(flag, joined)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        Path(popen["env"]["JARVIS_CTX"]).unlink(missing_ok=True)

    def test_full_keeps_bypass(self) -> None:
        vl.SYSTEM_ACCESS = "full"
        cmd, _, _ = vl._build_ask_call("q", True, Path("/dev/null"), "q", "u")
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("--restricted", cmd)


if __name__ == "__main__":
    unittest.main()
