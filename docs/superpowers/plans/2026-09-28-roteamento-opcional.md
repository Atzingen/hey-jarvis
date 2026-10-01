# Roteamento opcional e agente por assinatura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execução sequencial; não delegar sem instrução aplicável. Este documento é um plano, não autorização para implementar, instalar ou publicar agora.

**Goal:** Adicionar quatro modos de roteamento e continuidade em terminal, preservando o uso direto dos CLIs por assinatura como padrão e retirando o gatilho “pense bem”.

**Architecture:** Separar decisão, política de encaminhamento e sessão de execução. O caminho direto não depende dos novos roteadores; os backends opcionais convergem para o mesmo executor. Manter o executor atual como compatibilidade e só ativar sessões nativas depois de validar capacidades e permissões.

**Tech Stack:** Python e unittest existentes, CLIs Codex/Claude, HTTP da stdlib, QML e painel curses existentes. GLiNER somente em ambiente opcional separado, CPU.

**Spec:** [2026-09-28-roteamento-opcional-design.md](../specs/2026-09-28-roteamento-opcional-design.md).

## Global Constraints

- `routing_mode = "agent"` é o padrão de instalações novas, upgrades e configurações antigas sem essa chave.
- O modo `agent` não importa, inicializa ou consulta os novos backends API, Jev ou GLiNER.
- Não inferir autorização para respostas por API da existência de chaves de STT ou narração.
- Preservar provedor, modelo, esforço, tier, permissões, voz, reconhecimento, atalhos e perfis existentes.
- Remover o gatilho especial `pense bem` e suas instruções em português e inglês; preservar a frase como texto natural do pedido.
- Nenhum classificador altera `system_access` ou amplia as ferramentas permitidas.
- Uma falha anterior ao envio ao executor pode retornar ao agente direto; execução iniciada ou de estado incerto nunca é reenviada automaticamente.
- Manter o pedido original e seu contexto em todo encaminhamento; a saída do roteador não substitui a intenção do usuário.
- O classificador local inicial será somente GLiNER2.5-multi-Decide na CPU, sem instalação ou download automático.
- Sessões pertencem ao provedor, diretório, conversa e política de acesso; nunca usar `--last` para selecionar uma sessão do Jarvis.
- Não alterar TTS/Kokoro, o ícone, os atalhos do desktop ou configurações globais dos agentes neste trabalho.
- Não fazer commit, push, instalação ou reinício do serviço durante a elaboração deste plano.

## Review Focus

- Chave de API já usada pelo STT: upgrade deve continuar em assinatura, sem habilitar respostas pagas; testar na tarefa 1.
- Processo aceitou um pedido mas a conexão caiu: reconectar sem repetir a ação; testar nas tarefas 2 e 6.
- Mesmo projeto aberto em duas conversas, com uma entrada por voz e outra por teclado: IDs e turnos não se misturam; testar na tarefa 6.
- Frase negada ou ambígua, como “não abra, só explique” e “a segunda opção”: preservar intenção/contexto e esclarecer quando necessário; testar nas tarefas 2–5.
- CLI antigo, dependência opcional ausente ou worker frio: o caminho por assinatura permanece utilizável; testar nas tarefas 1, 5, 6 e 8.

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `bin/jarvis_config.py` | Defaults, validação, compatibilidade de config e perfis |
| `bin/jarvis_routing.py` (novo) | Tipos, catálogo de perfis, decisão e fallback determinísticos |
| `bin/jarvis_router_api.py` (novo) | Resposta inicial por API e encaminhamento estruturado |
| `bin/jarvis_router_jev.py` (novo) | Classificação Jev, sem executar ações |
| `bin/jarvis_router_local.py` (novo) | Cliente/worker GLiNER isolado na CPU |
| `bin/jarvis_sessions.py` (novo) | Identidade, persistência, exclusão de envios concorrentes e handoff |
| `bin/jarvis_agent_codex.py` (novo) | Adaptador de sessão nativa Codex |
| `bin/jarvis_agent_claude.py` (novo) | Adaptador de sessão nativa Claude e receptor de hooks |
| `bin/voice-launcher.py` | Integração na conversa, mantendo executor legado e funções de áudio |
| `bin/jarvis_events.py` | Normalização dos eventos novos com os limites atuais |
| `bin/jarvis`, `bin/jarvis-config.py`, `bin/jarvis-app.py`, `bin/jarvis_i18n.py` | Comandos, configuração e textos nos dois idiomas |
| `app/qs/StatusPoller.qml`, `PanelContent.qml`, `ConversationContent.qml` | Estado efetivo, opções e ação de abrir sessão |
| `install.sh`, `README.md`, `SECURITY.md` | Distribuição, documentação de uso e contrato de acesso atualizado |
| `requirements-router-local.txt`, `requirements-router-local.lock`, `scripts/install-router-local.sh` (novos) | Instalação opcional independente do ambiente de voz |

