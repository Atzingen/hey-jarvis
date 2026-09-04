#!/usr/bin/python3
"""Configuração do Jarvis: schema, defaults, load/save e perfis.

Arquivo do usuário: ~/.config/jarvis/config.toml (só as chaves alteradas).
Perfis salvos:      ~/.config/jarvis/profiles/<nome>.toml
Defaults:           SETTINGS abaixo (o comportamento "de fábrica").

Uso como CLI (chamado por `jarvis config ...`):
    jarvis_config.py show            # todas as chaves, valor atual e default
    jarvis_config.py get <chave>
    jarvis_config.py set <chave> <valor>
    jarvis_config.py reset [<chave>] # volta ao default (tudo, ou só a chave)
    jarvis_config.py path            # caminho do config.toml
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_i18n  # noqa: E402

CONFIG_DIR = Path.home() / ".config/jarvis"
CONFIG_FILE = CONFIG_DIR / "config.toml"
PROFILES_DIR = CONFIG_DIR / "profiles"
VOICES_DIR = Path.home() / ".local/share/piper-voices"

WAKE_WORDS = ["hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy"]
CLAUDE_MODELS = ["fable", "opus", "sonnet", "haiku"]
EFFORTS = ["low", "medium", "high"]
SYSTEM_ACCESS_MODES = ["ask", "full", "off"]
# system_access era booleano até a 2.1. `true` (acesso total, --dangerously-*)
# vira `ask`: o modo sem confirmação exige opt-in explícito nesta versão.
LEGACY_SYSTEM_ACCESS = {True: "ask", False: "off", "true": "ask", "false": "off"}

def available_voices() -> list[str]:
    return ["auto"] + sorted(p.stem for p in VOICES_DIR.glob("*.onnx"))


@dataclass
class Setting:
    key: str
    default: object
    label: str
    help: str
    section: str = "main"            # "main" | "advanced"
    group: str = ""                  # subtítulo dentro da seção
    choices: list | None = None      # enum -> cicla com Enter/setas
    min: float | None = None
    max: float | None = None
    step: float | None = None        # incremento das setas em números
    multiline: bool = False          # texto longo: edita no $EDITOR
    secret: bool = False             # mascarado no show/UI; arquivo fica 0600
    free_choices: bool = False       # choices só sugerem (UI cicla), qualquer valor é aceito

    @property
    def type(self) -> type:
        return type(self.default)


def mask(value: str) -> str:
    value = str(value)
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "•" * len(value)


SETTINGS: list[Setting] = [
    # --- principais -------------------------------------------------
    Setting("language", "en", "Idioma",
            "Idioma em que o Jarvis fala e entende. Define também a voz padrão e o idioma do reconhecimento.",
            group="Geral", choices=jarvis_i18n.LANGUAGES),
    Setting("wake_word_enabled", True, "Escuta da palavra-chave",
            "on = fica escutando \"hey jarvis\"; off = mic fechado quando ocioso — só o "
            "push-to-talk (Ctrl+Shift+H) e o ditado (Ctrl+Shift+K/L) ativam. `jarvis wake` alterna na hora.",
            group="Escuta"),
    Setting("wake_word", "hey_jarvis", "Palavra-chave",
            "Modelo openWakeWord que acorda o assistente.",
            group="Escuta", choices=WAKE_WORDS),
    Setting("wake_threshold", 0.5, "Sensibilidade da palavra-chave",
            "Score mínimo pra disparar (menor = mais sensível, mais falso-positivo).",
            group="Escuta", min=0.2, max=0.95, step=0.05),
    Setting("stt_provider", "auto", "Reconhecimento de fala",
            "local = whisper na máquina (GPU se houver, senão CPU); openai = Realtime API "
            "com texto ao vivo (precisa da chave); auto = GPU→local, sem GPU→openai se houver chave.",
            group="Escuta", choices=["auto", "local", "openai"]),
    Setting("openai_api_key", "", "Chave da OpenAI (API)",
            "Usada pelo reconhecimento openai. Vazio = variável de ambiente OPENAI_API_KEY.",
            group="Escuta", secret=True),
    Setting("end_silence_seconds", 1.2, "Silêncio que encerra a fala (s)",
            "Pausa contínua que marca o fim do que você disse. Suba se ele te corta.",
            group="Escuta", min=0.5, max=4.0, step=0.1),
    Setting("followup_seconds", 20.0, "Janela de follow-up (s)",
            "Tempo ouvindo sem wake word depois de cada resposta.",
            group="Escuta", min=5.0, max=90.0, step=5.0),

    Setting("quick_provider", "codex", "Provedor das perguntas rápidas",
            "codex = OpenAI Codex CLI (login ChatGPT); claude = Claude Code CLI.",
            group="Modelos", choices=["codex", "claude"]),
    Setting("system_access", "ask", "Acesso ao computador",
            "ask = o modelo não tem shell nem acesso a arquivos: cada comando que ele quiser "
            "rodar (até ler um arquivo) aparece numa janela pra você autorizar; full = sem "
            "sandbox e sem confirmação (--dangerously-*), só se você confia no que fala perto "
            "do mic; off = só responde de conhecimento.",
            group="Modelos", choices=SYSTEM_ACCESS_MODES),
    Setting("codex_model", "", "Modelo do Codex",
            "Vazio usa o default do Codex CLI (ex.: gpt-5.4). Só vale com provedor codex.",
            group="Modelos"),
    Setting("codex_effort", "low", "Esforço do Codex",
            "model_reasoning_effort do Codex nas perguntas rápidas.",
            group="Modelos", choices=EFFORTS),
    Setting("codex_fast", True, "Codex em modo rápido (fast)",
            "service_tier=fast: processamento prioritário, respostas mais rápidas "
            "(consome mais do plano). Desligado usa o tier padrão.",
            group="Modelos"),
    Setting("claude_quick_model", "sonnet", "Claude — modelo rápido",
            "Usado nas perguntas rápidas quando o provedor é claude.",
            group="Modelos", choices=CLAUDE_MODELS),
    Setting("claude_quick_effort", "low", "Claude — esforço rápido",
            "Esforço de raciocínio nas perguntas rápidas via claude.",
            group="Modelos", choices=EFFORTS),
    Setting("deep_model", "fable", "\"Pense bem\" — modelo",
            "Sempre via Claude Code CLI. fable = Claude Fable 5 (mais capaz).",
            group="Modelos", choices=CLAUDE_MODELS),
    Setting("deep_effort", "high", "\"Pense bem\" — esforço",
            "Esforço de raciocínio do pense bem.",
            group="Modelos", choices=EFFORTS),

    Setting("voice", "auto", "Voz (Piper)",
            "auto escolhe a voz do idioma; arquivos .onnx em ~/.local/share/piper-voices.",
            group="Voz", choices=available_voices(), free_choices=True),
    Setting("voice_length_scale", 1.15, "Velocidade da voz",
            ">1 mais lento e formal; <1 mais rápido.",
            group="Voz", min=0.7, max=1.8, step=0.05),
    Setting("greeting", "", "Saudação",
            "Frase falada quando a palavra-chave dispara. Vazio = padrão do idioma.",
            group="Voz"),

    Setting("window_enabled", True, "Janela da conversa",
            "Janela flutuante com fase, countdown, trocas e dicas.",
            group="Interface"),

    Setting("dictation_window", True, "Janela do ditado",
            "Mostra a transcrição ao vivo e o medidor de áudio enquanto você dita.",
            group="Ditado"),
    Setting("dictation_output", "paste", "Saída do ditado",
            "paste = copia e cola na janela ativa (Ctrl+V; Ctrl+Shift+V em terminais); "
            "type = digita o texto; clipboard = só copia (fica no topo do histórico).",
            group="Ditado", choices=["paste", "type", "clipboard"]),
    Setting("dictation_duck", 0.5, "Abaixar a música ao ditar (fator)",
            "Multiplica o volume do som enquanto o ditado por toggle grava e restaura ao parar "
            "(0.5 = cai pela metade; 1 = não mexe). Só no atalho de toggle, não no push-to-talk.",
            group="Ditado", min=0.0, max=1.0, step=0.05),
    Setting("dictation_polish", False, "Revisar o ditado (Ollama)",
            "Passa o texto por um modelo local só pra pontuar e tirar hesitações. Precisa do Ollama.",
            group="Ditado"),
    Setting("dictation_polish_model", "gemma3:4b", "Modelo da revisão",
            "Modelo do Ollama usado na revisão do ditado.",
            group="Ditado"),

    Setting("narration", "auto", "Narrar o progresso enquanto pensa",
            "A cada N segundos o Jarvis fala o que o modelo está fazendo. auto = detecta "
            "(Ollama+GPU → local; senão chave OpenAI → openai; senão templates); local = Ollama; "
            "openai = API; self = o próprio modelo narra; templates = frases fixas; off = mudo.",
            group="Narração", choices=["auto", "local", "openai", "self", "templates", "off"]),

    # --- avançado ---------------------------------------------------
    Setting("whisper_model", "auto", "Modelo Whisper (local)",
            "auto = large-v3-turbo em GPU, small em CPU. Maior = mais preciso e mais lento.",
            section="advanced", group="Reconhecimento",
            choices=["auto", "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]),
    Setting("whisper_device", "auto", "Dispositivo do Whisper",
            "auto detecta CUDA; force cpu se a GPU estiver ocupada.",
            section="advanced", group="Reconhecimento", choices=["auto", "cuda", "cpu"]),
    Setting("openai_stt_model", "gpt-live-transcribe", "Modelo OpenAI (tempo real)",
            "gpt-live-transcribe = streaming com deltas (recomendado); gpt-realtime-whisper = alternativa.",
            section="advanced", group="Reconhecimento",
            choices=["gpt-live-transcribe", "gpt-realtime-whisper", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]),
    Setting("vad_speech_threshold", 0.5, "Limiar do VAD",
            "Probabilidade mínima do silero pra considerar fala.",
            section="advanced", group="Reconhecimento", min=0.2, max=0.9, step=0.05),
    Setting("first_speech_wait_seconds", 6.0, "Espera pela 1ª fala (s)",
            "Quanto tempo esperar você começar a falar após a saudação.",
            section="advanced", group="Reconhecimento", min=2.0, max=20.0, step=1.0),
    Setting("max_utterance_seconds", 45.0, "Duração máxima da fala (s)",
            "Teto duro por fala.",
            section="advanced", group="Reconhecimento", min=10.0, max=180.0, step=5.0),
    Setting("preroll_chunks", 6, "Pré-roll (chunks de 80 ms)",
            "Áudio guardado antes do início da fala pra não cortar a 1ª sílaba.",
            section="advanced", group="Reconhecimento", min=0, max=20, step=1),

    Setting("max_history_exchanges", 4, "Trocas enviadas como contexto",
            "Quantas perguntas/respostas anteriores vão junto pro modelo.",
            section="advanced", group="Conversa", min=0, max=12, step=1),
    Setting("handoff_seconds_quick", 45, "Prazo da pergunta rápida (s)",
            "Depois disso o trabalho vai pra um terminal separado (não é morto).",
            section="advanced", group="Conversa", min=10, max=600, step=5),
    Setting("handoff_seconds_deep", 180, "Prazo do pense bem (s)",
            "Idem, para o pense bem.",
            section="advanced", group="Conversa", min=30, max=1800, step=30),
    Setting("narration_interval_quick", 8, "Narração — intervalo rápida (s)",
            "Segundos de silêncio antes de narrar, na pergunta rápida.",
            section="advanced", group="Narração", min=3, max=60, step=1),
    Setting("narration_interval_deep", 15, "Narração — intervalo pense bem (s)",
            "Idem, para o pense bem.",
            section="advanced", group="Narração", min=3, max=60, step=1),
    Setting("narration_local_model", "gemma3:4b", "Narração — modelo local (Ollama)",
            "Modelo do Ollama que gera a frase de progresso.",
            section="advanced", group="Narração"),
    Setting("narration_openai_model", "gpt-5.4-nano", "Narração — modelo OpenAI",
            "Modelo da Responses API usado quando a narração roda pela OpenAI (chave openai_api_key).",
            section="advanced", group="Narração",
            choices=["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.6-luna"], free_choices=True),
    Setting("system_prompt", "", "Prompt de sistema",
            "Instruções de estilo enviadas ao modelo (edite no $EDITOR). Vazio = padrão do idioma.",
            section="advanced", group="Conversa", multiline=True),

    Setting("barge_min_rms", 0.012, "Barge-in — piso de energia (RMS)",
            "Sua fala precisa passar disso pra interromper. Ambiente típico ≈ 0.005.",
            section="advanced", group="Interrupção (barge-in)", min=0.002, max=0.1, step=0.002),
    Setting("tts_bleed_factor", 1.5, "Barge-in — fator sobre o vazamento",
            "Fala precisa superar N× o nível do TTS captado pelo mic.",
            section="advanced", group="Interrupção (barge-in)", min=1.0, max=4.0, step=0.1),
    Setting("barge_tts_warmup_frames", 4, "Barge-in — frames de calibração",
            "Frames de 160 ms medindo o vazamento antes de permitir interromper.",
            section="advanced", group="Interrupção (barge-in)", min=1, max=12, step=1),
    Setting("barge_hits_tts", "3/4", "Barge-in durante a fala (N/M)",
            "Dispara com N dos últimos M frames com fala, enquanto o Jarvis fala.",
            section="advanced", group="Interrupção (barge-in)",
            choices=["2/3", "3/4", "4/5", "4/6"]),
    Setting("barge_hits_idle", "2/3", "Barge-in pensando (N/M)",
            "Idem, enquanto o Jarvis só pensa (sem TTS).",
            section="advanced", group="Interrupção (barge-in)",
            choices=["1/2", "2/3", "3/4"]),
    Setting("interrupt_threshold_boost", 0.2, "Wake word durante resposta (+)",
            "Somado à sensibilidade pra reduzir falso-positivo do próprio TTS.",
            section="advanced", group="Interrupção (barge-in)", min=0.0, max=0.5, step=0.05),
    Setting("barge_debug", True, "Log de calibração do barge-in",
            "Escreve níveis rms/gate/vad no journal a cada segundo na fase busy.",
            section="advanced", group="Interrupção (barge-in)"),
    Setting("log_transcripts", False, "Transcrições no log",
            "Escreve o texto do que você falou, ditou e da resposta no journal (jarvis log). "
            "Desligado, o log traz só tamanhos e tempos.",
            section="advanced", group="Interrupção (barge-in)"),

    Setting("dictation_max_seconds", 600, "Duração máxima do ditado (s)",
            "Teto de gravação de um ditado sem a tecla de parar.",
            section="advanced", group="Sistema", min=30, max=3600, step=30),
    Setting("dev_dir", "~/Desktop/dev", "Pasta dos projetos",
            "Onde \"abrir <projeto>\" procura.",
            section="advanced", group="Sistema"),
    Setting("layout_script", "~/.local/bin/dev-layout", "Script de layout",
            "Executado como <script> <projeto>.",
            section="advanced", group="Sistema"),
]

BY_KEY = {s.key: s for s in SETTINGS}

GROUP_EN = {"Geral": "General", "Escuta": "Listening", "Modelos": "Models", "Voz": "Voice",
            "Interface": "Interface", "Reconhecimento": "Recognition", "Conversa": "Conversation",
            "Interrupção (barge-in)": "Interruption (barge-in)", "Sistema": "System", "Ditado": "Dictation"}


def text_for(setting: Setting, lang: str) -> tuple[str, str]:
    """(rótulo, ajuda) no idioma da configuração."""
    if jarvis_i18n.norm_lang(lang) == "en":
        return jarvis_i18n.SETTING_TEXT_EN.get(setting.key, (setting.label, setting.help))
    return setting.label, setting.help


def group_for(group: str, lang: str) -> str:
    return GROUP_EN.get(group, group) if jarvis_i18n.norm_lang(lang) == "en" else group


def defaults() -> dict:
    return {s.key: s.default for s in SETTINGS}


def _coerce(setting: Setting, value):
    """Converte valor vindo de TOML/CLI pro tipo do default; ValueError se inválido."""
    t = setting.type
    if setting.key == "system_access":
        key = value if isinstance(value, bool) else str(value).strip().lower()
        value = LEGACY_SYSTEM_ACCESS.get(key, value)
    if t is bool:
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v in ("1", "true", "yes", "on", "sim"):
            return True
        if v in ("0", "false", "no", "off", "nao", "não"):
            return False
        raise ValueError(f"{setting.key}: esperado true/false, veio {value!r}")
    if t is int:
        out = int(float(value))
    elif t is float:
        out = float(value)
    else:
        out = str(value)
    if setting.choices is not None and not setting.free_choices and out not in setting.choices:
        raise ValueError(f"{setting.key}: {out!r} não está em {setting.choices}")
    if setting.min is not None and out < setting.min:
        raise ValueError(f"{setting.key}: {out} < mínimo {setting.min}")
    if setting.max is not None and out > setting.max:
        raise ValueError(f"{setting.key}: {out} > máximo {setting.max}")
    return out


def _read_toml(path: Path) -> dict:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"[config] ignorando {path}: {e}", file=sys.stderr)
        return {}
    out = {}
    for key, value in data.items():
        setting = BY_KEY.get(key)
        if setting is None:
            print(f"[config] chave desconhecida ignorada: {key}", file=sys.stderr)
            continue
        try:
            out[key] = _coerce(setting, value)
        except ValueError as e:
            print(f"[config] valor inválido ignorado — {e}", file=sys.stderr)
    return out


def load(path: Path = CONFIG_FILE) -> dict:
    """Defaults sobrescritos pelo que estiver no arquivo do usuário."""
    cfg = defaults()
    cfg.update(_read_toml(path))
    return cfg


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    if "\n" in s or ('"' in s and "'" not in s):
        return "'''" + s.replace("'''", "''\\'") + "'''"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(cfg: dict, only_changed: bool = True) -> str:
    """Serializa com comentários (label + help); por default só chaves != default."""
    lines = ["# Jarvis — configuração do usuário. Chaves ausentes usam o default.",
             "# Edite pela UI (`jarvis config`) ou à mão; `jarvis config show` lista tudo.", ""]
    current_group = None
    for s in SETTINGS:
        value = cfg.get(s.key, s.default)
        if only_changed and value == s.default:
            continue
        header = f"{'Avançado — ' if s.section == 'advanced' else ''}{s.group}"
        if header != current_group:
            lines.append(f"# ── {header}")
            current_group = header
        lines.append(f"# {s.label}: {s.help}")
        if s.choices:
            lines.append(f"#   opções: {', '.join(map(str, s.choices))}")
        if not only_changed or value != s.default:
            lines.append(f"# default: {_toml_value(s.default)}")
        lines.append(f"{s.key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def save(cfg: dict, path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump_toml(cfg))
    os.chmod(tmp, 0o600)  # pode conter chave de API
    tmp.replace(path)


# --- perfis ------------------------------------------------------------

def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))


def save_profile(name: str, cfg: dict) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip()) or "perfil"
    PROFILES_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PROFILES_DIR, 0o700)
    path = PROFILES_DIR / f"{safe}.toml"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump_toml(cfg, only_changed=False))
    os.chmod(tmp, 0o600)  # o perfil leva a config inteira, chave de API inclusive
    tmp.replace(path)
    return path


def load_profile(name: str) -> dict:
    cfg = defaults()
    cfg.update(_read_toml(PROFILES_DIR / f"{name}.toml"))
    return cfg


# --- CLI ---------------------------------------------------------------

def _fmt(value, setting: Setting | None = None) -> str:
    if setting is not None and setting.secret:
        return repr(mask(value))
    if isinstance(value, str) and len(value) > 60:
        return repr(value[:57] + "...")
    return repr(value)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"
    cfg = load()

    if cmd == "path":
        print(CONFIG_FILE)
        return 0

    if cmd == "show":
        lang = cfg["language"]
        section = None
        for s in SETTINGS:
            if s.section != section:
                section = s.section
                print(f"\n[{jarvis_i18n.T(lang, 'cfg_main' if section == 'main' else 'cfg_advanced')}]")
            mark = "*" if cfg[s.key] != s.default else " "
            print(f" {mark} {s.key:<28} = {_fmt(cfg[s.key], s):<32} (default {_fmt(s.default)})")
        print(f"\n* = alterado.  arquivo: {CONFIG_FILE}")
        return 0

    if cmd == "get" and len(argv) == 3:
        key = argv[2]
        if key not in BY_KEY:
            print(f"chave desconhecida: {key}", file=sys.stderr)
            return 2
        print(mask(cfg[key]) if BY_KEY[key].secret else cfg[key])
        return 0

    if cmd == "set" and len(argv) == 4:
        key, raw = argv[2], argv[3]
        if key not in BY_KEY:
            print(f"chave desconhecida: {key}", file=sys.stderr)
            return 2
        try:
            cfg[key] = _coerce(BY_KEY[key], raw)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        save(cfg)
        print(f"{key} = {_fmt(cfg[key], BY_KEY[key])}")
        return 0

    if cmd == "reset":
        if len(argv) == 3:
            key = argv[2]
            if key not in BY_KEY:
                print(f"chave desconhecida: {key}", file=sys.stderr)
                return 2
            cfg[key] = BY_KEY[key].default
            save(cfg)
            print(f"{key} = {cfg[key]!r} (default)")
        else:
            save(defaults())
            print("configuração restaurada aos defaults")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
