#!/usr/bin/env python
"""Reconhecimento de fala do Jarvis: backends atrás de uma interface comum.

    stt = build_transcriber(cfg, terms=[...])   # resolve provider (auto/local/openai)
    session = stt.begin(on_partial=lambda txt: ...)
    session.feed(chunk_int16_16k)                # a cada chunk do mic
    text = session.finish()                      # transcript final (str)

Backends:
  LocalWhisper      faster-whisper; CUDA quando disponível (large-v3-turbo),
                    senão CPU (small). Sem parciais: transcreve no finish().
  OpenAIRealtime    Realtime API (intent=transcription): envia o áudio enquanto
                    você fala, recebe deltas (texto provisório) e o transcript
                    final no commit. Guarda o áudio e cai pro local se falhar.

O voice-launcher decide início/fim da fala (VAD + energia); os backends só
recebem os chunks e devolvem texto. Um único consumidor do mic continua.
"""

from __future__ import annotations

import base64
import ctypes
import glob
import json
import os
import site
import sys
import threading
import time
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
OPENAI_RATE = 24000
OPENAI_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

PartialCallback = Callable[[str], None]


def log(msg: str) -> None:
    print(f"[stt]  {msg}", flush=True)


# --- CUDA ---------------------------------------------------------------

def preload_cuda_libs() -> bool:
    """Carrega cuBLAS/cuDNN dos wheels `nvidia-*-cu12` (pip) pro ctranslate2 achar.

    Sem isso o faster-whisper em CUDA falha com "libcublas.so.12 not found"
    mesmo com a GPU presente. Retorna True se carregou alguma lib.
    """
    loaded = False
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        pats = (f"{sp}/nvidia/cublas/lib/libcublas.so.*",
                f"{sp}/nvidia/cublas/lib/libcublasLt.so.*",
                f"{sp}/nvidia/cudnn/lib/libcudnn.so.*")
        for pat in pats:
            for lib in glob.glob(pat):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                    loaded = True
                except OSError:
                    pass
    return loaded


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# --- backend local ------------------------------------------------------

class LocalWhisper:
    """faster-whisper. `device` auto/cuda/cpu; `model` auto escolhe pelo device."""

    name = "local"
    streaming = False

    def __init__(self, model: str = "auto", device: str = "auto", terms: list[str] | None = None,
                 language: str = "pt"):
        from faster_whisper import WhisperModel

        self.language = language

        if device in ("auto", "cuda"):
            preload_cuda_libs()
        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        if model == "auto":
            model = "large-v3-turbo" if device == "cuda" else "small"

        self.hotwords = ", ".join(terms) if terms else None
        try:
            compute = "float16" if device == "cuda" else "int8"
            self.model = WhisperModel(model, device=device, compute_type=compute)
        except Exception as e:
            if device == "cuda":
                log(f"CUDA falhou ({str(e)[:80]}); usando CPU")
                device, model = "cpu", ("small" if model.startswith("large") else model)
                self.model = WhisperModel(model, device="cpu", compute_type="int8")
            else:
                raise
        self.device = device
        self.model_name = model
        self.label = f"whisper {model}/{device}"

    def transcribe(self, audio_f32: np.ndarray) -> str:
        # condition_on_previous_text=False: o prompt de cada janela de 30 s fica só
        # com as hotwords (+4 tokens fixos). Com o contexto das janelas anteriores
        # ligado, o faster-whisper (1.2.1) deixa hotwords (223) + contexto (223) + 4
        # passar dos 448 do modelo e o ctranslate2 falha com "The maximum decoding
        # length must be > 0" — acontecia em ditados longos (> ~60 s de fala).
        # Sem o contexto também não há loops de repetição em áudio longo.
        try:
            return self._run(audio_f32, self.hotwords)
        except (ValueError, RuntimeError) as e:
            # prompt estourado aparece como ValueError ("maximum decoding length")
            # ou RuntimeError ("No position encodings … >= 448"). Última linha de
            # defesa: nunca perder a fala por causa do prompt.
            log(f"transcrição falhou ({str(e)[:70]}) — repetindo sem hotwords")
            return self._run(audio_f32, None)

    def _run(self, audio_f32: np.ndarray, hotwords: str | None) -> str:
        segments, _ = self.model.transcribe(
            audio_f32, language=self.language, beam_size=1, vad_filter=True, hotwords=hotwords,
            condition_on_previous_text=False,
        )
        return " ".join(s.text for s in segments).strip()

    def begin(self, on_partial: PartialCallback | None = None) -> "LocalSession":
        return LocalSession(self)