Não mover o subsistema de áudio nem refatorar genericamente o launcher. Os módulos
novos isolam a funcionalidade adicionada. Não incluir `t3.json` nos diffs desta
feature. Antes da implementação, criar worktree na convenção `../worktrees/`;
não copiar ou descartar alterações locais alheias.

## Task 1: Congelar compatibilidade e retirar a frase especial

**Files:** modificar `bin/jarvis_config.py`, `bin/voice-launcher.py`, `tests/test_actions.py`; criar `tests/test_routing_config.py`.

**Interfaces:** `load(path: Path) -> dict`, `save(cfg: dict, path: Path) -> None`, `load_profile(name: str) -> dict` preservam seus contratos. Nesta tarefa `parse_command` ainda pode devolver `(texto, False)` para não exigir a integração inteira de uma vez; nenhum caminho de produção passa `deep=True`.

- [ ] Registrar baseline de testes e valores não secretos. Usar o Python do ambiente que realmente tem dependências de voz, sem instalar nada no Python global:

```bash
/home/gustavo/miniconda3/envs/voice/bin/python -m unittest discover -s tests -v
```

- [ ] Escrever regressões em arquivos temporários, sem ler a chave real do usuário:

```python
def test_upgrade_keeps_subscription_mode(self) -> None:
    self.config_path.write_text(
        'quick_provider="codex"\ncodex_model="modelo-atual"\n'
        'codex_effort="medium"\nopenai_api_key="fake-stt-key"\n'
        'deep_model="modelo-legado"\n', encoding="utf-8")
    cfg = jarvis_config.load(self.config_path)
    self.assertEqual(cfg["routing_mode"], "agent")
    self.assertEqual(cfg["api_provider"], "none")
    self.assertEqual(cfg["codex_model"], "modelo-atual")
    self.assertEqual(cfg["codex_effort"], "medium")
    jarvis_config.save(cfg, self.config_path)
    self.assertEqual(jarvis_config.load(self.config_path)["deep_model"], "modelo-legado")

def test_pense_bem_is_regular_text(self) -> None:
    text = "Pense bem antes de responder: não abra o projeto."
    self.assertEqual(vl.parse_command(text), ("ask", (text, False)))
```

`self.config_path` é `Path` em `TemporaryDirectory` criado em `setUp`; limpar em
`tearDown`. Importar `jarvis_config` via o mesmo `BIN` usado nos testes existentes.

- [ ] Acrescentar casos com apenas Claude instalado, perfil salvo, quatro chaves legadas, `think hard` e comandos locais `fim`, `pare` e entrada vazia.
- [ ] Confirmar falha das novas asserções; adicionar as chaves/defaults exatos da spec, preservar chaves legadas em load/save/perfis e ocultá-las nos controles ativos. Remover o ramo regex que extrai `pense bem`; não apagar essas palavras da fala.
- [ ] Manter os testes existentes de `off/ask/full`, ações, tetos de saída e watchdog. Rodar os testes novos e a suíte original. Esta tarefa só termina com o executor atual funcionando sem roteadores.

## Task 2: Política de roteamento sem dependências opcionais

**Files:** criar `bin/jarvis_routing.py`, `tests/test_routing.py`.

**Interfaces:** definir contratos abaixo neste módulo; módulos seguintes os importam. Usar funções e dataclasses simples, sem framework de plugins.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

