#!/usr/bin/python3
"""Textos do Jarvis nos dois idiomas suportados (pt-BR, en).

Tudo que o assistente FALA ou mostra ao usuário vem daqui: saudação, avisos
curtos, dicas da janela, prompt de sistema e protocolo de ações, rótulos da
tela de configuração. O idioma é a chave `language` do config.

    from jarvis_i18n import T
    T(lang, "not_heard")                 -> "Não ouvi, senhor"
    T(lang, "project_not_found", name=x) -> formatado
"""

from __future__ import annotations

LANGUAGES = ["pt-BR", "en"]

# código do idioma pro reconhecimento de fala (whisper / OpenAI)
STT_LANG = {"pt-BR": "pt", "en": "en"}

# voz Piper padrão por idioma (arquivo em ~/.local/share/piper-voices)
DEFAULT_VOICE = {"pt-BR": "pt_BR-faber-medium", "en": "en_US-lessac-medium"}

STRINGS: dict[str, dict[str, str]] = {
    "pt-BR": {
        "greeting": "No que vamos trabalhar, senhor?",
        "not_heard": "Não ouvi, senhor",
        "not_understood": "Não entendi, senhor",
        "thinking": "Pensando, senhor",
        "yes_sir": "Pois não, senhor?",
        "done": "Feito, senhor.",
        "problem": "Tive um problema, senhor. Pode repetir?",
        "handoff": "Está demorando mais que o normal, senhor. Deixei rodando num terminal separado.",
        "handoff_window": "(trabalho longo — seguindo em terminal separado)",
        "handoff_history": "(resposta longa entregue a um terminal separado)",
        "interrupted": "(interrompida)",
        "action_done": "(ação executada)",
        "action": "(ação)",
        "cmd_label": "comando",
        "suspend": "suspender o computador",
        "opening_project": "abrindo o projeto {name}",
        "opening_app": "abrindo {name}",
        "project_not_found": "Não achei o projeto {name}.",
        "app_not_found": "Não encontrei o aplicativo {name}.",
        "test_answer": "[test] resposta fake",
        # janela da conversa
        "you": "você",
        "empty": "(a conversa aparece aqui)",
        "ph_listening": ("OUVINDO", "pode falar, senhor · peça pra encerrar quando quiser · q fecha"),
        "ph_recording": ("GRAVANDO", "fale à vontade — uma pausa encerra sua fala"),
        "ph_transcribing": ("TRANSCREVENDO", "um instante..."),
        "ph_thinking": ("PENSANDO", "fale por cima pra cancelar e pedir outra coisa · q fecha"),
        "ph_speaking": ("RESPONDENDO", "fale por cima que eu paro e te escuto · 'pausa' silencia"),
        "ph_followup": ("SUA VEZ", "fale pra continuar · 'fecha a conversa', 'é só isso', 'tchau' encerram · q fecha"),
        "ph_handoff": ("NO TERMINAL", "trabalho longo seguindo em terminal separado"),
        # terminal rascunho
        "handoff_title": "Jarvis — trabalho longo",
        "handoff_question": "pergunta:",
        "handoff_running": "executando… (o que o modelo faz aparece aqui)",
        "handoff_answer": "resposta:",
        "handoff_close": "concluído — qualquer tecla fecha",
        "ev_tool": "executando",
        "ev_thinking": "pensando",
        "ev_text": "dizendo",
        # tela de configuração
        "cfg_title": "Jarvis — configuração",
        "cfg_modified": "(modificado)",
        "cfg_main": "Principais",
        "cfg_advanced": "Avançado",
        "cfg_expand": "Enter expande/recolhe as opções avançadas.",
        "cfg_default": "default",
        "cfg_keys1": "q / Esc  sair      s  salvar e aplicar      d  default do item      D  tudo default",
        "cfg_keys2": "↑↓ navegar   ←→ / Enter alterar   p  salvar perfil   o  abrir perfil   e  editar arquivo",
        "cfg_on": "ligado",
        "cfg_off": "desligado",
        "cfg_secret_empty": "(vazio — usa OPENAI_API_KEY do ambiente)",
        "cfg_saved_restart": "salvo — Jarvis reiniciado com a nova configuração",
        "cfg_saved_off": "salvo (Jarvis desligado; vale quando ligar)",
        "cfg_saved": "salvo",
        "cfg_profile_name": "nome do perfil",
        "cfg_profile_saved": "perfil salvo em {path}",
        "cfg_no_profiles": "erro: nenhum perfil salvo (use p pra criar)",
        "cfg_profile_pick": "perfil ({names})",
        "cfg_profile_missing": "erro: perfil {name!r} não existe",
        "cfg_profile_loaded": "perfil {name!r} carregado — s pra aplicar",
        "cfg_all_defaults": "tudo nos defaults — s pra aplicar",
        "cfg_reloaded": "arquivo recarregado",
        "cfg_quit_prompt": "alterações não salvas — [s] salvar e sair  [n] sair sem salvar  [Esc] voltar",
        "cfg_error": "erro",
    },
    "en": {
        "greeting": "What shall we work on, sir?",
        "not_heard": "I didn't catch that, sir",
        "not_understood": "I didn't understand, sir",
        "thinking": "Thinking, sir",
        "yes_sir": "Yes, sir?",
        "done": "Done, sir.",
        "problem": "I ran into a problem, sir. Could you repeat?",
        "handoff": "This is taking longer than usual, sir. I left it running in a separate terminal.",
        "handoff_window": "(long task — continuing in a separate terminal)",
        "handoff_history": "(long answer handed to a separate terminal)",
        "interrupted": "(interrupted)",
        "action_done": "(action executed)",
        "action": "(action)",
        "cmd_label": "command",
        "suspend": "suspend the computer",
        "opening_project": "opening project {name}",
        "opening_app": "opening {name}",
        "project_not_found": "I couldn't find the project {name}.",
        "app_not_found": "I couldn't find the application {name}.",
        "test_answer": "[test] fake answer",
        "you": "you",
        "empty": "(the conversation shows up here)",
        "ph_listening": ("LISTENING", "go ahead, sir · ask to end whenever you like · q closes"),
        "ph_recording": ("RECORDING", "speak freely — a pause ends your turn"),
        "ph_transcribing": ("TRANSCRIBING", "one moment..."),
        "ph_thinking": ("THINKING", "talk over me to cancel and ask something else · q closes"),
        "ph_speaking": ("ANSWERING", "talk over me and I'll stop and listen · 'pause' mutes"),
        "ph_followup": ("YOUR TURN", "keep talking to continue · 'close the conversation', 'that's all', 'bye' end it · q closes"),
        "ph_handoff": ("IN TERMINAL", "long task continuing in a separate terminal"),
        "handoff_title": "Jarvis — long task",
        "handoff_question": "question:",
        "handoff_running": "running… (what the model does shows up here)",
        "handoff_answer": "answer:",
        "handoff_close": "done — press any key to close",
        "ev_tool": "running",
        "ev_thinking": "thinking",
        "ev_text": "saying",
        "cfg_title": "Jarvis — settings",
        "cfg_modified": "(modified)",
        "cfg_main": "Main",
        "cfg_advanced": "Advanced",
        "cfg_expand": "Enter expands/collapses the advanced options.",
        "cfg_default": "default",
        "cfg_keys1": "q / Esc  quit      s  save & apply      d  reset item      D  reset all",
        "cfg_keys2": "↑↓ navigate   ←→ / Enter change   p  save profile   o  open profile   e  edit file",
        "cfg_on": "on",
        "cfg_off": "off",
        "cfg_secret_empty": "(empty — uses OPENAI_API_KEY from the environment)",
        "cfg_saved_restart": "saved — Jarvis restarted with the new settings",
        "cfg_saved_off": "saved (Jarvis is off; applies when it starts)",
        "cfg_saved": "saved",
        "cfg_profile_name": "profile name",
        "cfg_profile_saved": "profile saved to {path}",
        "cfg_no_profiles": "error: no saved profiles (use p to create one)",
        "cfg_profile_pick": "profile ({names})",
        "cfg_profile_missing": "error: profile {name!r} does not exist",
        "cfg_profile_loaded": "profile {name!r} loaded — s to apply",
        "cfg_all_defaults": "everything at defaults — s to apply",
        "cfg_reloaded": "file reloaded",
        "cfg_quit_prompt": "unsaved changes — [s] save and quit  [n] quit without saving  [Esc] back",
        "cfg_error": "error",
    },
}


