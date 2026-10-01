"""Ditado contínuo: prévia imediata, confirmação nas pausas e escrita sem duplicar."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
import json
import queue
import subprocess
import threading

import numpy as np

import jarvis_dictate


def active_window_id() -> str:
    try:
        result = subprocess.run(['hyprctl', 'activewindow', '-j'], capture_output=True,
                                text=True, check=True, timeout=1)
        return str(json.loads(result.stdout).get('address') or '')
    except (OSError, ValueError, subprocess.SubprocessError):
        return ''


class LiveOutput:
    def __init__(self, mode: str, target: str, dry_run: bool = False):
        self.mode = mode
        self.target = target
        self.dry_run = dry_run
        self.blocked = not target and not dry_run and mode != 'clipboard'
        self.inserted = False
        self.should_cancel: Callable[[], bool] | None = None

    def deliver(self, text: str) -> None:
        if self.mode == 'clipboard' or self.blocked:
            return
        if not self.dry_run and active_window_id() != self.target:
            self.blocked = True
            return
        # Uma quebra de linha colada num terminal pode executar um comando.
        phrase = ' '.join(text.rstrip().replace('\r', '\n').split('\n')) + ' '
        result = jarvis_dictate.paste_text(phrase, self.mode, dry_run=self.dry_run,
                                          target_window=self.target, should_cancel=self.should_cancel)
        self.inserted = self.inserted or result in ('pasted', 'typed', 'dry-run')
        self.blocked = result not in ('pasted', 'typed', 'dry-run')

    def finish(self, text: str) -> str:
        if text:
            jarvis_dictate.paste_text(text, 'clipboard', dry_run=self.dry_run)
        return 'pasted' if self.inserted and not self.blocked else 'copied'


class LiveDictation:
    """O worker finaliza uma frase enquanto o microfone continua alimentando a fila."""

    def __init__(self, backend, vad, on_partial: Callable[[str], None],
                 on_confirmed: Callable[[str], None], threshold: float = 0.5):
        self.backend, self.vad = backend, vad
        self.on_partial, self.on_confirmed = on_partial, on_confirmed
        self.threshold = threshold
        self.label = getattr(backend, 'label', '')
        self.parts: list[str] = []
        self.queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self.cancelled = threading.Event()
        self.session = None
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True, name='dictation-stream')
        self.thread.start()

    def feed(self, chunk: np.ndarray) -> None:
        if not self.cancelled.is_set():
            self.queue.put(chunk.copy())

    def _preview(self, text: str) -> None:
        if not self.cancelled.is_set():
            if text and self.session is not None:
                self.label = getattr(self.session, 'label', self.label)
            self.on_partial(' '.join(self.parts + [text]).strip())

    def _confirm(self) -> None:
        if self.session is None:
            return
        session = self.session
        try:
            text = session.finish().strip()
            self.label = getattr(session, 'label', self.label)
        finally:
            session.close()
            self.session = None
        if text and not self.cancelled.is_set():
            self.parts.append(text)
            self.on_confirmed(text)
            self._preview('')

    def _run(self) -> None:
        pending = np.empty(0, dtype=np.float32)
        preroll: deque[np.ndarray] = deque(maxlen=5)
        has_speech = False
        probability = 0.0
        silence_samples = 0
        segment_samples = 0
        try:
            while not self.cancelled.is_set():
                chunk = self.queue.get()
                if chunk is None:
                    break
                pending = np.concatenate([pending, chunk.astype(np.float32) / 32768.0])
                while len(pending) >= 2560:
                    frame, pending = pending[:2560], pending[2560:]
                    probability = float(self.vad(frame).max())
                speech = probability >= self.threshold
                if self.session is None:
                    preroll.append(chunk)
                    if not speech:
                        continue
                    self.session = self.backend.begin(on_partial=self._preview)
                    for saved in preroll:
                        self.session.feed(saved)
                        segment_samples += len(saved)
                    preroll.clear()
                else:
                    self.session.feed(chunk)
                    segment_samples += len(chunk)
                has_speech = has_speech or speech
                silence_samples = 0 if speech else silence_samples + len(chunk)
                if has_speech and (silence_samples >= 12800 or segment_samples >= 45 * 16000):
                    self._confirm()
                    has_speech = False
                    segment_samples = silence_samples = 0
            if not self.cancelled.is_set():
                self._confirm()
        except Exception as error:
            self.error = error
        finally:
            if self.session is not None:
                self.session.close()

    def finish(self, should_cancel: Callable[[], bool] | None = None) -> str:
        self.queue.put(None)
        while self.thread.is_alive():
            if should_cancel is not None and should_cancel():
                self.close()
                return ' '.join(self.parts)
            self.thread.join(timeout=0.05)
        if self.error is not None:
            raise self.error
        return ' '.join(self.parts)

    def close(self) -> None:
        self.cancelled.set()
        if self.session is not None:
            self.session.close()
        self.queue.put(None)
        self.thread.join(timeout=1)