RouteKind = Literal["answer", "api", "agent", "clarify"]
Complexity = Literal["low", "medium", "high"]

@dataclass(frozen=True)
class RouteRequest:
    request_id: str
    conversation_id: str
    text: str
    context: tuple[tuple[str, str], ...]
    cwd: Path
    active_session_id: str | None = None

@dataclass(frozen=True)
class RouteDecision:
    kind: RouteKind
    profile: str = "agent_default"
    complexity: Complexity = "low"
    needs_computer: bool = False
    continuation: bool = False
    answer: str = ""
    reason: str = ""

@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    transport: Literal["agent", "api"]
    provider: str
    model: str
    effort: str
    system_access: str
    fast: bool = False

RouterCall = Callable[[RouteRequest], RouteDecision]
```

Produzir `build_profiles(cfg: dict) -> dict[str, ExecutionProfile]`,
`choose_route(request: RouteRequest, cfg: dict, backends: dict[str, RouterCall]) -> RouteDecision`
e `resolve_profile(decision: RouteDecision, cfg: dict) -> ExecutionProfile`.
Exceções: `RouterUnavailable`, `RouterCancelled`, ambas derivadas de `Exception`.
O backend converte falhas recuperáveis em `RouterUnavailable`; cancelamento é
propagado e nunca se transforma em nova execução.

- [ ] Testar o bypass antes de qualquer construção/importação de backend:

```python
def test_agent_mode_does_not_classify(self) -> None:
    forbidden = Mock(side_effect=AssertionError("roteador foi chamado"))
    cfg = jarvis_config.defaults()
    decision = choose_route(self.request, cfg, {
        "assistant": forbidden, "jev": forbidden, "local": forbidden})
    self.assertEqual(decision.kind, "agent")
    self.assertEqual(decision.profile, "agent_default")
    forbidden.assert_not_called()
```

`self.request` é `RouteRequest("r1", "c1", "Explique isto", (), Path("/tmp/projeto"))`.

- [ ] Testar backends ausentes, `RouterUnavailable`, `RouterCancelled`, decisão inválida, perfil não configurado e tentativa de elevar `system_access`. Testar que texto de classificação não vira comando nem substitui `request.text`.
- [ ] Implementar bypass `agent` primeiro; factories dos outros modos são importadas apenas na seleção explícita. Resolver `agent_default` a partir das seis chaves existentes; `agent_strong` só existe se configurado no mesmo provedor. `api_default/api_strong` só existem com `api_provider` explicitamente habilitado e modelo correspondente preenchido.
- [ ] Classificação `api` sem perfil API resolve para `agent_default`; uma intenção que precisa do computador nunca resolve para resposta textual sem ferramenta. Resultados incompletos retornam ao agente com motivo explícito.
- [ ] Rodar `python -m unittest tests.test_routing tests.test_routing_config -v` com o Python de testes. Não chamar rede ou CLI real nesta unidade.

## Task 3: Modelo inicial por API que responde ou encaminha

**Files:** criar `bin/jarvis_router_api.py`, `tests/test_router_api.py`.

**Interfaces:** `build_api_router(cfg: dict, cancel: threading.Event, on_event: Callable[[str, str], None]) -> RouterCall`. Recebe/produz os contratos da tarefa 2. Produzir também `execute_api(request: RouteRequest, profile: ExecutionProfile, cfg: dict, cancel: threading.Event, on_event: Callable[[str, str], None]) -> str` para a rota escolhida por Jev/local.

- [ ] Usar HTTP simulado para testar: resposta simples em uma chamada, `handoff` para agente, `handoff` para API forte configurada, modelo inexistente, resposta vazia, 401/429, timeout, cancelamento e texto que contém uma marca de ação do launcher.
- [ ] Definir a ferramenta de encaminhamento com o schema abaixo; validar também no cliente, sem confiar apenas no schema remoto:

```json
{
  "type": "function",
  "name": "handoff",
  "description": "Encaminha o pedido quando ele exige outro executor; não executa ações.",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "target": {"type": "string", "enum": ["agent", "api_strong"]},
      "needs_computer": {"type": "boolean"},
      "complexity": {"type": "string", "enum": ["low", "medium", "high"]}
    },
    "required": ["target", "needs_computer", "complexity"],
    "additionalProperties": false
  }
}
```

- [ ] Implementar pelo endpoint Responses documentado, seguindo o padrão `urllib` já usado em `jarvis_narrate.py`. Credencial e modelo ausentes não produzem requisição. Remover chave/headers de mensagens de erro. Aplicar timeout, cancelamento entre eventos, tetos de bytes e limite de uma escalada.
- [ ] Resposta textual da API não passa por `parse_actions` para executar marcadores: ação no computador exige `handoff` validado. Para informações atuais, disponibilizar busca web somente quando suportada pelo perfil; manter citações na tela. Sem busca disponível, encaminhar ao agente.
- [ ] Testar que a chamada de destino recebe o pedido original completo, contexto e projeto; não recebe somente o resumo do roteador. Testar uma única execução após timeout anterior ao envio e nenhuma repetição depois de encaminhamento aceito.
- [ ] Rodar `python -m unittest tests.test_router_api tests.test_routing -v`. O teste real posterior usa credencial/modelo explicitamente configurados, sem afirmar que mocks validam latência ou qualidade.

## Task 4: Jev opcional, desacoplado do executor

**Files:** criar `bin/jarvis_router_jev.py`, `tests/test_router_jev.py`.

**Interfaces:** `build_jev_router(cfg: dict, cancel: threading.Event) -> RouterCall`.

- [ ] Salvar fixtures de requisição/resposta sem dados pessoais, conforme o contrato público atual `state` + perguntas tipadas do Jev. Testar escolhas de ferramenta, complexidade e continuação, credencial ausente, 401/429/5xx, timeout e JSON malformado.
- [ ] Implementar `POST https://api.typesafe.ai/v1/systemone` pelo contrato oficial disponível na execução; usar perguntas de escolha para categorias fechadas. Não depender de um limiar arbitrário de autoconfiança para executar comandos. O adaptador retorna categorias, não código nem nomes arbitrários de modelo.
- [ ] Fixar a regressão principal com a mesma fixture em duas configurações:

