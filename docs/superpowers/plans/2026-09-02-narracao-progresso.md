# Narração de progresso — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enquanto o Codex/Claude trabalha, o Jarvis fala a cada N segundos uma frase curta dizendo o que está sendo feito, com narrador local (Ollama), por API (OpenAI), auto-narração do modelo principal ou templates, e fallback em cadeia.

**Architecture:** Módulo novo `bin/jarvis_narrate.py` (estado da narração, backends, templates, detecção automática) alimentado pelos eventos que `ask_model` já parseia; o loop de espera de `run_conversation` consulta `narrator.poll()` e fala via `tts()` com corte quando a resposta chega. Configs no `jarvis_config.py`, textos no `jarvis_i18n.py`.

**Tech Stack:** Python 3.11 (conda `voice`), `urllib` (sem SDK), Ollama `/api/generate`, OpenAI Responses API, Piper via `tts()`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-02-narracao-progresso-design.md`

## Global Constraints

- Sem dependências novas; rede só por `urllib` (como `jarvis_stt.py` e `jarvis_dictate.py`).
- Nunca atrasar a resposta: geração em thread, timeout 3 s (local) / 4 s (openai); `tts` da narração é cortado por `stop_when`.
- Cadência: `narration_interval_quick` 8 s, `narration_interval_deep` 15 s; só com evento novo; "ainda pensando" uma vez por período seco (2× intervalo).
- Guardas da saída: uma linha, ≤ 140 caracteres, sem `<<`, sem markdown/aspas, não vazia, diferente da anterior.
- Idioma: tudo em pt-BR e en via `jarvis_i18n`.
- Testes: `~/miniconda3/envs/voice/bin/python -m unittest tests.test_narrate -v`.
- Commits sem atribuição de IA.

---

### Task 1: cliente Ollama público em `jarvis_dictate.py`

**Files:**
- Modify: `bin/jarvis_dictate.py:150-160` (`_ollama`)

**Interfaces:**
- Produces: `ollama_generate(prompt, system, model, timeout, keep_alive="60s", num_predict=None) -> str | None`; `ollama_models(timeout=0.8) -> list[str] | None` (None = Ollama fora do ar).

- [ ] **Step 1:** Renomear `_ollama` para `ollama_generate`, adicionar `keep_alive` e `num_predict` (entra em `options` só quando não None); manter `polish` chamando a nova função.
- [ ] **Step 2:** Adicionar `ollama_models()` fazendo GET em `http://127.0.0.1:11434/api/tags` e devolvendo `[m["name"] for m in models]`; qualquer erro → `None`.
- [ ] **Step 3:** `python -c "import sys; sys.path.insert(0,'bin'); import jarvis_dictate; print(jarvis_dictate.ollama_models())"` imprime a lista.
- [ ] **Step 4:** Commit `jarvis_dictate: cliente Ollama público (ollama_generate, ollama_models)`.

### Task 2: `jarvis_narrate.py` — templates, guardas, Narrator (com testes)

**Files:**
- Create: `bin/jarvis_narrate.py`
- Create: `tests/test_narrate.py`, `tests/__init__.py`

**Interfaces:**
- Produces:
  - `template_for(kind: str | None, text: str, lang: str) -> str` — frase fixa pela tabela da spec (`kind=None` → "ainda pensando").
  - `clean_output(text: str) -> str | None` — aplica as guardas; None = reprovado.
  - `class Narrator(mode, lang, interval, question, generate=None, clock=time.monotonic)`; `generate(question, events) -> str | None` é o backend (injetável); `feed(kind, text)`, `poll() -> str | None`, `spoke()`, `close()`.
  - `resolve_mode(cfg) -> tuple[str, str]` (modo efetivo, motivo); `build_generate(mode, cfg, lang)` devolve o backend ou None; `warm_up(cfg)`.

- [ ] **Step 1:** Escrever `tests/test_narrate.py` (unittest) cobrindo: templates nos dois idiomas; guardas (multi-linha, `<<`, markdown, vazio, >140); cadeia com `generate` falso que devolve None → usa `text` do modelo → sem `text` → template; `poll` com relógio injetado (não fala antes do intervalo; fala com evento novo; período seco fala uma vez; não repete frase); `resolve_mode` com funções de detecção injetadas.
- [ ] **Step 2:** Rodar: falha com `ModuleNotFoundError: jarvis_narrate`.
- [ ] **Step 3:** Implementar `bin/jarvis_narrate.py`:
  - Prompts `NARRATOR_PROMPT[lang]` (texto da spec).
  - `template_for`: classifica `tool` por regex sobre o texto (`\b(Read|cat|sed|head|tail)\b`, `\b(Grep|rg|grep|Glob|find)\b`, `\b(WebSearch|WebFetch|curl|wget)\b`), senão "rodando um comando"; `thinking` → "pensando"; `text` → primeira frase ≤ 140; None → período seco. Strings vêm de `jarvis_i18n.T` (chaves `narr_read`, `narr_search`, `narr_web`, `narr_command`, `narr_thinking`, `narr_dry`).
  - `clean_output`: strip, remove aspas/asteriscos/backticks, rejeita `\n`, `<<`, vazio, >140.
  - `Narrator`: lock; `_events` novos desde a última narração; `_last_spoken`, `_last_phrase`, `_dry_said`; `poll()`: se geração pendente → devolve resultado se pronto (ou None); senão se `now - last_spoken < interval` → None; senão se há eventos novos: snapshot, se `generate` → dispara thread com timeout (`threading.Thread` + `Event`, deadline) e devolve None; sem `generate` → `_fallback(snapshot)`; sem eventos e `now - last_spoken >= 2*interval` e não `_dry_said` → `_dry_said = True`, template dry. `_fallback`: último evento `text` (≤140) senão `template_for(último evento)`. Frase final passa por `clean_output` e comparação com `_last_phrase`.
  - Backends: `local_generate(model, lang)` → closure usando `jarvis_dictate.ollama_generate(..., keep_alive="5m", num_predict=40, timeout=3.0)`; `openai_generate(key, model, lang)` → POST `https://api.openai.com/v1/responses` com `{"model", "instructions", "input", "max_output_tokens": 60, "reasoning": {"effort": "low"}}`, timeout 4 s, lê `output[].type=="message"` → `content[].type=="output_text"` → `text`. Falhas 2× seguidas → o backend passa a devolver None sem tentar (flag `dead`) e loga `[narr]`.
  - `resolve_mode(cfg, ollama_models=jarvis_dictate.ollama_models, cuda=jarvis_stt.cuda_available)`: tabela da spec.
  - `warm_up(cfg)`: thread daemon com `ollama_generate("ok", "", model, timeout=20, keep_alive="5m", num_predict=1)`.
  - CLI: `status` (imprime modo/motivo) e `replay <jsonl> <provider> [modo]`.
