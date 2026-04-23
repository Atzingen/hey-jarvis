#!/usr/bin/env python
"""
voice-launcher: 'hey jarvis' + comando -> ação.

Comandos:
    "dormir"/"durma"              -> systemctl suspend
    "abrir <projeto>"             -> dev-layout <projeto>
    "pense bem <pergunta>"        -> claude opus/high   -> TTS
    <qualquer outra coisa>        -> claude sonnet/low  -> TTS
"""

import argparse
import difflib
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from openwakeword.model import Model as WakeModel

SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms @ 16kHz (openWakeWord default)
DEV_DIR = Path.home() / "Desktop/dev"
VOICE = Path.home() / ".local/share/piper-voices/pt_BR-faber-medium.onnx"
VOICE_LENGTH_SCALE = 1.15  # mais devagar = mais serio/butler
LAYOUT_SCRIPT = Path.home() / ".local/bin/dev-layout"
WAKE_THRESHOLD = 0.5
RECORD_SECONDS = 4.0  # folga extra porque o TTS come ~1s antes

# Claude CLI (pergunta livre e "pense bem")
CLAUDE_SYSTEM = (
    "Você está respondendo por voz através de um alto-falante. "
    "Responda SEMPRE em português do Brasil, em prosa corrida, "
    "sem markdown, sem listas, sem código, sem emojis, sem URLs. "
    "Seja direto e conciso: no máximo 3 frases quando possível."
)
CLAUDE_TIMEOUT_QUICK = 45   # sonnet/low
CLAUDE_TIMEOUT_DEEP = 180   # opus/high

# Perguntas rápidas (não-deep): qual CLI usar.
# "codex" = gpt-5.4/low, via ChatGPT login (~5-7s típico)
# "claude" = sonnet/low, via Claude CLI (~10s típico)
# "pense bem" sempre usa claude/opus/high, independente disso.
QUICK_PROVIDER = "codex"
CODEX_TIMEOUT_QUICK = 45

# Overlay flutuante centralizado (estilo Omarchy TUIs de sistema)
OVERLAY_ENABLED = True
OVERLAY_AUTOCLOSE_SECONDS = 20
OVERLAY_ANSWER_FILE = Path("/tmp/jarvis-answer.txt")
OVERLAY_QUESTION_FILE = Path("/tmp/jarvis-question.txt")

# Executor pra rodar ask_claude em paralelo com o TTS da pergunta entendida.
# Single worker: no máximo uma pergunta em voo por vez.
claude_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude")

# Interrupção por hotword: durante TTS/espera do Claude, se "hey jarvis"
# dispara de novo, cancelamos o que está rolando. Threshold elevado em relação
# ao wake normal pra reduzir falso-positivo do próprio TTS vazando pro mic.
INTERRUPT_THRESHOLD_BOOST = 0.2

# --- utils -----------------------------------------------------------

