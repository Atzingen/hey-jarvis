# Narração de progresso enquanto o modelo trabalha

Data: 2026-09-02. Estado: aguardando revisão.

## Problema

Depois do chime de reconhecimento, o usuário fica em silêncio até a resposta
(até 45 s na pergunta rápida, 180 s no "pense bem"). A janela flutuante mostra
os "pensamentos" (eventos `tool`/`thinking`/`text` do `stream-json` do Claude e
do `--json` do Codex), mas quem conversa por voz não está olhando a tela e tem
a impressão de que o Jarvis travou.

## Objetivo

A cada N segundos de silêncio, enquanto o modelo principal roda, o Jarvis fala
**uma frase curta** dizendo o que está sendo feito ("estou listando os
containers do Docker"), no idioma do usuário, sem atrasar a resposta e sem
atrapalhar o barge-in. Funciona com GPU (modelo local), sem GPU (API OpenAI) e
sem nada disso (templates).

## Não-objetivos

- Narrar depois do handoff pro terminal rascunho (ele já mostra os eventos).
- Narrar durante o ditado ou durante um pedido de consentimento.
- Mostrar a narração na janela como troca da conversa ou guardar no histórico.
- Narração em streaming palavra a palavra.

## Modos (config `narration`)

| Valor | Quem gera a frase | Fallback |
|---|---|---|
| `auto` (padrão) | `local` se o Ollama responde e há GPU CUDA (mesma detecção do STT); senão `openai` se há chave; senão `templates` | conforme o modo resolvido |
| `local` | Ollama (`narration_local_model`, padrão `gemma3:4b`) | auto-narração → templates |
| `openai` | Responses API (`narration_openai_model`, padrão `gpt-5.4-nano`; chave `openai_api_key`, a mesma do STT, ou `OPENAI_API_KEY`) | auto-narração → templates |
| `self` | O modelo principal: o system prompt pede uma linha curta de progresso antes de cada ação; o launcher fala os eventos `text` | templates |
| `templates` | Frases fixas por tipo de evento, sem modelo | (nenhum) |
| `off` | Ninguém | |

A cadeia é sempre **narrador (local/openai) → auto-narração → templates**. A
auto-narração como fallback usa eventos `text` que o modelo principal emite
espontaneamente (o Claude costuma escrever uma linha entre tool calls); a
instrução explícita no system prompt só entra no modo `self`, porque muda o
comportamento (e o custo) do modelo principal.

Demais configs (grupo novo "Narração" no `jarvis config`):

- `narration_interval_quick` = 8 s, `narration_interval_deep` = 15 s.
- `narration_local_model` = `gemma3:4b`, `narration_openai_model` = `gpt-5.4-nano`.

## Arquitetura

```
ask_model (thread)                     run_conversation (thread principal)
  tail do jsonl ──► on_event(kind,text) ──► window.update(thoughts)
                                        └─► narrator.feed(kind, text)
                                                 │
   loop de espera (0,1 s): fut.done()? consentimento? barge-in?
                                                 │
                       narrator.poll(now) ──► frase | None
                                                 │
                       tts(frase, listener, stop_when=fut.done)
```

### Novo módulo `bin/jarvis_narrate.py`

```python
narrator = Narrator(mode, lang, interval, question, backends)   # um por pergunta
narrator.feed(kind, text)        # chamado pela tail dos eventos (thread do modelo)
narrator.poll(now) -> str | None # chamado pelo loop de espera; frase pronta pra falar
narrator.spoke(now)              # marca que algo foi dito (narração, "pensando senhor", consentimento)
narrator.close()                 # descarta geração pendente (resposta chegou / cancelado)
resolve_mode(cfg) -> str         # "auto" → local | openai | templates (+ motivo pro log)
warm_up(cfg)                     # pré-carrega o modelo local (keep_alive 5 min), em thread
```

Regras do `poll`:

1. Só fala se passaram `interval` segundos desde a última fala **e** há eventos
   novos desde a última narração.
2. Sem eventos novos por `2 × interval`: fala o template "ainda pensando nisso"
   uma única vez por período seco.
3. A geração pelo narrador roda em **thread própria** com timeout de 3 s
   (local) / 4 s (openai); `poll` devolve `None` enquanto gera e a frase quando
   pronta. Se estourar o timeout ou a saída reprovar nas guardas, desce na
   cadeia (auto-narração → templates) no mesmo ciclo.
4. A frase escolhida nunca repete a anterior (compara normalizado); se repetir,
   pula esse ciclo.

Guardas da saída do narrador (local e openai): uma linha, ≤ 140 caracteres,
sem `<<`, sem markdown/aspas, não vazia. Reprovou → fallback.

Entrada do narrador: a pergunta falada (contexto) + os eventos novos desde a
última narração, cada um truncado a 160 caracteres, no máximo 8. Prompt de
sistema (pt-BR; o `en` é a tradução):

> Você é o narrador de progresso de um assistente de voz, não o assistente.
> Receberá a pergunta do usuário e as últimas ações do assistente (comandos,
> raciocínio). Devolva UMA frase curta, de no máximo 12 palavras, em primeira
> pessoa, em português do Brasil, dizendo o que está sendo feito agora. Nunca
> responda à pergunta, nunca invente resultados, sem markdown, sem aspas.

Templates (idioma do usuário), escolhidos pelo evento mais recente:

| Evento | pt-BR | en |
|---|---|---|
| `tool` com Read/cat/sed/head/tail | lendo um arquivo | reading a file |
| `tool` com Grep/rg/grep/Glob/find | procurando no código | searching the code |
| `tool` com WebSearch/WebFetch/curl/wget | consultando a internet | checking the internet |
| `tool` (demais) | rodando um comando | running a command |
| `thinking` | pensando | thinking |
| `text` (auto-narração) | o próprio texto, 1ª frase, ≤ 140 caracteres | idem |
| nada novo (período seco) | ainda pensando nisso | still thinking about it |

### Backends do narrador

- **Ollama**: `POST /api/generate`, `stream=false`, `temperature 0.1`,
  `num_predict 40`, `keep_alive 5m`. Reaproveita o cliente do
  `jarvis_dictate.py` (o `_ollama` ganha parâmetros `keep_alive` e
  `num_predict` e vira `ollama_generate`, público; o polish continua usando).
  `warm_up` faz uma geração vazia no início da conversa (thread) pra carga do
  modelo não cair no primeiro ciclo.
- **OpenAI**: `POST https://api.openai.com/v1/responses` com `model`,
  `instructions` (prompt de sistema), `input` (pergunta + eventos),
  `max_output_tokens 60`, `reasoning: {"effort": <mínimo aceito pelo modelo,
  verificado na implementação>}`. Lê o texto do primeiro item `output` do tipo
  `message`, conteúdo `output_text`. `urllib`, sem SDK, como o STT.

### Mudanças no `voice-launcher.py`

- `ask_model`: a tail chama `on_event(kind, text)` com o evento cru (hoje já
  formata o rótulo com `status_label` e chama `on_status`); `run_conversation`
  passa a formatar o rótulo pra janela e alimentar o narrador no mesmo callback.
- `tts(text, listener, stop_when=None)`: novo parâmetro; quando `stop_when()`
  vira verdadeiro, mata o `paplay` e devolve `False` (não é interrupção do
  usuário). A resposta final é falada logo em seguida, sem esperar a narração.
- Loop de espera em `run_conversation`: além de `fut.done()`, consentimento e
  barge-in, chama `narrator.poll()`; se veio frase, `tts(frase, listener,
  stop_when=fut.done)` e `narrator.spoke()`. Com consentimento pendente
  (`pend is not None`) o narrador não fala. O "Pensando, senhor" do deep e a
  fala de consentimento chamam `narrator.spoke()` pra zerar o intervalo. Na
  saída do loop (resposta, handoff, cancelamento) chama `narrator.close()`.
- Modo `self`: `action_protocol` (ou o trecho do system prompt) ganha um
  parágrafo pedindo uma linha curta de progresso, no idioma do usuário, antes
  de cada ação. Verificar na implementação que o `codex exec --json` emite
  `agent_message` intermediários; se não emitir, o modo `self` no Codex cai em
  templates e isso fica documentado.
- Início da conversa (`run_conversation`, após a saudação): `resolve_mode` uma
  vez, log `[narr] modo=local (gemma3:4b)` e `warm_up` em thread quando local.

### Janela e i18n

- Janela: sem mudança de layout. A frase narrada entra na lista `thoughts`
  com o prefixo `narrando:` (chave i18n `ev_narr`), pra quem estiver olhando
  ver o que foi dito.
- i18n: templates, prefixo e o parágrafo de progresso do modo `self`, em
  pt-BR e en.

## Tratamento de erros

- Ollama fora do ar / modelo ausente / timeout: fallback silencioso na
  cadeia, log `[narr] local falhou (motivo) — fallback`. Em `auto`, se o
  local falhar 2 vezes seguidas na mesma conversa, o narrador passa a
  `templates` até o fim da conversa (não fica pagando timeout a cada ciclo).
- OpenAI sem chave: `resolve_mode` já desce pra templates com aviso no log.
  HTTP ≠ 200 ou timeout: mesmo tratamento do local.
- Barge-in durante a narração: `tts` devolve `True` como hoje; o fluxo de
  interrupção é o existente (cancela o modelo, escuta o usuário).
- Resposta chega durante a narração: `stop_when` corta o `paplay`; a
  resposta é falada em seguida.
- Modelo devolve algo que parece resposta (frase longa, "a resposta é…"):
  as guardas de tamanho e o prompt "nunca responda" seguram a maior parte; o
  que passar é curto e some na resposta real logo depois. Aceitável.

## Testes

`tests/test_narrate.py` (pytest, sem áudio nem rede):

- templates: cada linha da tabela, nos dois idiomas.
- guardas: saída multi-linha, com `<<`, com markdown, vazia, > 140 → reprovada.
- cadeia: backend falso que falha → usa `text` do modelo → sem `text` → template.
- `poll` com relógio injetado: não fala antes do intervalo; fala com evento
  novo; "ainda pensando" só uma vez no período seco; não repete a frase
  anterior.
- `resolve_mode`: matriz (ollama ok/ausente × GPU × chave) → modo esperado.
- parser do OpenAI: JSON de exemplo com item `reasoning` antes do `message`.

`python bin/jarvis_narrate.py replay <eventos.jsonl> <provider> [modo]`:
reproduz um arquivo de eventos gravado e imprime o que seria narrado e em que
segundo, sem TTS. Serve pra ajustar prompt e cadência com dados reais.

Verificação manual, antes de dar por pronto: uma conversa real em cada modo
(`local`, `openai`, `self`, `templates`) com uma pergunta que roda comandos,
conferindo no log `[narr]` a frase, a latência da geração e que a resposta não
foi atrasada; uma com barge-in durante a narração; uma com consentimento
pendente (`system_access = ask`).

## Arquivos

- novo `bin/jarvis_narrate.py`, novo `tests/test_narrate.py`
- `bin/voice-launcher.py` (ask_model, tts, run_conversation, action_protocol)
- `bin/jarvis_dictate.py` (`ollama_generate`)
- `bin/jarvis_config.py` (grupo Narração), `bin/jarvis_i18n.py`
- `README.md` (Settings, "How a conversation works", Troubleshooting)