class LocalSession:
    def __init__(self, backend: LocalWhisper):
        self.backend = backend
        self.chunks: list[np.ndarray] = []

    def feed(self, chunk_i16: np.ndarray) -> None:
        self.chunks.append(chunk_i16)

    def audio(self) -> np.ndarray:
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.chunks).astype(np.float32) / 32768.0

    def finish(self) -> str:
        audio = self.audio()
        if len(audio) < SAMPLE_RATE * 0.3:
            return ""
        return self.backend.transcribe(audio)

    def close(self) -> None:
        pass


# --- backend OpenAI Realtime -------------------------------------------

def resample_16k_to_24k(chunk_i16: np.ndarray) -> np.ndarray:
    n_out = int(round(len(chunk_i16) * OPENAI_RATE / SAMPLE_RATE))
    x_old = np.linspace(0.0, 1.0, num=len(chunk_i16), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, chunk_i16.astype(np.float32)).astype(np.int16)


class OpenAIRealtime:
    """Sessão de transcrição da Realtime API por WebSocket (commit manual)."""

    name = "openai"
    streaming = True

    def __init__(self, api_key: str, model: str = "gpt-live-transcribe",
                 terms: list[str] | None = None, fallback: LocalWhisper | None = None,
                 url: str = OPENAI_URL, connect_timeout: float = 6.0, final_timeout: float = 8.0,
                 language: str = "pt"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY ausente")
        self.api_key = api_key
        self.model = model
        self.language = language
        self.terms = terms or []
        self.fallback = fallback
        self.url = url
        self.connect_timeout = connect_timeout
        self.final_timeout = final_timeout
        self.label = f"openai {model}" + (" (+fallback local)" if fallback else "")

    def begin(self, on_partial: PartialCallback | None = None) -> "OpenAISession":
        return OpenAISession(self, on_partial)


class OpenAISession:
    """Uma fala: conecta, envia chunks, commit, espera o transcript final.

    A conexão é aberta em thread própria assim que a sessão é criada (idealmente
    enquanto o Piper ainda fala a saudação) — o áudio fica em fila até ela
    ficar pronta. Todo chunk também é guardado em memória para o fallback local.
    """

    def __init__(self, backend: OpenAIRealtime, on_partial: PartialCallback | None):
        self.b = backend
        self.on_partial = on_partial
        self.chunks: list[np.ndarray] = []
        self.pending: list[np.ndarray] = []
        self.partial = ""
        self.final: str | None = None
        self.error: str | None = None
        self.ws = None
        self.ready = threading.Event()
        self.done = threading.Event()
        self.closed = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="openai-stt")
        self.thread.start()

    # -- thread de rede --------------------------------------------------

    def _run(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.error = "pacote websockets ausente (pip install websockets)"
            self.done.set()
            return
        headers = {"Authorization": f"Bearer {self.b.api_key}"}
        try:
            self.ws = connect(self.b.url, additional_headers=headers,
                              open_timeout=self.b.connect_timeout, max_size=None)
            transcription = {"model": self.b.model, "language": self.b.language}
            if self.b.terms:
                transcription["keywords"] = self.b.terms[:100]
            self.ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {"input": {
                        "format": {"type": "audio/pcm", "rate": OPENAI_RATE},
                        "transcription": transcription,
                        "turn_detection": None,
                        "noise_reduction": {"type": "near_field"},
                    }},
                },
            }))
        except Exception as e:
            self.error = f"conexão falhou: {type(e).__name__}: {str(e)[:120]}"
            self.done.set()
            return

        with self.lock:
            self.ready.set()
            backlog, self.pending = self.pending, []
        for chunk in backlog:
            self._send_audio(chunk)

        try:
            for raw in self.ws:
                ev = json.loads(raw)
                t = ev.get("type", "")
                if t == "conversation.item.input_audio_transcription.delta":
                    self.partial += ev.get("delta", "")
                    if self.on_partial:
                        self.on_partial(self.partial)
                elif t == "conversation.item.input_audio_transcription.completed":
                    self.final = (ev.get("transcript") or "").strip()
                    self.done.set()
                    return
                elif t == "error":
                    err = ev.get("error", {})
                    self.error = f"api: {err.get('code') or err.get('type')}: {err.get('message', '')[:160]}"
                    self.done.set()
                    return
                if self.closed:
                    return
        except Exception as e:
            if not self.closed:
                self.error = f"socket: {type(e).__name__}: {str(e)[:120]}"
            self.done.set()

    def _send_audio(self, chunk_i16: np.ndarray) -> None:
        try:
            pcm = resample_16k_to_24k(chunk_i16).tobytes()
            self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }))
        except Exception as e:
            self.error = f"envio falhou: {type(e).__name__}"
            self.done.set()

    # -- API da sessão -----------------------------------------------------

    def feed(self, chunk_i16: np.ndarray) -> None:
        self.chunks.append(chunk_i16)
        if self.error:
            return
        with self.lock:
            if not self.ready.is_set():
                self.pending.append(chunk_i16)
                return
        self._send_audio(chunk_i16)

    def audio(self) -> np.ndarray:
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.chunks).astype(np.float32) / 32768.0

    def finish(self) -> str:
        audio = self.audio()
        if len(audio) < SAMPLE_RATE * 0.3:
            self.close()
            return ""
        text: str | None = None
        if not self.error and self.ready.wait(timeout=self.b.connect_timeout):
            try:
                self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            except Exception as e:
                self.error = f"commit falhou: {type(e).__name__}"
            if not self.error and self.done.wait(timeout=self.b.final_timeout) and self.final is not None:
                text = self.final
            elif not self.error:
                self.error = "sem transcript final no prazo"
        self.close()
        if text is not None:
            return text
        log(f"openai indisponível ({self.error}) — fallback local")
        if self.b.fallback is None:
            return ""
        return self.b.fallback.transcribe(audio)

    def close(self) -> None:
        self.closed = True
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