```python
decision = RouteDecision(kind="api", reason="pergunta geral")
cfg = jarvis_config.defaults()
cfg["routing_mode"] = "jev"
self.assertEqual(resolve_profile(decision, cfg).transport, "agent")
cfg.update(api_provider="openai", api_model="modelo-configurado")
self.assertEqual(resolve_profile(decision, cfg).transport, "api")
```

- [ ] Confirmar que Jev habilitado não exige chave OpenAI nem outro agente instalado. Falha do classificador produz retorno ao agente selecionado antes de começar a tarefa.
- [ ] Rodar `python -m unittest tests.test_router_jev tests.test_routing -v`. Integração real Jev é teste separado, condicionado a acesso válido; sem esse acesso a opção permanece identificada como não verificada, sem bloquear o modo por assinatura.

## Task 5: Classificador pequeno na CPU, com instalação explícita

**Files:** criar `bin/jarvis_router_local.py`, `tests/test_router_local.py`, `scripts/install-router-local.sh`, `requirements-router-local.txt`, `requirements-router-local.lock`; modificar `bin/jarvis`.

**Interfaces:** `build_local_router(cfg: dict, cancel: threading.Event) -> RouterCall`. O próprio módulo pode executar em `--worker`, recebendo JSONL pelo stdin e emitindo JSONL pelo stdout. Formato: request `{id, text, context, categories}`; response `{id, kind, complexity, needs_computer, continuation}`. Nunca misturar logs com stdout de protocolo.

