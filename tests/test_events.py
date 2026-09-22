"""Leitura com teto dos arquivos escritos pelo modelo (eventos JSONL, resposta):
linhas gigantes nunca são carregadas inteiras, arquivos são lidos até o teto.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_events -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_events  # noqa: E402


def _claude_result(text: str) -> str:
    return json.dumps({"type": "result", "result": text})


class ReadCompleteLines(unittest.TestCase):
    def _lines(self, content: str, pos: int = 0, max_line: int = 64):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(content)
            path = f.name
        with open(path) as f:
            return jarvis_events.read_complete_lines(f, pos, max_line)

    def test_only_complete_lines_are_consumed(self):
        lines, pos, too_long = self._lines("a\nbb\nccc")
        self.assertEqual(lines, ["a\n", "bb\n"])
        self.assertEqual(pos, len("a\nbb\n"))
        self.assertFalse(too_long)

    def test_resumes_from_position(self):
        content = "a\nbb\nccc\n"
        lines, pos, _ = self._lines(content, pos=len("a\n"))
        self.assertEqual(lines, ["bb\n", "ccc\n"])
        self.assertEqual(pos, len(content))

    def test_unterminated_line_above_ceiling_is_an_overflow(self):
        lines, pos, too_long = self._lines("ok\n" + "x" * 100)
        self.assertEqual(lines, ["ok\n"])
        self.assertEqual(pos, 3)
        self.assertTrue(too_long)

    def test_unterminated_short_line_just_waits(self):
        lines, pos, too_long = self._lines("ok\n" + "x" * 10)
        self.assertEqual(lines, ["ok\n"])
        self.assertFalse(too_long)

    def test_position_counts_bytes_not_characters(self):
        content = "olá\nmundo\n"
        lines, pos, _ = self._lines(content)
        self.assertEqual(pos, len(content.encode()))
        self.assertEqual(lines, ["olá\n", "mundo\n"])


class FinalAnswerBounded(unittest.TestCase):
    def _path(self, content: str) -> Path:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(content)
        return Path(f.name)

    def test_result_line_is_found(self):
        p = self._path(_claude_result("quarenta e dois") + "\n")
        self.assertEqual(jarvis_events.final_answer("claude", p), "quarenta e dois")

    def test_oversized_lines_are_skipped(self):
        huge = json.dumps({"type": "result", "result": "x" * 5000})
        p = self._path(huge + "\n" + _claude_result("curta") + "\n")
        self.assertEqual(jarvis_events.final_answer("claude", p, max_line=1000), "curta")

    def test_reads_at_most_max_bytes(self):
        first = _claude_result("primeira")
        p = self._path(first + "\n" + _claude_result("depois do teto") + "\n")
        self.assertEqual(jarvis_events.final_answer("claude", p, max_bytes=len(first) + 1), "primeira")

    def test_missing_file_gives_fallback(self):
        self.assertEqual(jarvis_events.final_answer("claude", Path("/nonexistent/x.jsonl"), "fb"), "fb")


if __name__ == "__main__":
    unittest.main()
