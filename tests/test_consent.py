"""safe_text: texto controlado pelo modelo não pode emitir controles de terminal
na janela de autorização (jarvis-consent.py).

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_consent -v
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_consent  # noqa: E402

safe_text = jarvis_consent.safe_text


def has_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or unicodedata.category(ch) == "Cf"
               for ch in text)


class SafeTextTest(unittest.TestCase):
    def test_ansi_escapes_become_visible(self) -> None:
        out = safe_text("\x1b[2J\x1b[H rm -rf /")
        self.assertEqual(out, "\\x1b[2J\\x1b[H rm -rf /")
        self.assertFalse(has_control(out))

    def test_c1_controls_and_del_are_escaped(self) -> None:
        self.assertEqual(safe_text("a\x9bb\x7fc"), "a\\x9bb\\x7fc")

    def test_bidi_and_zero_width_are_escaped(self) -> None:
        out = safe_text("ls‮ /rm​")
        self.assertEqual(out, "ls\\u202e /rm\\u200b")
        self.assertFalse(has_control(out))

    def test_newlines_only_kept_when_asked(self) -> None:
        self.assertEqual(safe_text("a\nb"), "a\\x0ab")
        self.assertEqual(safe_text("a\nb", keep_newlines=True), "a\nb")
        self.assertEqual(safe_text("a\r\nb", keep_newlines=True), "a\\x0d\nb")

    def test_tab_and_plain_unicode_pass_through(self) -> None:
        self.assertEqual(safe_text("x\ty"), "x    y")
        self.assertEqual(safe_text("olá ção — ✓"), "olá ção — ✓")


if __name__ == "__main__":
    unittest.main()


class BrokerTest(unittest.TestCase):
    def test_exit_code_and_output(self) -> None:
        rc, out = jarvis_consent.execute_brokered("echo hi; echo err >&2; exit 3")
        self.assertEqual(rc, 3)
        self.assertEqual(out, "hi\nerr\n")

    def test_output_is_capped(self) -> None:
        _, out = jarvis_consent.execute_brokered("head -c 5000 /dev/zero | tr '\\0' x", limit=100)
        self.assertIn("truncated at 100 bytes", out)
        self.assertLess(len(out), 200)

    def test_timeout_kills_background_children(self) -> None:
        rc, out = jarvis_consent.execute_brokered("sleep 30 & sleep 30", timeout=1)
        self.assertEqual(rc, 124)
        self.assertIn("timeout", out)

    def test_env_is_minimal(self) -> None:
        import os
        os.environ["JARVIS_TEST_SECRET"] = "x"
        try:
            _, out = jarvis_consent.execute_brokered("env")
        finally:
            del os.environ["JARVIS_TEST_SECRET"]
        self.assertNotIn("JARVIS_TEST_SECRET", out)
        self.assertIn("HOME=", out)

    def test_grants_are_per_call_and_revocable(self) -> None:
        jarvis_consent.grant_allow_all("jarvis-model-test-9")
        self.assertTrue(jarvis_consent.has_grant("jarvis-model-test-9"))
        self.assertFalse(jarvis_consent.has_grant("jarvis-model-test-8"))
        jarvis_consent.revoke_grants()
        self.assertFalse(jarvis_consent.has_grant("jarvis-model-test-9"))
