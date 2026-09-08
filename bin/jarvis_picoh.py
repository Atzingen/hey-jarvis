#!/usr/bin/python3
"""Picoh (o robô da Ohbot) como rosto do Jarvis.

O robô é um espectador dos mesmos arquivos que a janela da conversa lê:

    $XDG_RUNTIME_DIR/jarvis-state.json   fase da conversa (escrito pelo voice-launcher)
    $XDG_RUNTIME_DIR/jarvis-tts.json     envelope de volume da fala em curso (escrito por tts())

Este módulo roda como daemon (`jarvis_picoh.py`, lançado pelo voice-launcher
quando picoh != off) e a cada 50 ms traduz fase + envelope em comandos seriais:
cor da base por fase (verde ouvindo, âmbar pensando, vermelho pedindo
autorização, azul falando…), boca abrindo no ritmo da voz do Piper, olhos que
vagueiam enquanto o modelo trabalha, piscadas ocasionais. Sem robô conectado o
daemon fica esperando o dispositivo aparecer; nada no launcher depende dele.

Protocolo serial (19200 baud, o mesmo da biblioteca oficial picoh-python):
    v\\n                  handshake: o robô responde "v2"
    a0<m>\\n              liga o motor m;  d0<m>\\n desliga
    m0<m>,<graus>,<vel>\\n move o motor m (vel 0-250)
    l00,r,g,b\\n l01,r,g,b\\n  LEDs da base (0-255)
    FI,<0-255>\\n         brilho dos olhos
    FE,0,<x>,<y>\\n       posição das pupilas (0-255)
    FL,+0,<0-255>\\n      pálpebras (0 aberto, 255 fechado)
    FB,<set>,<18 bytes hex>\\n  formato dos olhos (sets 0-4 e 8)

Uso direto:
    jarvis_picoh.py --probe          # procura o robô e mostra a porta
    jarvis_picoh.py --demo           # percorre as fases (com --fake não precisa do robô)
    jarvis_picoh.py --reset          # cor apagada, olhos padrão, motores em repouso
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import subprocess
import sys
import time
import wave
from pathlib import Path

_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE_FILE = _RUNTIME_DIR / "jarvis-state.json"
TTS_FILE = _RUNTIME_DIR / "jarvis-tts.json"

BAUD = 19200
HANDSHAKE_REPLY = b"v2"
ENVELOPE_STEP = 0.05          # s por amostra do envelope da fala (20 Hz)
TICK = 0.05                   # período do loop do daemon
RESCAN_SECONDS = 3.0          # com que frequência olhar se apareceu uma porta nova

# --- motores (MotorDefinitionsPicoh.omd da lib oficial) ----------------------
# índice -> (mín graus, máx graus, invertido). Posições na API vão de 0 a 10.
HEADNOD, HEADTURN, EYETURN, LIDBLINK, TOPLIP, BOTTOMLIP, EYETILT = range(7)
MOTORS: dict[int, tuple[int, int, bool]] = {
    HEADNOD: (54, 111, True),
    HEADTURN: (0, 180, False),
    TOPLIP: (0, 99, True),
    BOTTOMLIP: (34, 151, True),
}
REST = {HEADNOD: 5, HEADTURN: 5, TOPLIP: 5, BOTTOMLIP: 5}

# --- formatos de olho (ohbot.obe da lib oficial): nome -> (hex, auto-espelha) --
EYE_SHAPES: dict[str, tuple[str, bool]] = {
    "angry": ("2070F8FCFE7C3800000070F8FCFE3C000000000078FCFC38000000000000F8FC0000000000000000F800000000000010381000000000", True),
    "eyeball": ("387CFEFEFE7C380000007CFEFEFE7C00000000007CFE7C00000000000000FE7C00000000000000827C00000000000010381000000000", True),
    "heart": ("6CFEFEFE7C38100000007CFEFE7C3810000000007CFE7C38100000000000FE7C38000000000000007C00000000000010381000000000", True),
    "large": ("387CFEFEFE7C380000007CFEFEFE7C00000000007CFE7C00000000000000FE7C00000000000000827C000000000010387C3810000000", True),
    "sad": ("081C3E7EFE7C380000001C3E7EFE7C00000000003C7E7E3C0000000000003E7E00000000000000003E00000000000010381000000000", True),
    "smallball": ("00387C7C7C3800000000107C7C7C100000000000387C3800000000000000380000000000000000000000000000000000100000000000", True),
    "square": ("7CFEFEFEFEFE7C0000007CFEFEFE7C00000000007CFE7C00000000000000FE7C00000000000000827C00000000000010381000000000", True),
    "sunglasses": ("FE838282FE00000000FEC78282FE00000000FEFF8282FE00000000FEFFC682FE00000000FEFFFE82FE00000000000000000000000000", True),
    "glasses": ("FEFFFEFEFE00000000FEFFFEFEFE00000000FEFFFEFEFE00000000FEFFFEFEFE00000000FEFFFEFEFE00000000001038100000000000", True),
    "full": ("ffffffffffffffffffffffffffffffff0000ffffffff0000000000ffff00000000000000000000000000000000000000000000000000", True),
}

# --- aparência por fase -------------------------------------------------------
# colour: RGB 0-255 da base | pulse: None, "breathe" (senoide lenta), "blink" (2 Hz),
# "fast" (senoide rápida) | shape: formato dos olhos | bright: brilho 0-10 |
# nod/turn: cabeça 0-10 | eyes: pupilas (x, y) 0-10 | wander: olhos vagueiam
Look = dict
IDLE: Look = dict(colour=(0, 0, 0), pulse=None, shape="eyeball", bright=3,
                  nod=5, turn=5, eyes=(5, 5), wander=False)
LOOKS: dict[str, Look] = {
    # cada fase da conversa tem formato de olho e cor próprios
    "listening":    dict(colour=(0, 220, 60), pulse=None, shape="large", bright=10,
                         nod=6, turn=5, eyes=(5, 5), wander=False),
    "recording":    dict(colour=(140, 255, 0), pulse="fast", shape="full", bright=10,
                         nod=6.5, turn=5, eyes=(5, 5), wander=False),
    "transcribing": dict(colour=(0, 160, 255), pulse=None, shape="square", bright=8,
                         nod=5.5, turn=5, eyes=(5, 6), wander=False),
    "thinking":     dict(colour=(255, 120, 0), pulse="breathe", shape="smallball", bright=8,
                         nod=6.5, turn=5, eyes=(4, 8), wander=True),
    "consent":      dict(colour=(255, 20, 0), pulse="blink", shape="angry", bright=10,
                         nod=5, turn=5, eyes=(5, 5), wander=False),
    "speaking":     dict(colour=(90, 110, 255), pulse=None, shape="heart", bright=10,
                         nod=5.5, turn=5, eyes=(5, 5), wander=False),
    "followup":     dict(colour=(0, 170, 140), pulse=None, shape="glasses", bright=8,
                         nod=6, turn=5, eyes=(5, 5), wander=False),
    "handoff":      dict(colour=(170, 0, 255), pulse="breathe", shape="sunglasses", bright=8,
                         nod=5, turn=5, eyes=(5, 5), wander=False),
    # ditado (outro modo): ciano enquanto grava, âmbar revisando, coração colou, triste cancelou
    "dictating":    dict(colour=(0, 220, 255), pulse="fast", shape="large", bright=10,
                         nod=4.5, turn=5, eyes=(5, 3), wander=False),
    "polishing":    dict(colour=(255, 120, 0), pulse="breathe", shape="smallball", bright=8,
                         nod=5, turn=5, eyes=(5, 5), wander=True),
    "pasted":       dict(colour=(0, 255, 80), pulse=None, shape="heart", bright=10,
                         nod=5, turn=5, eyes=(5, 5), wander=False),
    "copied":       dict(colour=(0, 255, 80), pulse=None, shape="heart", bright=10,
                         nod=5, turn=5, eyes=(5, 5), wander=False),
    "cancelled":    dict(colour=(255, 0, 0), pulse=None, shape="sad", bright=6,
                         nod=4, turn=5, eyes=(5, 3), wander=False),
}
# fases que são só um "flash" antes de voltar ao repouso
FLASH_PHASES = {"pasted": 2.0, "copied": 2.0, "cancelled": 2.0}
WANDER_TARGETS = [(3, 8), (7, 8), (4, 7), (6, 7), (5, 9)]


# --- envelope da fala ---------------------------------------------------------

def envelope(wav_path: str | os.PathLike, step: float = ENVELOPE_STEP) -> list[int]:
    """Volume RMS por janela de `step` segundos, normalizado a 0-10 (0 = silêncio).

    É o que abre a boca do robô: um valor por 50 ms, alinhado ao início da
    reprodução. Piso de ruído em 12 % do pico pra pausas ficarem de boca fechada."""
    import numpy as np

    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2 or not raw:
        return []
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    win = max(1, int(rate * step))
    n = len(samples) // win
    if n == 0:
        return []
    frames = samples[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return [0] * n
    norm = rms / peak
    norm[norm < 0.12] = 0.0
    return [int(round(float(v) * 10)) for v in norm]


def publish_tts(wav_path: str | os.PathLike, t0: float | None = None) -> None:
    """Chamado pelo launcher logo antes do paplay: envelope + instante inicial."""
    try:
        env = envelope(wav_path)
    except Exception as e:  # wav estranho: sem boca, sem drama
        print(f"   [picoh: envelope falhou: {e}]")
        return
    data = {"t0": time.time() if t0 is None else t0, "step": ENVELOPE_STEP, "env": env}
    try:
        tmp = TTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(TTS_FILE)
    except OSError as e:
        print(f"   [picoh: falha escrevendo envelope: {e}]")


def clear_tts() -> None:
    """Fala terminou ou foi cortada: boca fecha."""
    TTS_FILE.unlink(missing_ok=True)


def mouth_at(tts: dict | None, now: float) -> float:
    """Posição do lábio inferior (5 fechado … 8 aberto) pro envelope no instante `now`."""
    if not tts:
        return 5.0
    env = tts.get("env") or []
    step = float(tts.get("step") or ENVELOPE_STEP)
    idx = int((now - float(tts.get("t0", 0))) / step)
    if idx < 0 or idx >= len(env):
        return 5.0
    return 5.0 + max(0, min(10, env[idx])) * 0.3


# --- ligação serial -----------------------------------------------------------

class FakeLink:
    """Substitui a serial: guarda (e opcionalmente imprime) os comandos."""

    def __init__(self, echo: bool = False):
        self.sent: list[str] = []
        self.echo = echo

    def write(self, msg: str) -> None:
        self.sent.append(msg)
        if self.echo:
            print(f"[picoh→] {msg.rstrip()}")

    def close(self) -> None:
        pass


class SerialLink:
    def __init__(self, port: str):
        import serial

        self.ser = serial.Serial(port, BAUD, timeout=0.5, write_timeout=1.0)
        self.port = port

    def write(self, msg: str) -> None:
        self.ser.write(msg.encode("latin-1"))

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


def serial_candidates() -> list[str]:
    """Portas seriais USB presentes (by-id resolve pra ttyACM*/ttyUSB*)."""
    found: set[str] = set()
    for link in glob.glob("/dev/serial/by-id/*"):
        found.add(os.path.realpath(link))
    for dev in glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"):
        found.add(dev)
    return sorted(found)


def handshake(port: str, settle: float = 0.3) -> bool:
    """Manda "v" e espera "v2" — é assim que a lib oficial reconhece o Picoh."""
    try:
        import serial

        with serial.Serial(port, BAUD, timeout=1.0, write_timeout=1.0) as ser:
            time.sleep(settle)
            ser.reset_input_buffer()
            ser.write(b"v\n")
            return HANDSHAKE_REPLY in ser.readline()
    except Exception:
        return False


def find_port(candidates: list[str] | None = None, check=handshake) -> str | None:
    for port in (serial_candidates() if candidates is None else candidates):
        if check(port):
            return port
    return None


# --- o robô -------------------------------------------------------------------

class Picoh:
    """Comandos de alto nível; só escreve na serial quando algo muda."""

    def __init__(self, link):
        self.link = link
        self._attached: set[int] = set()
        self._motor: dict[int, int] = {}
        self._colour: tuple[int, int, int] | None = None
        self._bright: int | None = None
        self._shape: str | None = None
        self._eyes: tuple[int, int] | None = None
        self._lids: int | None = None

    def _send(self, msg: str) -> None:
        self.link.write(msg)

    def attach(self, m: int) -> None:
        if m not in self._attached:
            self._send(f"a0{m}\n")
            self._attached.add(m)

    def detach(self, m: int) -> None:
        self._send(f"d0{m}\n")
        self._attached.discard(m)
        self._motor.pop(m, None)

    def move(self, m: int, pos: float, speed: float = 5) -> None:
        lo, hi, reverse = MOTORS[m]
        pos = max(0.0, min(10.0, float(pos)))
        if m == BOTTOMLIP and pos < 5:     # abaixo do centro o lábio bate no de cima
            pos = 5 - (5 - pos) / 2
        if reverse:
            pos = 10 - pos
        degrees = int(lo + (hi - lo) / 10 * pos)
        if self._motor.get(m) == degrees:
            return
        self.attach(m)
        spd = int(max(0.0, min(10.0, float(speed))) * 25)
        self._send(f"m0{m},{degrees},{spd}\n")
        self._motor[m] = degrees

    def colour(self, r: int, g: int, b: int) -> None:
        rgb = tuple(max(0, min(255, int(v))) for v in (r, g, b))
        if rgb == self._colour:
            return
        self._send(f"l00,{rgb[0]},{rgb[1]},{rgb[2]}\n")
        self._send(f"l01,{rgb[0]},{rgb[1]},{rgb[2]}\n")
        self._colour = rgb

    def eye_brightness(self, level: float) -> None:
        level = max(0.0, min(10.0, float(level))) / 10
        val = int(round(level * level * 255))
        if val == self._bright:
            return
        self._send(f"FI,{val}\n")
        self._bright = val

    def eye_shape(self, name: str) -> None:
        name = name.lower()
        if name == self._shape or name not in EYE_SHAPES:
            return
        hex_str, auto_mirror = EYE_SHAPES[name]
        for set_no, cmd_set in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 8)):
            self._send(f"FB,{cmd_set},{_eye_bytes(hex_str, set_no, auto_mirror)}\n")
        self._shape = name
        self._eyes = None   # a lib oficial reposiciona as pupilas após trocar o formato

    def eyes(self, x: float, y: float) -> None:
        """Pupilas: 0-10 em cada eixo (5,5 = centro). Escala reduzida como na lib oficial."""
        def scaled(v: float) -> int:
            v = (max(0.0, min(10.0, float(v))) - 5) * 0.4 + 5
            return int(round(v * 255 / 10))
        pos = (scaled(x), scaled(y))
        if pos == self._eyes:
            return
        self._send(f"FE,0,{pos[0]},{pos[1]}\n")
        self._eyes = pos

    def lids(self, closed: float) -> None:
        """0 = olhos abertos … 1 = fechados."""
        val = int(round(max(0.0, min(1.0, float(closed))) * 255))
        if val == self._lids:
            return
        self._send(f"FL,+0,{val}\n")
        self._lids = val

    def reset(self) -> None:
        self.colour(0, 0, 0)
        self.eye_shape("eyeball")
        self.eye_brightness(IDLE["bright"])
        self.eyes(5, 5)
        self.lids(0)
        for m in (BOTTOMLIP, TOPLIP, HEADTURN, HEADNOD):
            self.move(m, REST[m], 3)
            time.sleep(0.15)
        self.close()

    def close(self) -> None:
        for m in sorted(self._attached):
            self.detach(m)


def _reverse_bits(byte_hex: str) -> str:
    x = int(byte_hex, 16)
    r = 0
    for bit in range(8):
        if x & (1 << bit):
            r |= 1 << (7 - bit)
    return f"{r:02X}"


def _eye_bytes(hex_str: str, set_no: int, auto_mirror: bool) -> str:
    """Um set do formato (9 linhas × 2 olhos), como _EyeShapeBytes da lib oficial."""
    parts: list[str] = []
    for row in range(9):
        offset = set_no * 18 + row * 2
        byte = hex_str[offset: offset + 2]
        left = byte if auto_mirror else _reverse_bits(byte)
        parts.append(left + _reverse_bits(byte))
    return ",".join(parts)


# --- coreografia --------------------------------------------------------------

class Face:
    """Traduz (fase, envelope, instante) em comandos. Determinístico dado `rng`."""

    def __init__(self, picoh: Picoh, rng: random.Random | None = None):
        self.picoh = picoh
        self.rng = rng or random.Random()
        self.phase = ""
        self.phase_since = 0.0
        self.next_blink = 0.0
        self.blink_until = 0.0
        self.wander_target = (5, 5)
        self.next_wander = 0.0

    def look_for(self, state: dict | None, now: float) -> Look:
        phase = (state or {}).get("phase") or "closed"
        if phase != self.phase:
            self.phase, self.phase_since = phase, now
        if phase in FLASH_PHASES and now - self.phase_since > FLASH_PHASES[phase]:
            return IDLE
        return LOOKS.get(phase, IDLE)

    def tick(self, state: dict | None, tts: dict | None, now: float) -> None:
        look = self.look_for(state, now)
        p = self.picoh

        # base: cor da fase, pulsando quando a fase pede
        r, g, b = look["colour"]
        pulse = look["pulse"]
        if pulse == "breathe":
            k = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 2 * math.pi / 2.4))
        elif pulse == "fast":
            k = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(now * 2 * math.pi / 0.8))
        elif pulse == "blink":
            k = 1.0 if int(now * 4) % 2 == 0 else 0.15
        else:
            k = 1.0
        # quantiza pra não inundar a serial com variações de 1 unidade
        p.colour(*(min(255, round(v * k / 8) * 8) for v in (r, g, b)))

        p.eye_shape(look["shape"])
        p.eye_brightness(look["bright"])

        # olhos: vagueiam pensando; senão fixos na direção da fase
        if look["wander"]:
            if now >= self.next_wander:
                self.wander_target = self.rng.choice(WANDER_TARGETS)
                self.next_wander = now + self.rng.uniform(0.9, 2.2)
            p.eyes(*self.wander_target)
        else:
            p.eyes(*look["eyes"])

        # piscada ocasional (não durante consent, que já pisca a base)
        if now >= self.next_blink:
            self.blink_until = now + 0.14
            self.next_blink = now + self.rng.uniform(3.0, 7.0)
        p.lids(1.0 if now < self.blink_until else 0.0)

        # boca segue o envelope da fala (qualquer fase: saudação, narração, resposta)
        lip = mouth_at(tts, now)
        p.move(BOTTOMLIP, lip, 10)
        # cabeça: pose da fase + leve aceno acompanhando a voz
        nod = look["nod"] + (lip - 5.0) * 0.25
        p.move(HEADNOD, nod, 4 if lip > 5 else 2)
        p.move(HEADTURN, look["turn"], 2)


# --- daemon -------------------------------------------------------------------

def _read_json(path: Path, cache: dict) -> dict | None:
    """Relê só quando o arquivo mudou (mtime/size); ausente = None."""
    try:
        st = path.stat()
    except OSError:
        cache.pop(path, None)
        return None
    key = (st.st_mtime_ns, st.st_size)
    entry = cache.get(path)
    if entry and entry[0] == key:
        return entry[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return entry[1] if entry else None
    cache[path] = (key, data)
    return data


def connect(port: str | None, fake: bool) -> tuple[Picoh | None, str]:
    if fake:
        return Picoh(FakeLink(echo=True)), "fake"
    if port:
        if not handshake(port):
            return None, ""
        return Picoh(SerialLink(port)), port
    found = find_port()
    if found is None:
        return None, ""
    return Picoh(SerialLink(found)), found


def run(port: str | None = None, fake: bool = False, parent: int | None = None) -> None:
    picoh: Picoh | None = None
    where = ""
    seen: list[str] = []
    next_scan = 0.0
    cache: dict = {}
    face: Face | None = None
    print(f"[picoh] daemon: estado={STATE_FILE} fala={TTS_FILE} porta={port or 'auto'}")
    try:
        while True:
            now = time.time()
            if parent is not None and os.getppid() != parent:
                print("[picoh] launcher saiu — encerrando")
                return
            if picoh is None and now >= next_scan:
                next_scan = now + RESCAN_SECONDS
                present = serial_candidates()
                # só sonda quando aparece porta nova: sondar reseta placas com auto-reset
                if fake or port or present != seen:
                    seen = present
                    picoh, where = connect(port, fake)
                    if picoh is not None:
                        print(f"[picoh] conectado em {where}")
                        face = Face(picoh)
                        picoh.reset()
                    elif present:
                        print(f"[picoh] nenhuma porta respondeu ao handshake: {', '.join(present)}")
            if picoh is None or face is None:
                time.sleep(0.5)
                continue
            state = _read_json(STATE_FILE, cache)
            tts = _read_json(TTS_FILE, cache)
            try:
                face.tick(state, tts, now)
            except Exception as e:  # serial caiu (cabo puxado): volta a procurar
                print(f"[picoh] erro falando com o robô ({e}) — reconectando")
                try:
                    picoh.link.close()
                except Exception:
                    pass
                picoh, face, seen = None, None, []
                continue
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        if picoh is not None:
            try:
                picoh.reset()
            except Exception:
                pass


def spawn_daemon(port: str = "") -> subprocess.Popen | None:
    """Usado pelo voice-launcher: o daemon no mesmo interpretador, saída no mesmo log."""
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--parent", str(os.getpid())]
    if port:
        cmd += ["--port", port]
    try:
        return subprocess.Popen(cmd)
    except OSError as e:
        print(f"[picoh] não consegui iniciar o daemon: {e}")
        return None


def demo(picoh: Picoh) -> None:
    """Percorre as fases com uma fala sintética no meio (sem áudio)."""
    face = Face(picoh, random.Random(1))
    fake_env = [0, 0, 4, 8, 10, 7, 3, 0, 5, 9, 6, 2, 0, 0, 7, 10, 8, 4, 0, 0] * 2
    script = [("listening", 2.5, False), ("recording", 2.0, False), ("transcribing", 1.0, False),
              ("thinking", 4.0, False), ("consent", 2.0, False), ("speaking", 2.0, True),
              ("followup", 2.0, False), ("closed", 1.5, False)]
    for phase, secs, talk in script:
        print(f"[demo] fase {phase}")
        t0 = time.time()
        tts = {"t0": t0, "step": ENVELOPE_STEP, "env": fake_env} if talk else None
        while time.time() - t0 < secs:
            face.tick({"phase": phase}, tts, time.time())
            time.sleep(TICK)
    picoh.reset()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Picoh como rosto do Jarvis")
    ap.add_argument("--port", default="", help="porta serial (vazio = procura pelo handshake)")
    ap.add_argument("--fake", action="store_true", help="sem robô: imprime os comandos")
    ap.add_argument("--probe", action="store_true", help="só procura o robô e sai")
    ap.add_argument("--demo", action="store_true", help="percorre as fases e sai")
    ap.add_argument("--reset", action="store_true", help="repouso: cor apagada, motores soltos")
    ap.add_argument("--parent", type=int, default=None, help="pid do launcher (sai junto com ele)")
    args = ap.parse_args(argv)
    fake = args.fake or os.environ.get("JARVIS_PICOH_FAKE") == "1"

    if args.probe:
        ports = serial_candidates()
        print("portas seriais:", ", ".join(ports) or "nenhuma")
        found = find_port([args.port] if args.port else None)
        print("picoh:", found or "não encontrado")
        return 0 if found else 1

    if args.demo or args.reset:
        picoh, where = connect(args.port or None, fake)
        if picoh is None:
            print("picoh não encontrado (use --fake pra testar sem o robô)")
            return 1
        print(f"picoh em {where}")
        if args.demo:
            demo(picoh)
        else:
            picoh.reset()
        picoh.link.close()
        return 0

    run(args.port or None, fake, args.parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