- [ ] Testar com processo falso: worker ausente, aquecimento, EOF, ID errado, resposta acima do teto, timeout, cancelamento e encerramento por inatividade. Verificar que `CUDA_VISIBLE_DEVICES` é vazio no worker e o dispositivo solicitado é `cpu`.
- [ ] Implementar classificador único `fastino/GLiNER2.5-multi-Decide`, usando labels/descriptions coerentes com o catálogo da tarefa 2. Fixar uma revisão imutável disponível na execução, baixar explicitamente e carregar apenas arquivos locais; não usar download implícito de `from_pretrained` durante um pedido de voz.
- [ ] Acrescentar comandos explícitos `jarvis router install-local` e `jarvis router status`. Instalador cria ambiente separado e lock com hashes, testa import/carregamento CPU e informa armazenamento usado. Não adicionar GLiNER ao `requirements.lock` de voz nem chamar o instalador opcional a partir de `install.sh`.
- [ ] Iniciar worker somente com modo local escolhido. Enquanto frio ou indisponível, usar agente direto; encerrar após `local_router_idle_seconds`. Verificar `torch.cuda.is_initialized()` no teste real do worker, não apenas o texto da configuração.
- [ ] Rodar `python -m unittest tests.test_router_local tests.test_routing -v`. Depois, medir separadamente acerto em português e p50/p95 quente/frio no hardware real, com voz ativa; não transformar números do fabricante em requisito de desempenho local.

## Task 6: Sessão persistente com terminal nativo e fallback compatível

**Files:** criar `bin/jarvis_sessions.py`, `bin/jarvis_agent_codex.py`, `bin/jarvis_agent_claude.py`, `tests/test_sessions.py`, `tests/test_agent_codex.py`, `tests/test_agent_claude.py`; modificar `bin/jarvis_events.py` e `bin/jarvis`.

**Interfaces:** `SessionRef` e as funções públicas abaixo ficam em `jarvis_sessions.py`:

```python
@dataclass(frozen=True)
class SessionRef:
    id: str
    conversation_id: str
    provider: str
    native_id: str
    cwd: Path
    system_access: str

def start_session(request: RouteRequest, profile: ExecutionProfile) -> SessionRef:
    """Cria a sessão e registra o primeiro envio sem duplicá-lo."""

def submit_turn(session: SessionRef, request: RouteRequest) -> str:
    """Envia uma vez; devolve ID do turno ou erro de estado incerto."""

def open_terminal(session: SessionRef) -> None:
    """Anexa ao ID exato, sem reenviar o pedido."""

def interrupt_turn(session: SessionRef, turn_id: str) -> None:
    """Interrompe o turno sem escolher outra conversa."""
```

Adicionar `SessionUnsupported` para rejeição **antes** de envio e
`SubmissionUnknown` para estado incerto **depois** de tentar enviar. A primeira
permite executor legado; a segunda exige reconciliação e nunca permite reenvio.
Os dois adaptadores expõem os mesmos contratos: `capabilities() -> dict[str, bool]`
com chaves `start`, `final_answer`, `submit`, `attach`, `interrupt`, `access_off`,
`access_ask`, `access_full`; `start(request: RouteRequest, profile: ExecutionProfile) -> SessionRef`;
`submit(session: SessionRef, request: RouteRequest) -> str`;
`attach_argv(session: SessionRef) -> list[str]`;
`interrupt(session: SessionRef, turn_id: str) -> None`;
`events(session: SessionRef, after_event_id: str | None) -> list[dict]`.
Cada evento contém `event_id`, `session_id`, `turn_id`, `kind` e `text` (strings).
O gestor deduplica por `event_id` e entrega `(kind, text)` aos consumidores de
narração existentes. O filtro de capacidades exige o modo de acesso da sessão,
além das cinco operações; não basta existir o subcomando `attach`.