def norm_lang(lang: str | None) -> str:
    lang = (lang or "").strip()
    if lang.lower().startswith("pt"):
        return "pt-BR"
    return "en" if lang.lower().startswith("en") else "pt-BR"


def T(lang: str, key: str, **fmt):
    table = STRINGS.get(norm_lang(lang), STRINGS["pt-BR"])
    value = table.get(key, STRINGS["en"].get(key, key))
    if fmt and isinstance(value, str):
        return value.format(**fmt)
    return value


# --- prompt de sistema ----------------------------------------------------

SYSTEM_PROMPT = {
    "pt-BR": (
        "Você é o Jarvis, assistente pessoal por VOZ — isto é uma conversa falada "
        "em alto-falante, não um texto. Responda SEMPRE em português do Brasil, em "
        "prosa corrida natural de diálogo: sem markdown, sem listas, sem código, "
        "sem emojis, sem URLs. Vá direto ao ponto, tom de mordomo competente e "
        "discreto; no máximo 3 frases na maioria dos casos. "
        "Quando a resposta envolver muitos dados (previsão do tempo de vários dias, "
        "rankings, tabelas, listas longas), NUNCA enumere item por item na fala: "
        "diga apenas a tendência geral e os destaques. Se o detalhe completo for "
        "útil, escreva primeiro o resumo falado, depois uma linha contendo apenas "
        "--- e depois os detalhes, que aparecerão só na tela. "
        "Você está rodando no computador do usuário e tem acesso a ele (shell, "
        "Docker, arquivos, processos, rede): quando a pergunta for sobre a máquina, "
        "execute os comandos necessários e responda com o RESULTADO, nunca com o "
        "comando nem com a instrução pra ele rodar algo. "
        "O usuário pode emendar perguntas de follow-up; trate como conversa contínua. "
        "O texto que você recebe vem de reconhecimento de voz: tolere erros de "
        "transcrição e interprete a intenção, não a palavra exata. Você é quem "
        "decide o que o usuário quer — pergunta, pedido de ação na máquina, abrir "
        "algo, encerrar a conversa — não existe palavra-chave; as ações que o "
        "sistema executa por você estão descritas a seguir, com seus marcadores."
    ),
    "en": (
        "You are Jarvis, a personal VOICE assistant — this is a spoken conversation "
        "over a speaker, not a text. ALWAYS answer in English, in natural spoken "
        "prose: no markdown, no lists, no code, no emojis, no URLs. Get to the "
        "point, in the tone of a competent, discreet butler; at most 3 sentences "
        "in most cases. When the answer involves a lot of data (multi-day weather, "
        "rankings, tables, long lists), NEVER enumerate item by item out loud: give "
        "the overall trend and the highlights. If the full detail is useful, write "
        "the spoken summary first, then a line containing only --- and then the "
        "details, which will be shown on screen only. "
        "You are running on the user's computer and have access to it (shell, "
        "Docker, files, processes, network): when the question is about the "
        "machine, run the needed commands and answer with the RESULT, never with "
        "the command or instructions for the user to run something. "
        "The user may chain follow-up questions; treat it as one continuous "
        "conversation. The text you receive comes from speech recognition: "
        "tolerate transcription errors and interpret the intent, not the exact "
        "words. You decide what the user wants — a question, an action on the "
        "machine, opening something, ending the conversation — there are no "
        "keywords; the actions the system executes for you are described next, "
        "with their markers."
    ),
}

