from __future__ import annotations

import importlib
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))


class LiveDictationTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('jarvis_dictation_stream'))
        return importlib.import_module('jarvis_dictation_stream')

    def test_only_confirmed_segments_are_delivered_once_and_audio_during_finish_is_kept(self) -> None:
        module = self.module()
        confirmed, previews, audio_seen = [], [], []
        entered, release = threading.Event(), threading.Event()

        class Backend:
            label = 'test'
            count = 0
            def begin(self, on_partial):
                self.count += 1
                number = self.count
                class Session:
                    label = 'test'
                    def feed(self, chunk):
                        audio_seen.append(chunk.copy())
                        on_partial('hipótese provisória')
                    def finish(self):
                        if number == 1:
                            entered.set()
                            release.wait(2)
                        return ['Primeiro trecho.', 'Segundo trecho.'][number - 1]
                    def close(self):
                        pass
                return Session()

        vad = lambda frame: np.array([float(np.any(frame))])
        session = module.LiveDictation(Backend(), vad, on_partial=previews.append, on_confirmed=confirmed.append)
        speech = np.full(2560, 4000, np.int16)
        silence = np.zeros(2560, np.int16)
        session.feed(speech)
        for _ in range(6):
            session.feed(silence)
        self.assertTrue(entered.wait(2))
        self.assertEqual(confirmed, [])
        session.feed(speech)
        release.set()
        self.assertEqual(session.finish(), 'Primeiro trecho. Segundo trecho.')
        self.assertEqual(confirmed, ['Primeiro trecho.', 'Segundo trecho.'])
        self.assertEqual(sum(np.count_nonzero(chunk) for chunk in audio_seen), 5120)
        self.assertTrue(any('hipótese provisória' in text for text in previews))

    def test_focus_change_stops_insertion_and_final_clipboard_has_full_text(self) -> None:
        module = self.module()
        output = []
        focus = ['0x1']
        with (patch.object(module, 'active_window_id', side_effect=lambda: focus[0]),
              patch.object(module.jarvis_dictate, 'paste_text', side_effect=lambda text, mode, **kw:
                           output.append((text, mode)) or ('typed' if mode == 'type' else 'clipboard'))):
            destination = module.LiveOutput('type', target='0x1')
            destination.deliver('Primeiro.')
            focus[0] = '0x2'
            destination.deliver('Segundo.')
            focus[0] = '0x1'
            destination.deliver('Terceiro.')
            destination.finish('Primeiro. Segundo. Terceiro.')
        self.assertEqual(output, [('Primeiro. ', 'type'), ('Primeiro. Segundo. Terceiro.', 'clipboard')])
        self.assertTrue(destination.blocked)

    def test_paste_mode_never_turns_spoken_letters_into_keyboard_shortcuts(self) -> None:
        module = self.module()
        delivered = []
        with (patch.object(module, 'active_window_id', return_value='0x1'),
              patch.object(module.jarvis_dictate, 'paste_text', side_effect=lambda text, mode, **kw:
                           delivered.append((text, mode)) or 'pasted')):
            destination = module.LiveOutput('paste', target='0x1')
            destination.deliver('primeiro trecho\nsem executar Enter')
        self.assertEqual(delivered, [('primeiro trecho sem executar Enter ', 'paste')])
        self.assertTrue(destination.inserted)
        self.assertFalse(destination.blocked)

    def test_insertion_waits_for_held_shortcut_modifiers_not_a_fixed_delay(self) -> None:
        module = self.module()
        clock = [0.0]
        inserted_while_held = []
        def run(command, **kwargs):
            if command[:2] == ['hyprctl', 'repl']:
                return type('Result', (), {'stdout': 'true\n' if clock[0] < 0.8 else 'false\n'})()
            if command[0] == 'wtype':
                inserted_while_held.append(clock[0] < 0.8)
            return type('Result', (), {'stdout': '{"address":"0x1"}'})()
        with (patch.dict(module.jarvis_dictate.os.environ, {'HYPRLAND_INSTANCE_SIGNATURE': 'test'}),
              patch.object(module.jarvis_dictate.shutil, 'which', return_value='/usr/bin/wtype'),
              patch.object(module.jarvis_dictate.time, 'monotonic', side_effect=lambda: clock[0]),
              patch.object(module.jarvis_dictate.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)),
              patch.object(module.jarvis_dictate.subprocess, 'run', side_effect=run)):
            result = module.jarvis_dictate.paste_text('p', 'type', target_window='0x1')
        self.assertEqual(result, 'typed')
        self.assertEqual(inserted_while_held, [False])

    def test_cancel_during_finalization_never_delivers_late_text(self) -> None:
        module = self.module()
        entered, release = threading.Event(), threading.Event()
        confirmed = []
        class Backend:
            label = 'test'
            def begin(self, on_partial):
                class Session:
                    def feed(self, chunk):
                        pass
                    def finish(self):
                        entered.set()
                        release.wait(2)
                        return 'não inserir'
                    def close(self):
                        release.set()
                return Session()
        session = module.LiveDictation(Backend(), lambda frame: np.array([float(np.any(frame))]),
                                        on_partial=lambda text: None, on_confirmed=confirmed.append)
        session.feed(np.full(2560, 4000, np.int16))
        for _ in range(6):
            session.feed(np.zeros(2560, np.int16))
        self.assertTrue(entered.wait(2))
        session.close()
        self.assertEqual(confirmed, [])

    def test_focus_change_during_modifier_release_delay_never_types(self) -> None:
        module = self.module()
        commands = []
        focus = ['0x1']
        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ['hyprctl', 'repl']:
                return type('Result', (), {'stdout': 'false\n'})()
            return type('Result', (), {'stdout': '{"address":"' + focus[0] + '"}'})()
        with (patch.object(module, 'active_window_id', side_effect=lambda: focus[0]),
              patch.object(module.jarvis_dictate.shutil, 'which', return_value='/usr/bin/wtype'),
              patch.object(module.jarvis_dictate.time, 'sleep', side_effect=lambda _: focus.__setitem__(0, '0x2')),
              patch.object(module.jarvis_dictate.subprocess, 'run', side_effect=run)):
            destination = module.LiveOutput('type', target='0x1')
            destination.deliver('Não enviar à outra janela.')
        self.assertFalse(any(command[0] == 'wtype' for command in commands))
        self.assertTrue(destination.blocked)

    def test_finish_observes_escape_while_waiting_and_does_not_insert(self) -> None:
        module = self.module()
        entered, release, escape = threading.Event(), threading.Event(), threading.Event()
        confirmed = []
        class Backend:
            label = 'test'
            def begin(self, on_partial):
                class Session:
                    def feed(self, chunk):
                        pass
                    def finish(self):
                        entered.set()
                        release.wait(2)
                        return 'não inserir'
                    def close(self):
                        release.set()
                return Session()
        session = module.LiveDictation(Backend(), lambda frame: np.array([1.0]),
                                      on_partial=lambda text: None, on_confirmed=confirmed.append)
        session.feed(np.full(2560, 4000, np.int16))
        timer = threading.Timer(0.1, escape.set)
        timer.start()
        try:
            session.finish(should_cancel=escape.is_set)
        finally:
            session.close()
            timer.join()
        self.assertTrue(entered.is_set())
        self.assertEqual(confirmed, [])

    def test_escape_during_modifier_release_delay_never_types(self) -> None:
        module = self.module()
        commands = []
        cancelled = threading.Event()
        with (patch.object(module.jarvis_dictate.shutil, 'which', return_value='/usr/bin/wtype'),
              patch.object(module.jarvis_dictate.time, 'sleep', side_effect=lambda _: cancelled.set()),
              patch.object(module.jarvis_dictate.subprocess, 'run', side_effect=lambda command, **kw: commands.append(command))):
            result = module.jarvis_dictate.paste_text('não inserir', 'type', should_cancel=cancelled.is_set)
        self.assertEqual(result, 'cancelled')
        self.assertFalse(any(command[0] == 'wtype' for command in commands))