- [ ] Antes de habilitar qualquer adaptador, provar em checkout descartável: autenticação por assinatura sem chave API, primeiro pedido, resposta final, continuação por voz, teclado durante execução, interrupção e políticas `off/ask/full`. Usar os CLIs instalados; consultar seus schemas/helps atuais. Não atualizar CLI nem daemon global como efeito colateral.
- [ ] Codex: usar app-server local, `initialize`, `thread/start`, `turn/start`, eventos e retomada pelo ID. TUI deve apontar para a mesma instância/sessão. Preservar limites e ferramentas do modo `ask/off`, inclusive broker; não supor que sandbox read-only sozinho equivale ao modo `off` atual.
- [ ] Claude: iniciar com ID conhecido, background nativo e `attach`; coletar eventos por hooks escopados à sessão. Validar retomada/entrada de voz sem criar cópia da sessão já rodando e sem criar worktree ou trocar de diretório implicitamente. Se o canal necessário não for suportado, usar uma sessão interativa em PTY/tmux controlada pelo Jarvis apenas após comprovar IDs, entrada literal e eventos; se ainda não houver todas as capacidades, marcar `SessionUnsupported` antes do primeiro envio. Uma dependência tmux ausente também seleciona compatibilidade, sem instalação automática.
- [ ] Configurar hooks somente para a invocação do Jarvis. Aplicar `--restricted`, ferramentas e broker equivalentes aos atuais. Nunca introduzir `--bare` no Claude: ele não preserva o caminho de login por assinatura em todas as configurações.
- [ ] Persistir registros de sessões em diretório privado do Jarvis, com escrita atômica e bloqueio por sessão. Não ler nem anexar conversas de outros projetos por proximidade temporal. Testar projeto/ID/política divergentes e reinício do launcher com tarefa em andamento.
- [ ] Escrever testes com adaptadores falsos verificando que anexar não chama `start/submit`, que reconexão não incrementa contagem de envios, que cada `request_id` é enviado uma vez e que dois projetos não compartilham `native_id`. Cobrir teclado e voz simultâneos, fechamento da janela, cancelamento e entrega tardia da resposta após handoff.
- [ ] Adicionar `jarvis session open <id>`; lançar Alacritty normal, sem `TUI.float`, passando argv sem interpolação de pedido em shell. Preservar o viewer legado apenas para o modo de compatibilidade, identificado corretamente.
- [ ] Rodar testes de sessões, adaptadores, eventos, ações e consentimento. Validar terminal real aberto, entrada funcionando e IDs iguais; help, screenshot estático ou processo vivo não bastam como prova de continuidade.

## Task 7: Integrar as opções na conversa e na configuração

**Files:** modificar `bin/voice-launcher.py`, `bin/jarvis-config.py`, `bin/jarvis-app.py`, `bin/jarvis_i18n.py`, `app/qs/StatusPoller.qml`, `app/qs/PanelContent.qml`, `app/qs/ConversationContent.qml`; criar `tests/test_conversation_routing.py`; ampliar `tests/test_i18n.py`.

**Interfaces:** conversa produz `RouteRequest`, recebe `RouteDecision`, resolve `ExecutionProfile` e escolhe gestor nativo ou executor legado. Áudio/STT/TTS mantêm suas interfaces. Acrescentar ao estado da janela `routing_mode`, `effective_route`, `fallback_reason`, `session_id`, `session_backend` e `can_attach`.

- [ ] Testar a conversa completa com STT/TTS falsos e agentes falsos: quatro modos, assinatura sem chaves, apenas um CLI instalado, erro do roteador, conclusão tardia, barge-in e continuação. Espionar fábricas de backends para provar que modo direto não as instancia.
- [ ] Substituir a escolha `deep` pela resolução de perfil no ponto de chamada, sem alterar as funções de captura e reprodução. O executor legado recebe perfil explícito; manter os mesmos argv/políticas para cada provedor no perfil padrão. Adaptar `BuildAskCallTest` a perfis sem apagar as verificações de acesso.
- [ ] Fazer o estado de envio transitar de `not_submitted` para `submitting` **antes** da operação externa. Só `not_submitted` permite fallback; erro durante `submitting` exige reconciliação. Garantir essa regra também no timeout e no cancelamento do roteador.
- [ ] Atualizar histórico pela conclusão real. Handoff é apenas estado de apresentação, não uma resposta definitiva. Enquanto o terminal tem a entrada, novos pedidos de voz são serializados e encaminhados ao ID explícito; ausência de destino claro produz esclarecimento.
- [ ] Exibir os quatro modos nos painéis QML/curses e configuração, com dependências claras e estado efetivo. Remover exemplos/chips “pense bem”/“think hard”; substituir por modo de atendimento e agente configurado. Textos pt-BR/en mantêm paridade.
- [ ] Abrir terminal com o ID já existente ao atingir `handoff_seconds_quick` ou por ação explícita. Assegurar que falha ao abrir janela não mata a sessão nem reenvia o trabalho.
- [ ] Rodar `python -m unittest tests.test_conversation_routing tests.test_i18n tests.test_actions tests.test_events -v` e inspeção visual dos painéis disponíveis. UI não deve anunciar modo interativo quando estiver no backend legado.