ACTIONS_PROTOCOL = {
    "pt-BR": (
        "\n\nAÇÕES. Você decide a intenção do usuário; algumas ações são executadas "
        "pelo sistema que te chama quando você inclui o marcador correspondente na "
        "resposta (cada marcador numa linha própria, no fim; a fala vem antes, curta):\n"
        "- <<ABRIR_PROJETO: nome>> abre o layout de desenvolvimento de um projeto. "
        "Projetos existentes: {projects}. Use o nome mais parecido com o que foi dito.\n"
        "- <<ABRIR_APP: nome>> abre um aplicativo instalado (btop, chrome, spotify, "
        "yazi...). Prefira isto a rodar o comando você mesmo.\n"
        "- <<DORMIR>> suspende o computador (só quando o pedido for claramente esse).\n"
        "- <<FIM>> encerra a conversa (despedida, 'é só isso', 'pode parar', etc.).\n"
        "Sem marcador, a conversa simplesmente continua."
    ),
    "en": (
        "\n\nACTIONS. You decide the user's intent; some actions are executed by the "
        "system calling you when you include the matching marker in your reply "
        "(each marker on its own line, at the end; the spoken part comes first, short):\n"
        "- <<ABRIR_PROJETO: name>> opens the development layout of a project. "
        "Existing projects: {projects}. Use the name closest to what was said.\n"
        "- <<ABRIR_APP: name>> opens an installed application (btop, chrome, spotify, "
        "yazi...). Prefer this over running the command yourself.\n"
        "- <<DORMIR>> suspends the computer (only when that is clearly the request).\n"
        "- <<FIM>> ends the conversation (goodbye, 'that's all', 'you can stop', etc.).\n"
        "Without a marker, the conversation simply continues."
    ),
}