- [ ] **Step 4:** Rodar os testes: PASS.
- [ ] **Step 5:** Commit `jarvis_narrate: narrador de progresso (templates, backends, Narrator)`.

### Task 3: settings e i18n

**Files:**
- Modify: `bin/jarvis_config.py` (após `dictation_polish_model`, e no bloco avançado)
- Modify: `bin/jarvis_i18n.py` (STRINGS pt-BR e en; `SELF_NARRATION_NOTES`)

- [ ] **Step 1:** `Setting("narration", "auto", ..., group="Narração", choices=["auto","local","openai","self","templates","off"])` na seção principal; avançado: `narration_interval_quick` (8, min 3, max 60, step 1), `narration_interval_deep` (15, idem), `narration_local_model` ("gemma3:4b"), `narration_openai_model` ("gpt-5.4-nano", choices `["gpt-5.4-nano","gpt-5.4-mini","gpt-5.6-luna"]`, `free_choices=True`).
- [ ] **Step 2:** i18n: `narr_read`, `narr_search`, `narr_web`, `narr_command`, `narr_thinking`, `narr_dry`, `ev_narr` ("narrando"/"narrating"); `SELF_NARRATION_NOTES = {lang: "\n\nPROGRESSO. Antes de cada comando ou tool call, escreva UMA linha curta ... em primeira pessoa ... (será falada em voz alta)."}`.
- [ ] **Step 3:** `python bin/jarvis-config.py show | grep narration` mostra os defaults; `jarvis config set narration self` e `get` funcionam.
- [ ] **Step 4:** Commit `Narração: settings e textos (pt-BR/en)`.

### Task 4: integração no `voice-launcher.py`

**Files:**
- Modify: `bin/voice-launcher.py` — constantes (após `HANDOFF_SECONDS_*`), `action_protocol`, `ask_model` (tail), `tts`, `run_conversation`.

- [ ] **Step 1:** Constantes: `NARRATION_MODE = CFG["narration"]`, intervalos, import `jarvis_narrate`.
- [ ] **Step 2:** `action_protocol`: se `NARRATION_MODE == "self"`, anexa `jarvis_i18n.SELF_NARRATION_NOTES[LANG]`.
- [ ] **Step 3:** `ask_model(..., on_event=None)`: a tail chama `on_event(kind, text)` e, se `on_status`, também o rótulo (mantém compatibilidade); `ask_with_broker` repassa `on_event`.
- [ ] **Step 4:** `tts(text, listener=None, stop_when=None)`: no loop de `paplay`, se `stop_when and stop_when()` → `proc.terminate()`, retorna False. Também no ramo sem listener (poll com 0.05 s).
- [ ] **Step 5:** `run_conversation`: no início (após saudação) `narr_mode, why = jarvis_narrate.resolve_mode(CFG)`; log; `warm_up` se local. Por pergunta: `narrator = jarvis_narrate.Narrator(narr_mode, LANG, interval, question, generate=jarvis_narrate.build_generate(narr_mode, CFG, LANG))`; callback `on_event` alimenta narrador + janela; após o ack (`chime` / "Pensando, senhor") `narrator.spoke()`; no loop, quando `pend is None`: `phrase = narrator.poll()`; se frase: log `[narr] {phrase}`, `window.update(thoughts=... + ["narrando: ..."])`, `interrupted = tts(phrase, listener, stop_when=fut.done)`, `narrator.spoke()`. `narrator.spoke()` também após a fala de consentimento. `narrator.close()` ao sair do loop.
- [ ] **Step 6:** `python -m py_compile bin/voice-launcher.py`; testes da Task 2 seguem passando.
- [ ] **Step 7:** Commit `Narração de progresso enquanto o modelo trabalha`.

### Task 5: README e verificação real

**Files:**
- Modify: `README.md` (Features, How a conversation works, Settings main/advanced, Troubleshooting)

- [ ] **Step 1:** Documentar `narration` e as chaves avançadas; parágrafo em "How a conversation works"; item de troubleshooting ("narração muda / em inglês").
- [ ] **Step 2:** `python bin/jarvis_narrate.py status` → modo `local (gemma3:4b)` nesta máquina.
- [ ] **Step 3:** Conversa real (`systemctl --user restart voice-launcher.service`, `journalctl --user -u voice-launcher.service -f`): pergunta que roda comandos em `local`; depois `jarvis config set narration templates` e `self`; conferir `[narr]` no log, latência da geração e que a resposta não atrasou. Barge-in durante uma narração.
- [ ] **Step 4:** Commit `README: narração de progresso`.
