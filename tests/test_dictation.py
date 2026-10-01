from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))
import jarvis_dictate as dictate

spec = importlib.util.spec_from_file_location("dictation_launcher", BIN / "voice-launcher.py")
vl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vl)


class InterruptiblePolishTest(unittest.TestCase):
    def test_skip_during_blocking_generation_stops_child(self) -> None:
        real_popen = subprocess.Popen
        children: list[subprocess.Popen] = []
        cancel = threading.Event()

        def launch(*args, **kwargs):
            child = real_popen([sys.executable, "-c",
                                'import json,sys,time; json.load(sys.stdin); time.sleep(30); print(json.dumps("late"))'],
                               **kwargs)
            children.append(child)
            return child

        timer = threading.Timer(0.2, cancel.set)
        original = "uma frase ainda sem pontuação " * 80
        timer.start()
        try:
            with patch.object(dictate.subprocess, "Popen", side_effect=launch):
                start = time.monotonic()
                result = dictate.polish_interruptible(original, should_skip=cancel.is_set)
            self.assertEqual(result, original)
            self.assertLess(time.monotonic() - start, 2)
            self.assertIsNotNone(children[0].poll())
        finally:
            timer.cancel()
            timer.join()

    def test_skip_stops_worker_and_returns_entire_original(self) -> None:
        real_popen = subprocess.Popen
        children: list[subprocess.Popen] = []
        cancel = threading.Event()

        def launch(*args, **kwargs):
            child = real_popen(
                [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"],
                **kwargs,
            )
            children.append(child)
            cancel.set()
            return child

        with patch.object(dictate.subprocess, "Popen", side_effect=launch):
            start = time.monotonic()
            result = dictate.polish_interruptible("texto bruto sem revisão", should_skip=cancel.is_set)
        self.assertEqual(result, "texto bruto sem revisão")
        self.assertLess(time.monotonic() - start, 2)
        self.assertIsNotNone(children[0].poll())

    def test_finished_worker_returns_revision(self) -> None:
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            return real_popen([sys.executable, "-c",
                              'import json,sys; json.load(sys.stdin); print(json.dumps("Texto revisado."))'],
                             **kwargs)

        with patch.object(dictate.subprocess, "Popen", side_effect=launch):
            result = dictate.polish_interruptible("texto revisado")
        self.assertEqual(result, "Texto revisado.")

    def test_worker_failure_preserves_original(self) -> None:
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import sys; sys.exit(2)"], **kwargs)

        with patch.object(dictate.subprocess, "Popen", side_effect=launch):
            self.assertEqual(dictate.polish_interruptible("original"), "original")


class DictationFlowTest(unittest.TestCase):
    def test_live_dictation_skips_polish_and_never_pastes_complete_text_again(self) -> None:
        import numpy as np
        import jarvis_dictation_stream as live
        output = []
        class Window:
            def __init__(self, **kwargs): pass
            def open(self, **kwargs): pass
            def update(self, **kwargs): pass
            def close(self): pass
        class Backend:
            label = 'nemotron/cpu'
            def begin(self, on_partial):
                return SimpleNamespace(feed=lambda chunk: on_partial('texto provisório'),
                                       finish=lambda: 'Texto confirmado.', close=lambda: None,
                                       label='nemotron/cpu')
        class Microphone:
            reads = 0
            def read(self, size):
                self.reads += 1
                if self.reads == 4:
                    vl.DICT.set()
                return np.full((size, 1), 1000, np.int16), False
        def deliver(text, mode, **kwargs):
            output.append((text, mode))
            return {'type': 'typed', 'paste': 'pasted', 'clipboard': 'clipboard'}[mode]
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(vl, 'JarvisWindow', Window), patch.object(vl, 'chime'),
                  patch.object(vl, 'QUIT_FLAG', Path(folder)/'quit'), patch.object(vl.time, 'sleep'),
                  patch.object(dictate, 'POLISHING_FILE', Path(folder)/'polishing'),
                  patch.object(dictate, 'SKIP_POLISH_FILE', Path(folder)/'skip'),
                  patch.object(dictate, 'set_recording'), patch.object(dictate, 'restore_volume'),
                  patch.object(dictate, 'take_command', return_value='stop'),
                  patch.object(dictate, 'paste_text', side_effect=deliver),
                  patch.object(dictate, 'polish_interruptible', side_effect=AssertionError('streaming must not polish')),
                  patch.object(live, 'active_window_id', return_value='0x1'),
                  patch.dict(vl.CFG, dictation_live=True, dictation_polish=True, dictation_output='paste')):
                vl.DICT.clear()
                vl.run_dictation(Microphone(), Backend(), SimpleNamespace(test=True),
                                 vad_model=lambda frame: np.array([1.0]))
        self.assertEqual(output, [('Texto confirmado. ', 'paste'), ('Texto confirmado.', 'clipboard')])

    def run_flow(self, polish: bool, skip: bool = False, cancel: bool = False) -> tuple[list[tuple[str, str]], list[str]]:
        output: list[tuple[str, str]] = []
        phases: list[str] = []

        class Window:
            def __init__(self, **kwargs):
                pass

            def open(self, **kwargs):
                pass

            def update(self, phase=None, **kwargs):
                if phase:
                    phases.append(phase)

            def close(self):
                pass

        def revise(text, *args, **kwargs):
            self.assertEqual(output, [("texto bruto", "clipboard")])
            self.assertTrue(dictate.POLISHING_FILE.exists())
            if skip:
                dictate.SKIP_POLISH_FILE.touch()
            if cancel:
                vl.QUIT_FLAG.touch()
            if skip or cancel:
                self.assertTrue(kwargs["should_skip"]())
            return "Texto revisado."

        def deliver(text, mode, **kwargs):
            if mode == "paste":
                self.assertEqual(phases[-1], "delivering")
                self.assertFalse(dictate.POLISHING_FILE.exists())
            output.append((text, mode))
            return "clipboard" if mode == "clipboard" else "pasted"

        session = SimpleNamespace(finish=lambda: "texto bruto", close=lambda: None)
        stt = SimpleNamespace(begin=lambda **kwargs: session)
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(vl, "JarvisWindow", Window),
                  patch.object(vl, "QUIT_FLAG", Path(folder) / "quit"),
                  patch.object(dictate, "POLISHING_FILE", Path(folder) / "polishing", create=True),
                  patch.object(dictate, "SKIP_POLISH_FILE", Path(folder) / "skip", create=True),
                  patch.object(vl, "chime"), patch.object(vl.time, "sleep"),
                  patch.dict(vl.CFG, dictation_live=False, dictation_polish=polish, dictation_output="paste"),
                  patch.object(dictate, "set_recording"), patch.object(dictate, "restore_volume"),
                  patch.object(dictate, "take_command", return_value="stop"),
                  patch.object(dictate, "polish", side_effect=revise),
                  patch.object(dictate, "polish_interruptible", side_effect=revise, create=True),
                  patch.object(dictate, "paste_text", side_effect=deliver)):
                vl.DICT.set()
                vl.run_dictation(None, stt, SimpleNamespace(test=True))
                self.assertFalse(dictate.POLISHING_FILE.exists())
                self.assertFalse(dictate.SKIP_POLISH_FILE.exists())
        return output, phases

    def test_raw_clipboard_is_ready_before_polish(self) -> None:
        output, phases = self.run_flow(polish=True)
        self.assertEqual(output, [("texto bruto", "clipboard"), ("Texto revisado.", "paste")])
        self.assertIn("polishing", phases)

    def test_disabled_polish_delivers_raw_once(self) -> None:
        output, phases = self.run_flow(polish=False)
        self.assertEqual(output, [("texto bruto", "paste")])
        self.assertNotIn("polishing", phases)

    def test_skip_wins_over_simultaneous_revision_result(self) -> None:
        output, _ = self.run_flow(polish=True, skip=True)
        self.assertEqual(output, [("texto bruto", "clipboard"), ("texto bruto", "paste")])

    def test_closing_window_cancels_without_pasting(self) -> None:
        output, phases = self.run_flow(polish=True, cancel=True)
        self.assertEqual(output, [("texto bruto", "clipboard")])
        self.assertIn("cancelled", phases)