ENVIRONMENT_NOTES = {
    "pt-BR": (
        "\n\nAMBIENTE. Você está no computador do usuário: Arch Linux / Omarchy 4 "
        "(Hyprland com config Lua), terminal ghostty, sessão uwsm. Para pedidos "
        "sobre a máquina ou o desktop que não têm marcador, execute você mesmo e "
        "confirme em uma frase. Receitas que funcionam aqui:\n"
        "- abrir um terminal: omarchy-launch-terminal (desacoplado, retorna na hora)\n"
        "- abrir qualquer app sem travar seu shell: systemd-run --user --collect "
        "--slice=app-graphical.slice -- <comando>\n"
        "- ir para o workspace N: hyprctl dispatch 'hl.dsp.focus({{ workspace = \"N\" }})'\n"
        "- executar algo pelo compositor: hyprctl dispatch 'hl.dsp.exec_cmd(\"<comando>\")'\n"
        "- listar janelas: hyprctl clients -j; workspace atual: hyprctl activeworkspace -j\n"
        "hyprctl dispatch só aceita essa sintaxe Lua (a antiga 'workspace 1' falha)."
    ),
    "en": (
        "\n\nENVIRONMENT. You are on the user's computer: Arch Linux / Omarchy 4 "
        "(Hyprland with Lua config), ghostty terminal, uwsm session. For requests "
        "about the machine or the desktop that have no marker, do it yourself and "
        "confirm in one sentence. Recipes that work here:\n"
        "- open a terminal: omarchy-launch-terminal (detached, returns immediately)\n"
        "- open any app without blocking your shell: systemd-run --user --collect "
        "--slice=app-graphical.slice -- <command>\n"
        "- go to workspace N: hyprctl dispatch 'hl.dsp.focus({{ workspace = \"N\" }})'\n"
        "- run something through the compositor: hyprctl dispatch 'hl.dsp.exec_cmd(\"<command>\")'\n"
        "- list windows: hyprctl clients -j; current workspace: hyprctl activeworkspace -j\n"
        "hyprctl dispatch only accepts this Lua syntax (the old 'workspace 1' fails)."
    ),
}


# --- rótulos da tela de configuração em inglês (os em pt-BR ficam no schema) --

