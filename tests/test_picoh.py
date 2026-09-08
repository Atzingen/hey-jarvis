"""Picoh — protocolo, envelope da fala e coreografia, sem robô e sem áudio.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_picoh -v
"""

from __future__ import annotations

import math
import random
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_picoh as picoh  # noqa: E402


def _wav(path: Path, segments: list[tuple[float, float]], rate: int = 16000) -> None:
    """Grava um wav mono 16-bit: lista de (segundos, amplitude 0-1) — 0 = silêncio."""
    frames = bytearray()
    for secs, amp in segments:
        for i in range(int(secs * rate)):
            v = int(amp * 20000 * math.sin(2 * math.pi * 220 * i / rate))
            frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))


class Envelope(unittest.TestCase):
    def test_silence_then_speech(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.wav"
            _wav(p, [(0.5, 0.0), (0.5, 1.0), (0.25, 0.3), (0.25, 0.0)])
            env = picoh.envelope(p, step=0.05)
        self.assertEqual(len(env), 30)                  # 1.5 s / 50 ms
        self.assertTrue(all(v == 0 for v in env[:10]))  # silêncio inicial
        self.assertEqual(max(env[10:20]), 10)           # trecho alto normaliza em 10
        self.assertTrue(all(0 < v < 10 for v in env[20:25]))
        self.assertTrue(all(v == 0 for v in env[25:]))

    def test_empty_or_silent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.wav"
            _wav(p, [(0.3, 0.0)])
            self.assertEqual(picoh.envelope(p), [0] * 6)

    def test_mouth_at(self):
        tts = {"t0": 100.0, "step": 0.05, "env": [0, 10, 5]}
        self.assertEqual(picoh.mouth_at(None, 100.0), 5.0)
        self.assertEqual(picoh.mouth_at(tts, 99.0), 5.0)        # antes de começar
        self.assertEqual(picoh.mouth_at(tts, 100.01), 5.0)      # env[0] = 0
        self.assertEqual(picoh.mouth_at(tts, 100.06), 8.0)      # env[1] = 10 -> todo aberto
        self.assertEqual(picoh.mouth_at(tts, 100.11), 6.5)      # env[2] = 5
        self.assertEqual(picoh.mouth_at(tts, 100.30), 5.0)      # acabou


class Protocol(unittest.TestCase):
    def setUp(self):
        self.link = picoh.FakeLink()
        self.robot = picoh.Picoh(self.link)

    def test_move_attaches_and_scales(self):
        # BottomLip: 34..151 graus, invertido: pos 8 -> 10-8=2 -> 34 + 11.7*2 = 57
        self.robot.move(picoh.BOTTOMLIP, 8, 10)
        self.assertEqual(self.link.sent, ["a05\n", "m05,57,250\n"])
        # mesma posição de novo: nada na serial
        self.robot.move(picoh.BOTTOMLIP, 8, 10)
        self.assertEqual(len(self.link.sent), 2)

    def test_head_turn_not_reversed(self):
        self.robot.move(picoh.HEADTURN, 7, 2)
        self.assertEqual(self.link.sent[-1], "m01,126,50\n")   # 0 + 18*7

    def test_bottom_lip_below_centre_is_halved(self):
        # pos 1 -> 5 - 4/2 = 3 -> invertido 7 -> 34 + 11.7*7 = 115
        self.robot.move(picoh.BOTTOMLIP, 1)
        self.assertEqual(self.link.sent[-1], "m05,115,125\n")

    def test_colour_dedup_and_clamp(self):
        self.robot.colour(300, -5, 128)
        self.robot.colour(255, 0, 128)
        self.assertEqual(self.link.sent, ["l00,255,0,128\n", "l01,255,0,128\n"])

    def test_eye_brightness_quadratic(self):
        self.robot.eye_brightness(10)
        self.robot.eye_brightness(5)
        self.assertEqual(self.link.sent, ["FI,255\n", "FI,64\n"])

    def test_eyes_reduced_scale(self):
        self.robot.eyes(5, 5)
        self.robot.eyes(10, 0)
        self.assertEqual(self.link.sent, ["FE,0,128,128\n", "FE,0,178,76\n"])

    def test_lids(self):
        self.robot.lids(1)
        self.robot.lids(0)
        self.assertEqual(self.link.sent, ["FL,+0,255\n", "FL,+0,0\n"])

    def test_eye_shape_sets(self):
        self.robot.eye_shape("eyeball")
        sets = [m.split(",")[1] for m in self.link.sent]
        self.assertEqual(sets, ["0", "1", "2", "3", "4", "8"])
        # primeira linha do set 0: 38 -> esquerdo 38, direito com bits invertidos 1C
        self.assertTrue(self.link.sent[0].startswith("FB,0,381C,"))
        self.robot.eye_shape("EYEBALL")           # mesmo formato: nada
        self.assertEqual(len(self.link.sent), 6)
        self.robot.eye_shape("nope")              # desconhecido: ignora
        self.assertEqual(len(self.link.sent), 6)

    def test_reverse_bits(self):
        self.assertEqual(picoh._reverse_bits("01"), "80")
        self.assertEqual(picoh._reverse_bits("38"), "1C")
        self.assertEqual(picoh._reverse_bits("FF"), "FF")

    def test_close_detaches_only_attached(self):
        self.robot.move(picoh.HEADNOD, 5)
        self.robot.move(picoh.HEADTURN, 5)
        self.link.sent.clear()
        self.robot.close()
        self.assertEqual(self.link.sent, ["d00\n", "d01\n"])


class PortDiscovery(unittest.TestCase):
    def test_find_port_uses_handshake(self):
        asked: list[str] = []

        def check(port: str) -> bool:
            asked.append(port)
            return port == "/dev/ttyACM1"

        self.assertEqual(picoh.find_port(["/dev/ttyUSB0", "/dev/ttyACM1", "/dev/ttyACM2"], check),
                         "/dev/ttyACM1")
        self.assertEqual(asked, ["/dev/ttyUSB0", "/dev/ttyACM1"])
        self.assertIsNone(picoh.find_port([], check))


class Choreography(unittest.TestCase):
    def setUp(self):
        self.link = picoh.FakeLink()
        self.face = picoh.Face(picoh.Picoh(self.link), random.Random(0))

    def _cmds(self, prefix: str) -> list[str]:
        return [m for m in self.link.sent if m.startswith(prefix)]

    def test_idle_when_no_state(self):
        self.face.tick(None, None, 1000.0)
        self.assertIn("l00,0,0,0\n", self.link.sent)
        self.assertEqual(self._cmds("m05"), ["m05,92,250\n"])   # boca fechada (pos 5)

    def test_phase_colours(self):
        self.face.tick({"phase": "listening"}, None, 1000.0)
        self.assertIn("l00,0,200,64\n", self.link.sent)          # verde (quantizado em 8)
        self.link.sent.clear()
        self.face.tick({"phase": "thinking"}, None, 1000.0)
        self.assertTrue(any(m.startswith("l00,") for m in self.link.sent))
        self.assertTrue(any(m.startswith("FB,") for m in self.link.sent))  # olhos smallball
        self.link.sent.clear()
        self.face.tick({"phase": "consent"}, None, 1000.0)
        self.assertIn("l00,255,16,0\n", self.link.sent)           # vermelho, meio-ciclo aceso

    def test_consent_blinks_base(self):
        self.face.tick({"phase": "consent"}, None, 1000.0)
        self.face.tick({"phase": "consent"}, None, 1000.3)
        colours = [m for m in self.link.sent if m.startswith("l00,")]
        self.assertEqual(colours, ["l00,255,16,0\n", "l00,40,0,0\n"])

    def test_mouth_follows_envelope_in_any_phase(self):
        tts = {"t0": 1000.0, "step": 0.05, "env": [10, 0]}
        self.face.tick({"phase": "thinking"}, tts, 1000.01)      # narração enquanto pensa
        self.assertIn("m05,57,250\n", self.link.sent)             # aberto (pos 8)
        self.link.sent.clear()
        self.face.tick({"phase": "thinking"}, tts, 1000.06)
        self.assertIn("m05,92,250\n", self.link.sent)             # fechado
        self.link.sent.clear()
        self.face.tick({"phase": "thinking"}, None, 1000.5)
        self.assertEqual(self._cmds("m05"), [])                   # já fechado: nada a enviar

    def test_flash_phase_falls_back_to_idle(self):
        self.face.tick({"phase": "pasted"}, None, 1000.0)
        self.assertIn("l00,0,255,80\n", self.link.sent)
        self.link.sent.clear()
        self.face.tick({"phase": "pasted"}, None, 1003.0)
        self.assertIn("l00,0,0,0\n", self.link.sent)

    def test_blink_closes_then_opens(self):
        self.face.tick({"phase": "listening"}, None, 1000.0)    # agenda a 1ª piscada
        self.assertEqual(self._cmds("FL,")[-1], "FL,+0,255\n")   # next_blink=0 -> pisca já
        self.face.tick({"phase": "listening"}, None, 1000.2)
        self.assertEqual(self._cmds("FL,")[-1], "FL,+0,0\n")

    def test_wandering_eyes_change_target(self):
        seen = set()
        for i in range(60):
            self.face.tick({"phase": "thinking"}, None, 1000.0 + i * 0.1)
        seen = {m for m in self.link.sent if m.startswith("FE,")}
        self.assertGreater(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