# --- resolução do provider ---------------------------------------------

def resolve_provider(cfg: dict) -> tuple[str, str]:
    """(provider, motivo) a partir de stt_provider/chave/GPU."""
    want = cfg.get("stt_provider", "auto")
    key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    if want == "local":
        return "local", "configurado"
    if want == "openai":
        if key:
            return "openai", "configurado"
        return "local", "stt_provider=openai mas sem chave — usando local"
    # auto
    preload_cuda_libs()
    if cuda_available():
        return "local", "GPU disponível"
    if key:
        return "openai", "sem GPU e com chave OpenAI"
    return "local", "sem GPU e sem chave — whisper em CPU"


def build_transcriber(cfg: dict, terms: list[str] | None = None, language: str = "pt"):
    provider, why = resolve_provider(cfg)
    local = LocalWhisper(cfg.get("whisper_model", "auto"), cfg.get("whisper_device", "auto"), terms,
                         language=language)
    log(f"provider={provider} ({why}); local={local.label}")
    if provider == "openai":
        key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
        return OpenAIRealtime(key, cfg.get("openai_stt_model", "gpt-live-transcribe"),
                              terms=terms, fallback=local, language=language)
    return local


if __name__ == "__main__":
    # teste rápido: python jarvis_stt.py arquivo.wav [local|openai]
    import wave
    path = sys.argv[1]
    want = sys.argv[2] if len(sys.argv) > 2 else "auto"
    with wave.open(path) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if sr != SAMPLE_RATE:
        x = np.arange(len(a)) / sr
        a = np.interp(np.arange(0, x[-1], 1 / SAMPLE_RATE), x, a.astype(np.float64)).astype(np.int16)
    stt = build_transcriber({"stt_provider": want}, terms=["Jarvis"])
    sess = stt.begin(on_partial=lambda t: print(f"   … {t!r}"))
    t0 = time.time()
    for i in range(0, len(a), 1280):
        sess.feed(a[i:i + 1280])
        if getattr(stt, "streaming", False):
            time.sleep(0.08)
    print(f"final ({time.time()-t0:.2f}s): {sess.finish()!r}")