SETTING_TEXT_EN: dict[str, tuple[str, str]] = {
    "language": ("Language", "Language Jarvis speaks and understands. Also picks the default voice and the speech-recognition language."),
    "stt_provider": ("Speech recognition", "local = Whisper on this machine (GPU if present, else CPU); openai = Realtime API with live text (needs the key); auto = GPU→local, no GPU→openai if a key exists."),
    "openai_api_key": ("OpenAI API key", "Used by the openai speech recognition. Empty = OPENAI_API_KEY environment variable."),
    "wake_word": ("Wake word", "openWakeWord model that wakes the assistant."),
    "wake_threshold": ("Wake word sensitivity", "Minimum score to trigger (lower = more sensitive, more false positives)."),
    "end_silence_seconds": ("Silence that ends your turn (s)", "Continuous pause that marks the end of what you said. Raise it if it cuts you off."),
    "followup_seconds": ("Follow-up window (s)", "Time listening without the wake word after each answer."),
    "quick_provider": ("Fast-question provider", "codex = OpenAI Codex CLI (ChatGPT login); claude = Claude Code CLI."),
    "system_access": ("Computer access", "Lets the model run commands on the machine (Docker, files, processes) without sandbox or approvals. Off = knowledge-only answers."),
    "codex_model": ("Codex model", "Empty uses the Codex CLI default. Only with the codex provider."),
    "codex_effort": ("Codex effort", "model_reasoning_effort for fast questions."),
    "codex_fast": ("Codex fast mode", "service_tier=fast: priority processing, faster answers (uses more of the plan). Off = default tier."),
    "claude_quick_model": ("Claude — fast model", "Used for fast questions when the provider is claude."),
    "claude_quick_effort": ("Claude — fast effort", "Reasoning effort for fast questions via claude."),
    "deep_model": ("\"Think hard\" — model", "Always via Claude Code CLI. fable = Claude Fable 5 (most capable)."),
    "deep_effort": ("\"Think hard\" — effort", "Reasoning effort for think-hard questions."),
    "voice": ("Voice (Piper)", "auto picks a voice for the language; .onnx files in ~/.local/share/piper-voices."),
    "voice_length_scale": ("Voice speed", ">1 slower and more formal; <1 faster."),
    "greeting": ("Greeting", "Sentence spoken when the wake word fires. Empty = default for the language."),
    "window_enabled": ("Conversation window", "Floating window with phase, countdown, exchanges and hints."),
    "whisper_model": ("Whisper model (local)", "auto = large-v3-turbo on GPU, small on CPU. Bigger = more accurate and slower."),
    "whisper_device": ("Whisper device", "auto detects CUDA; force cpu if the GPU is busy."),
    "openai_stt_model": ("OpenAI model (realtime)", "gpt-live-transcribe = streaming with deltas (recommended); gpt-realtime-whisper = alternative."),
    "vad_speech_threshold": ("VAD threshold", "Minimum silero probability to count as speech."),
    "first_speech_wait_seconds": ("Wait for first speech (s)", "How long to wait for you to start talking after the greeting."),
    "max_utterance_seconds": ("Maximum utterance (s)", "Hard cap per utterance."),
    "preroll_chunks": ("Pre-roll (80 ms chunks)", "Audio kept from before speech onset so the first syllable isn't cut."),
    "max_history_exchanges": ("Exchanges sent as context", "How many previous questions/answers go to the model."),
    "handoff_seconds_quick": ("Fast-question deadline (s)", "After this the work moves to a separate terminal (it is not killed)."),
    "handoff_seconds_deep": ("Think-hard deadline (s)", "Same, for think-hard questions."),
    "system_prompt": ("System prompt", "Style instructions sent to the model (edit in $EDITOR). Empty = default for the language."),
    "barge_min_rms": ("Barge-in — energy floor (RMS)", "Your speech must exceed this to interrupt. Typical room ≈ 0.005."),
    "tts_bleed_factor": ("Barge-in — factor over bleed", "Speech must exceed N× the TTS level picked up by the mic."),
    "barge_tts_warmup_frames": ("Barge-in — calibration frames", "160 ms frames measuring the bleed before allowing interruption."),
    "barge_hits_tts": ("Barge-in while speaking (N/M)", "Triggers with N of the last M speech frames while Jarvis talks."),
    "barge_hits_idle": ("Barge-in while thinking (N/M)", "Same, while Jarvis only thinks (no TTS)."),
    "interrupt_threshold_boost": ("Wake word during answer (+)", "Added to the sensitivity to reduce false positives from the TTS itself."),
    "barge_debug": ("Barge-in calibration log", "Writes rms/gate/vad levels to the journal every second during the busy phase."),
    "dev_dir": ("Projects folder", "Where \"open <project>\" looks."),
    "layout_script": ("Layout script", "Run as <script> <project>."),
}
