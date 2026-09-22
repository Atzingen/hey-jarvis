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


class FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _session(on_partial=None) -> jarvis_stt.OpenAISession:
    """OpenAISession sem rede: só o que _handle_frame usa."""
    import threading
    s = jarvis_stt.OpenAISession.__new__(jarvis_stt.OpenAISession)
    s.on_partial = on_partial
    s.partial = ""
    s.final = None
    s.error = None
    s.ws = FakeSocket()
    s.done = threading.Event()
    s.closed = False
    s.session_bytes = 0
    s.events = 0
    return s


def _delta(text: str) -> str:
    import json
    return json.dumps({"type": "conversation.item.input_audio_transcription.delta", "delta": text})


class RealtimeCeilings(unittest.TestCase):
    """Frames do servidor OpenAI são entrada não confiável: tetos e formato antes de guardar."""

    def test_deltas_accumulate_and_completed_ends_session(self):
        import json
        seen = []
        s = _session(on_partial=seen.append)
        self.assertTrue(s._handle_frame(_delta("olá ")))
        self.assertTrue(s._handle_frame(_delta("mundo")))
        self.assertEqual(seen, ["olá ", "olá mundo"])
        done = json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                           "transcript": " olá mundo "})
        self.assertFalse(s._handle_frame(done))
        self.assertEqual(s.final, "olá mundo")
        self.assertIsNone(s.error)
        self.assertTrue(s.done.is_set())

    def test_unknown_events_are_ignored(self):
        s = _session()
        self.assertTrue(s._handle_frame('{"type": "session.created", "session": {}}'))
        self.assertIsNone(s.error)

    def _assert_failed_closed(self, s, reason_part: str):
        self.assertIn(reason_part, s.error)
        self.assertTrue(s.closed)
        self.assertTrue(s.done.is_set())
        self.assertIsNone(s.ws)

    def test_text_ceiling_rejects_delta_before_retaining_it(self):
        s = _session()
        s.partial = "x" * (jarvis_stt.OPENAI_MAX_TEXT_CHARS - 1)
        self.assertFalse(s._handle_frame(_delta("ab")))
        self._assert_failed_closed(s, "teto de texto")
        self.assertEqual(len(s.partial), jarvis_stt.OPENAI_MAX_TEXT_CHARS - 1)

    def test_text_ceiling_applies_to_final_transcript(self):
        import json
        s = _session()
        done = json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                           "transcript": "y" * (jarvis_stt.OPENAI_MAX_TEXT_CHARS + 1)})
        self.assertFalse(s._handle_frame(done))
        self._assert_failed_closed(s, "teto de texto")
        self.assertIsNone(s.final)

    def test_event_count_ceiling(self):
        s = _session()
        s.events = jarvis_stt.OPENAI_MAX_EVENTS
        self.assertFalse(s._handle_frame('{"type": "noop"}'))
        self._assert_failed_closed(s, "teto de eventos")

    def test_session_bytes_ceiling(self):
        s = _session()
        s.session_bytes = jarvis_stt.OPENAI_MAX_SESSION_BYTES - 5
        self.assertFalse(s._handle_frame('{"type": "noop"}'))
        self._assert_failed_closed(s, "teto de bytes")

    def test_nonconforming_frames_close_the_socket(self):
        cases = {
            "binário": b"\x00\x01",
            "não é JSON": "{not json",
            "fora do formato": "[1, 2, 3]",
            "delta fora do formato": '{"type": "conversation.item.input_audio_transcription.delta", "delta": 7}',
            "transcript fora do formato": '{"type": "conversation.item.input_audio_transcription.completed", "transcript": {"a": 1}}',
        }
        for reason, frame in cases.items():
            s = _session()
            self.assertFalse(s._handle_frame(frame), reason)
            self._assert_failed_closed(s, reason)
            self.assertEqual(s.partial, "")

    def test_api_error_is_reported_without_closing_twice(self):
        s = _session()
        self.assertFalse(s._handle_frame('{"type": "error", "error": {"code": "bad", "message": "m"}}'))
        self.assertEqual(s.error, "api: bad: m")
        self.assertTrue(s.done.is_set())

    def test_frame_size_ceiling_is_finite(self):
        self.assertGreater(jarvis_stt.OPENAI_MAX_FRAME_BYTES, 0)
        self.assertLess(jarvis_stt.OPENAI_MAX_FRAME_BYTES, jarvis_stt.OPENAI_MAX_SESSION_BYTES)


if __name__ == "__main__":
    unittest.main()