## Task 8: Distribuição, matriz de regressão e reversão

**Files:** modificar `install.sh`, `README.md`, `SECURITY.md`; criar `tests/test_install_routing.py` e `tests/fixtures/routing_pt_br.json`.

- [ ] Adicionar os novos módulos leves à lista `SCRIPTS` de instalação/remoção, mantendo o classificador e seus pacotes fora da instalação normal. Testar staging em diretório temporário com comandos externos simulados; não reinstalar o serviço do usuário para provar cópia de arquivos.
- [ ] Atualizar guia, exemplos, diagrama, requisitos e configurações. Explicar assinatura, API opcional, classificação local versus resposta local e modo de compatibilidade. Registrar em `SECURITY.md` que roteamento não eleva acesso e que resposta API não executa marcadores textuais.
- [ ] Construir conjunto de 60 pedidos: 10 respostas simples, 10 consultas externas, 10 tarefas de projeto, 10 análises profundas, 10 continuações contextualizadas e 10 negações/ambiguidades. Cada caso contém `text`, `context`, `expected_route`, `needs_computer` e perfis permitidos. Não usar dados privados. Comparar cada modo na mesma base e registrar acertos, encaminhamentos indevidos, erros confiantes e latência p50/p95.
- [ ] Executar a matriz abaixo e anotar `passou`, `falhou` ou `não executado` com causa. Nunca converter falta de chave/acesso em integração validada.

| Cenário | Prova exigida |
|---|---|
| Nova config sem chave, sem GLiNER e sem GPU de classificação | Resposta e ação por CLI autenticado; nenhuma chamada dos roteadores |
| Config/perfil antigo com chave de STT | Mesmo agente/modelo/esforço, sem API de respostas acionada |
| Só Codex / só Claude disponível | Uso normal sem depender do outro fornecedor |
| `off`, `ask`, `full` | Mesmas restrições e consentimentos antes/depois; sem promoção por fallback |
| Cada modo opcional sem sua dependência | Motivo visível e retorno ao agente antes do envio |
| Roteador funcionando, execução falha após aceite | Mesma sessão reconciliada; nenhum efeito duplicado |
| Terminal ativo e tarefa concluída | Teclado e voz continuam no mesmo ID e diretório |
| CLI sem capacidades nativas | Executor legado ainda responde e se identifica como compatibilidade |
| Interrupção, ditado, narração e Picoh | Comportamentos existentes preservados |
| Modo local quente/frio | CPU confirmada, classificação e fallback medidos, sem alocação CUDA pelo worker |

- [ ] Rodar a suíte completa uma vez após as mudanças integradas e os checks de shell/compilação dos arquivos alterados. Repetir apenas em caso de novas mudanças ou falhas. Evidências de sessões reais são separadas dos testes simulados.
- [ ] Provar reversão de configuração em ambiente de teste:

```bash
jarvis config set routing_mode agent
jarvis config set agent_session_mode legacy
```

Esses comandos são para a futura verificação autorizada, não para executar durante
o planejamento. Validar também que dados de perfis antigos não foram descartados
ao salvar e que um checkout anterior consegue lê-los.

- [ ] Na entrega da implementação, apresentar diff, testes, limitações dos backends opcionais e resultado real do caminho por assinatura. Commit/push somente conforme autorização do usuário; nenhuma etapa deste plano autoriza publicação por si só.

## Ordem e condição de conclusão

Executar 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Cada tarefa fecha sua própria verificação;
o launcher em uso permanece no caminho por assinatura até a integração estar
validada. As etapas são decomposição da entrega completa, não uma proposta de
encerrar em um protótipo.

A condição prioritária de conclusão é o usuário sem chave de API e sem
classificador continuar usando o Jarvis pelo agente autenticado. O sucesso dos
backends opcionais nunca compensa uma regressão nesse caminho.