class TalkCliTest(unittest.TestCase):
    def test_failed_start_removes_pending_talk(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stub = root / "systemctl"
            stub.write_text('#!/bin/sh\nexit 1\n')
            stub.chmod(0o755)
            env = {**os.environ, "PATH": str(root) + ":" + os.environ["PATH"], "XDG_RUNTIME_DIR": str(root)}
            result = subprocess.run(["bash", str(BIN / "jarvis"), "talk"], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "jarvis-talk").exists())

    def test_skip_only_requests_polish_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = {**os.environ, "XDG_RUNTIME_DIR": str(root)}
            for active in (False, True):
                if active:
                    (root / "jarvis-polishing").write_text(str(os.getpid()))
                result = subprocess.run(["bash", str(BIN / "jarvis"), "dictate", "skip"], env=env, capture_output=True)
                self.assertEqual(result.returncode, 0)
                self.assertEqual((root / "jarvis-skip-polish").exists(), active)
                self.assertFalse((root / "jarvis-dictate.cmd").exists())

    def test_talk_starts_service_and_queues_without_unsafe_signal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "systemctl.log"
            stub = fake_bin / "systemctl"
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$JARVIS_TEST_LOG"\nexit 0\n')
            stub.chmod(0o755)
            env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                   "XDG_RUNTIME_DIR": str(root), "JARVIS_TEST_LOG": str(log)}
            result = subprocess.run(["bash", str(BIN / "jarvis"), "talk"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((root / "jarvis-talk").exists())
            operations = log.read_text()
            self.assertIn("start voice-launcher.service", operations)
            self.assertNotIn("SIGUSR1", operations)

    def test_pending_talk_is_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / "jarvis-talk"
            request.touch()
            with patch.object(vl, "TALK_REQUEST_FILE", request, create=True):
                self.assertTrue(vl.take_talk_request())
                self.assertFalse(vl.take_talk_request())


if __name__ == "__main__":
    unittest.main()