def list_projects() -> list[str]:
    return sorted(p.name for p in DEV_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def match_project(text: str) -> str | None:
    projs = list_projects()
    text_n = _norm(text)
    if not text_n:
        return None
    # 1) substring (ambos sentidos)
    for p in projs:
        pn = _norm(p)
        if pn and (pn in text_n or text_n in pn):
            return p
    # 2) fuzzy
    norms = [_norm(p) for p in projs]
    m = difflib.get_close_matches(text_n, norms, n=1, cutoff=0.55)
    if m:
        return projs[norms.index(m[0])]
    return None


def parse_command(text: str):
    """
    Decide o que fazer com o texto transcrito.

    Retorna (kind, payload):
        ("sleep",     None)
        ("open",      "<projeto>")
        ("open_fail", "<resto>")
        ("ask",       ("<pergunta>", deep: bool))
        ("noop",      None)
    """
    if not text or not text.strip():
        return ("noop", None)

    t_norm = _norm(text)
    t_low = text.lower().strip()

    # 1. dormir
    if "dormir" in t_norm or "durma" in t_norm:
        return ("sleep", None)

    # 2. abrir <projeto>  (prefixo explícito, variantes "abrir/abra/abre")
    m = re.search(r"\babr(?:ir|a|e)\b\s*(.*)", t_low)
    if m:
        resto = m.group(1).strip()
        proj = match_project(resto) if resto else None
        if proj:
            return ("open", proj)
        return ("open_fail", resto or text)

    # 3. pense bem <pergunta>  -> opus + high
    if re.search(r"\bpense[\s\-]?bem\b", t_low):
        q = re.sub(r"\bpense[\s\-]?bem\b[,\s]*", "", t_low, count=1).strip()
        return ("ask", (q or text, True))

    # 4. default: pergunta livre -> sonnet + low
    return ("ask", (text, False))


def show_overlay(question: str, answer: str, label: str) -> None:
    """Lança terminal flutuante (ghostty --class=TUI.float) com pergunta + resposta.

    A regra `tag +floating-window` do Omarchy captura a class TUI.float e
    aplica float/center/size 875x600 automaticamente.
    """
    if not OVERLAY_ENABLED:
        return
    try:
        OVERLAY_QUESTION_FILE.write_text(question.strip() or "(vazio)")
        OVERLAY_ANSWER_FILE.write_text(answer.strip() or "(sem resposta)")
    except OSError as e:
        print(f"   [overlay tmp falhou: {e}]")
        return

    header = f"Jarvis ({label})"

    shell_cmd = f"""
clear
echo
gum style --bold --foreground 212 --margin '1 2' '{header}'
echo
echo '  » pergunta:'
gum style --border normal --padding '0 1' --margin '0 2' --width 80 "$(cat {OVERLAY_QUESTION_FILE})"
echo
echo '  « resposta:'
gum style --border rounded --padding '1 2' --margin '0 2' --width 80 "$(cat {OVERLAY_ANSWER_FILE})"
echo
echo '  (auto-fecha em {OVERLAY_AUTOCLOSE_SECONDS}s — ou qualquer tecla)'
read -n 1 -s -t {OVERLAY_AUTOCLOSE_SECONDS} -r || true
"""
    try:
        subprocess.Popen(
            ["ghostty", "--class=TUI.float", "--title=Jarvis",
             "-e", "bash", "-c", shell_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("   [overlay: ghostty não encontrado]")


def ask_claude(question: str, deep: bool = False) -> str:
    """Chama claude CLI headless e retorna texto plano para TTS."""
    model = "opus" if deep else "sonnet"
    effort = "high" if deep else "low"
    timeout = CLAUDE_TIMEOUT_DEEP if deep else CLAUDE_TIMEOUT_QUICK
    try:
        result = subprocess.run(
            ["claude", "-p",
             "--model", model,
             "--effort", effort,
             "--tools", "",                      # sem tool use, resposta pura
             "--append-system-prompt", CLAUDE_SYSTEM,
             question],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "").strip()
        if not out:
            err = (result.stderr or "").strip()[:200]
            return f"Sem resposta. {err}" if err else "Sem resposta."
        return out
    except subprocess.TimeoutExpired:
        return "Demorei demais para responder, senhor. Tente de novo."
    except FileNotFoundError:
        return "Claude CLI não encontrado no ambiente."
    except Exception as e:
        return f"Erro ao consultar: {e}"


def ask_codex(question: str) -> str:
    """Chama codex CLI headless (gpt-5.4 + low reasoning) e retorna texto plano.

    Usa `-o <file>` em vez de stdout porque stdout traz eventos do agente
    (cabeçalho 'codex', contagem de tokens); o arquivo tem só a última mensagem.
    """
    prompt = f"{CLAUDE_SYSTEM}\n\nPergunta: {question}"
    out_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            out_path = f.name
        result = subprocess.run(
            ["codex", "exec",
             "--skip-git-repo-check",
             "--ephemeral",
             "-c", "model_reasoning_effort=low",
             "-o", out_path,
             prompt],
            capture_output=True, text=True, timeout=CODEX_TIMEOUT_QUICK,
        )
        out = Path(out_path).read_text().strip() if Path(out_path).exists() else ""
        if not out:
            err = (result.stderr or "").strip()[:200]
            return f"Sem resposta. {err}" if err else "Sem resposta."
        return out
    except subprocess.TimeoutExpired:
        return "Demorei demais para responder, senhor. Tente de novo."
    except FileNotFoundError:
        return "Codex CLI não encontrado no ambiente."
    except Exception as e:
        return f"Erro ao consultar codex: {e}"
    finally:
        if out_path:
            try:
                Path(out_path).unlink()
            except OSError:
                pass


def ask_fast(question: str) -> str:
    """Dispatcher para perguntas rápidas (não-deep). Escolhe provider via QUICK_PROVIDER."""
    if QUICK_PROVIDER == "codex":
        return ask_codex(question)
    return ask_claude(question, deep=False)


def chime(freq_start=880, freq_end=None, ms=120, vol=0.25) -> None:
    """Gera sine wave e toca (blocking)."""
    n = int(SAMPLE_RATE * ms / 1000)
    t = np.linspace(0, ms / 1000, n, False)
    if freq_end is None:
        freq = np.full(n, freq_start)
    else:
        freq = np.linspace(freq_start, freq_end, n)
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase) * vol
    fade = int(0.01 * SAMPLE_RATE)
    wave[:fade] *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    sd.play(wave.astype(np.float32), SAMPLE_RATE, blocking=True)


class InterruptListener:
    """Thread daemon que lê o mic e seta `fired` se a wake word dispara.

    Assume consumo exclusivo do stream enquanto ativa — o loop principal
    não deve ler o mesmo stream em paralelo.
    """

    def __init__(self, stream, wake, threshold: float):
        self.stream = stream
        self.wake = wake
        self.threshold = threshold
        self._stop = threading.Event()
        self.fired = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.fired = False
        self._stop.clear()
        self.wake.reset()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                data, _ = self.stream.read(CHUNK)
                pred = self.wake.predict(data.flatten())
                score = max(pred.values()) if pred else 0.0
                if score > self.threshold:
                    print(f"[int!]  interrupção detectada (score={score:.2f})")
                    self.fired = True
                    return
        except Exception as e:
            print(f"   [listener erro: {e}]")


def tts(text: str, listener: "InterruptListener | None" = None) -> bool:
    """Fala texto via piper -> paplay. Retorna True se interrompido pelo listener."""
    wav = "/tmp/voice-tts.wav"
    try:
        subprocess.run(
            ["piper", "-m", str(VOICE),
             "--length-scale", str(VOICE_LENGTH_SCALE),
             "-f", wav],
            input=text.encode(),
            check=True, capture_output=True, timeout=15,
        )
    except Exception as e:
        print(f"   [tts gen falhou: {e}]")
        return False

    try:
        proc = subprocess.Popen(["paplay", wav])
    except FileNotFoundError:
        return False

    if listener is None:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return False

    deadline = time.monotonic() + 60  # hard cap: TTS nunca deveria passar disso
    while proc.poll() is None:
        if listener.fired:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return True
        if time.monotonic() > deadline:
            print("   [tts: timeout absoluto, matando paplay]")
            proc.kill()
            return False
        time.sleep(0.05)
    return listener.fired


def record(stream: sd.InputStream, seconds: float) -> np.ndarray:
    """Le `seconds` de audio da stream int16, retorna float32 [-1,1]."""
    n_chunks = int(seconds * SAMPLE_RATE / CHUNK) + 1
    buf = []
    for _ in range(n_chunks):
        data, _ = stream.read(CHUNK)
        buf.append(data.flatten())
    arr = np.concatenate(buf).astype(np.float32) / 32768.0
    return arr


def transcribe(whisper: WhisperModel, audio: np.ndarray) -> str:
    segments, _ = whisper.transcribe(audio, language="pt", beam_size=1, vad_filter=True)
    return " ".join(s.text for s in segments).strip()


# --- main loop -------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="dry-run (nao abre janelas)")
    ap.add_argument("--whisper-model", default="small", help="tiny/base/small/medium")
    ap.add_argument("--wake-threshold", type=float, default=WAKE_THRESHOLD)
    args = ap.parse_args()

    print(">> carregando wake word (hey_jarvis)...")
    wake = WakeModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    print(f">> carregando whisper ({args.whisper_model} / int8 / cpu)...")
    whisper = WhisperModel(args.whisper_model, device="cpu", compute_type="int8")

    projs = list_projects()
    print(f">> projetos ({len(projs)}): {', '.join(projs)}")
    print(">> script de layout:", LAYOUT_SCRIPT, "(existe)" if LAYOUT_SCRIPT.exists() else "(AUSENTE!)")
    print(f">> modo: {'TEST (dry-run)' if args.test else 'REAL'}")
    print(">> pronto — diga 'hey jarvis' e espere o chime, depois fale o projeto\n")

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK,
    )
    stream.start()

    # chime inicial confirmando que tá no ar
    chime(660, 990, ms=80, vol=0.15)

    try:
        while True:
            try:
                data, _ = stream.read(CHUNK)
                chunk = data.flatten()
                pred = wake.predict(chunk)
                score = max(pred.values()) if pred else 0.0
            except Exception as e:
                print(f"[loop erro na leitura/wake: {e}] — sleep 1s e tenta de novo")
                time.sleep(1)
                continue

            if score > args.wake_threshold:
                try:
                    print(f"[wake] hey_jarvis detectado (score={score:.2f})")
                    tts("No que vamos trabalhar, senhor?")

                    # flush buffer residual (TTS vazou pro mic + audio anterior)
                    for _ in range(5):
                        stream.read(CHUNK)

                    print(f"[rec]  gravando {RECORD_SECONDS}s...")
                    audio = record(stream, RECORD_SECONDS)

                    print("[stt]  transcrevendo...")
                    t0 = time.time()
                    text = transcribe(whisper, audio)
                    print(f"[stt]  '{text}' ({time.time()-t0:.1f}s)")

                    kind, payload = parse_command(text)
                    print(f"[cmd]  {kind} :: {payload!r}")

                    if kind == "sleep":
                        tts("Boa noite, senhor")
                        if args.test:
                            print("[test] NAO executou systemctl suspend")
                        else:
                            subprocess.run(["systemctl", "suspend"], check=False)

                    elif kind == "open":
                        project = payload
                        spoken = project.replace("-", " ").replace("_", " ")
                        tts(f"Entendido, abrindo {spoken}")
                        if args.test:
                            print(f"[test] NAO lancou dev-layout {project}")
                        else:
                            subprocess.Popen([str(LAYOUT_SCRIPT), project])

                    elif kind == "open_fail":
                        tts("Não encontrei esse projeto, senhor")

                    elif kind == "ask":
                        question, deep = payload
                        if deep:
                            label = "claude opus/high"
                        elif QUICK_PROVIDER == "codex":
                            label = "codex gpt-5.4/low"
                        else:
                            label = "claude sonnet/low"
                        print(f"[ask]  provider={label} q={question!r}")
                        t0 = time.time()

                        if args.test:
                            tts(f"{'Pensando sobre' if deep else 'Entendi'}: {question}")
                            tts("[test] resposta fake")
                        else:
                            if deep:
                                fut = claude_executor.submit(ask_claude, question, True)
                            else:
                                fut = claude_executor.submit(ask_fast, question)
                            prefix = "Pensando sobre" if deep else "Entendi"

                            # fase busy: listener assume leitura do mic
                            listener = InterruptListener(
                                stream, wake,
                                args.wake_threshold + INTERRUPT_THRESHOLD_BOOST,
                            )
                            listener.start()

                            interrupted = tts(f"{prefix}: {question}", listener)

                            # espera o claude (se não foi interrompido ainda)
                            deadline = t0 + CLAUDE_TIMEOUT_DEEP + 15
                            while not interrupted and not fut.done():
                                if listener.fired or time.time() > deadline:
                                    interrupted = True
                                    break
                                time.sleep(0.1)

                            if interrupted:
                                listener.stop()
                                print("[int]  interrompido pelo usuário")
                                # confirmação curta (sem listener: não interromper a interrupção)
                                tts("Ok, senhor")
                            else:
                                try:
                                    resposta = fut.result(timeout=2)
                                except Exception as e:
                                    resposta = f"Erro inesperado: {e}"
                                elapsed = time.time() - t0
                                print(f"[ans]  {elapsed:.1f}s [{label}] :: {resposta[:200]}")
                                show_overlay(question, resposta, label)
                                if tts(resposta, listener):
                                    print("[int]  resposta interrompida")
                                listener.stop()

                    else:
                        tts("Não entendi, senhor")

                except Exception as e:
                    print(f"[wake handler erro: {type(e).__name__}: {e}]")
                    try:
                        tts("Tive um problema, senhor. Pode repetir?")
                    except Exception:
                        pass

                # reset wake word state + cooldown (sempre roda, mesmo após erro)
                try:
                    wake.reset()
                    time.sleep(0.3)
                    for _ in range(5):
                        stream.read(CHUNK)
                except Exception:
                    pass
                print("-> aguardando novo wake word...\n")

    except KeyboardInterrupt:
        print("\n>> bye")
    finally:
        claude_executor.shutdown(wait=False, cancel_futures=True)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
