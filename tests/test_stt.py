"""LocalWhisper.transcribe: prompt sem contexto acumulado e repetição sem hotwords.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_stt -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_stt  # noqa: E402


class FakeModel:
    """Imita WhisperModel.transcribe: falha (como o ctranslate2) enquanto houver hotwords."""

    def __init__(self, fail_with_hotwords: type | None = None):
        self.fail_with_hotwords = fail_with_hotwords
        self.calls: list[dict] = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if self.fail_with_hotwords and kw.get("hotwords"):
            raise self.fail_with_hotwords("prompt estourou os 448 tokens")
        return iter([SimpleNamespace(text="olá"), SimpleNamespace(text="mundo")]), None


def _backend(model: FakeModel) -> jarvis_stt.LocalWhisper:
    stt = jarvis_stt.LocalWhisper.__new__(jarvis_stt.LocalWhisper)
    stt.model = model
    stt.hotwords = "Jarvis, Docker"
    stt.language = "pt"
    return stt


class Transcribe(unittest.TestCase):
    def test_no_previous_text_context(self):
        model = FakeModel()
        self.assertEqual(_backend(model).transcribe(np.zeros(16000, np.float32)), "olá mundo")
        self.assertEqual(len(model.calls), 1)
        self.assertIs(model.calls[0]["condition_on_previous_text"], False)
        self.assertEqual(model.calls[0]["hotwords"], "Jarvis, Docker")

    def test_retries_without_hotwords_on_prompt_overflow(self):
        for exc in (ValueError, RuntimeError):
            model = FakeModel(fail_with_hotwords=exc)
            self.assertEqual(_backend(model).transcribe(np.zeros(16000, np.float32)), "olá mundo")
            self.assertEqual([c["hotwords"] for c in model.calls], ["Jarvis, Docker", None])


if __name__ == "__main__":
    unittest.main()
