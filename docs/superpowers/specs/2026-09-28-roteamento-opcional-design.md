# Roteamento opcional com preservação do agente por assinatura

Data: 2026-09-28. Documento de projeto; não descreve funcionalidades já instaladas.

## Objetivo e decisão principal

Adicionar opções de roteamento ao Jarvis sem retirar o caminho que usa diretamente
Codex CLI ou Claude Code com o login do usuário. O padrão de instalações novas e
atualizadas será **Agente direto — assinatura**. API e classificador local são
opt-in, inclusive quando uma chave já existir para reconhecimento ou narração.

Esta decisão substitui a proposta anterior de usar um modelo por API como padrão.
O modelo inicial por API continua disponível como uma das opções.

## Requisitos globais

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

## Quatro modos visíveis

| Valor | Nome na interface | Decisão e execução |
|---|---|---|
| `agent` | Agente direto — assinatura | Envia ao CLI selecionado, sem classificador anterior. Mantém modelo/esforço atuais. |
| `assistant` | Modelo inicial por API | Um modelo responde ou pede encaminhamento estruturado; exige ativação explícita e configuração da API. |
| `jev` | Roteador Jev | Jev classifica; o executor continua sendo o agente por assinatura, ou a API se explicitamente habilitada. |
| `local` | Classificador local — CPU | GLiNER classifica; o executor continua sendo o agente por assinatura, ou a API se explicitamente habilitada. |

Jev e GLiNER não redigem respostas. Portanto, `local` significa classificação
local, não um assistente generativo inteiramente offline. O modo por assinatura
continua precisando da conectividade e do acesso do CLI, como atualmente.

No modo `agent`, não adicionar uma segunda chamada ao CLI só para classificar.
O agente interpreta o pedido e usa suas ferramentas normalmente. A seleção
automática de modelo/esforço pertence aos modos opcionais; quem preferir o caminho
direto conserva o controle existente na configuração, sem frase obrigatória.

## Configuração e compatibilidade

Manter as chaves existentes `quick_provider`, `codex_model`, `codex_effort`,
`codex_fast`, `claude_quick_model` e `claude_quick_effort`; atualizar apenas seus
rótulos para "agente", "modelo" e "esforço". Não renomear chaves consumidas por
scripts ou perfis já salvos.

Novas chaves propostas:

| Chave | Default | Regra |
|---|---|---|
| `routing_mode` | `agent` | `agent`, `assistant`, `jev`, `local` |
| `api_provider` | `none` | `none` ou `openai`; escolha explícita permite respostas por API nos modos opcionais |
| `api_model` | string vazia | Obrigatório para o modo API; não escolher um modelo pago por descoberta automática |
| `api_strong_model` | string vazia | Opcional; vazio impede escalada para um segundo modelo por API |
| `api_effort` | `low` | Validar suporte do modelo antes da primeira execução |
| `api_strong_effort` | `high` | Usado apenas com perfil forte configurado e suportado |
| `agent_strong_model` | string vazia | Opcional, do mesmo provedor do agente selecionado |
| `agent_strong_effort` | string vazia | Vazio herda o esforço atual |
| `jev_api_key` | string vazia | Segredo separado, com alternativa `JEV_API_KEY` |
| `router_timeout_seconds` | `5` | Prazo de classificação Jev/local; não é promessa de latência |
| `api_timeout_seconds` | `30` | Prazo da chamada inicial por API |
| `local_router_idle_seconds` | `300` | Encerra o worker de classificação após inatividade |
| `agent_session_mode` | `auto` | Sessão nativa se o adaptador suportar todas as capacidades necessárias; `legacy` força o executor atual |

`openai_api_key` pode ser reutilizada tecnicamente quando `api_provider=openai`,
mas sua presença sozinha não habilita nada. Um erro de configuração informa o
motivo e usa o agente direto, sem alterar silenciosamente o valor salvo.

As chaves `deep_model`, `deep_effort`, `handoff_seconds_deep` e
`narration_interval_deep` tornam-se legadas, sem efeito no novo fluxo. Devem
continuar legíveis e preservadas no save/load de configurações e perfis para
permitir rollback. Não converter `deep_model` automaticamente em outro provedor
nem exigir uma assinatura Claude de quem usa apenas Codex. Usar os valores
existentes de `handoff_seconds_quick` e `narration_interval_quick` como prazos
gerais; atualizar seus rótulos sem mudar os valores.

## Decisão e encaminhamento

O roteador recebe pedido original, contexto recente limitado, projeto atual,
sessão ativa e catálogo de perfis realmente disponíveis. Retorna intenção,
necessidade de ferramentas, complexidade e indicação de continuação. Um
resolvedor determinístico transforma isso em um perfil configurado. Não aceitar
nomes de modelos, comandos de shell, caminhos arbitrários ou IDs de sessão
inventados pelo classificador.

Rotas internas: `answer`, `api`, `agent`, `clarify`.

- `answer`: somente resposta concluída pelo modelo inicial por API.
- `api`: resposta ou consulta por API, com perfil explicitamente habilitado.
- `agent`: execução no agente configurado, eventualmente no perfil forte do mesmo provedor.
- `clarify`: falta informação indispensável, como o projeto entre dois candidatos reais.

O modo API usa uma chamada inicial com possibilidade de resposta textual,
busca web disponibilizada pelo provedor ou função `handoff`. A função aceita
somente `target` (`agent` ou `api_strong`), `needs_computer` e `complexity`
(`low`, `medium`, `high`); o programa resolve o perfil. Ela não executa comandos.
No máximo uma escalada por pedido. O executor de destino não chama o roteador de
novo. O histórico transferido inclui o pedido original, sem tratar a justificativa
do roteador como nova instrução.

Comandos locais de parar/encerrar continuam imediatos. Continuação vinculada
explicitamente à sessão ativa vai direto a ela; quando a vinculação não estiver
clara, usar o contexto para classificar ou pedir esclarecimento. Não considerar
toda fala durante uma sessão como continuação: uma pergunta independente pode
seguir outra rota. Uma mudança de modo vale para pedidos novos e não transporta
uma tarefa em andamento para outro executor.

Retorno ao agente direto: credencial ausente, backend indisponível, timeout antes
do encaminhamento, resposta inválida, perfil inexistente ou classificador ainda
carregando. Cancelamento não é falha elegível para fallback. Depois do envio a um
agente, queda de conexão exige consultar/reanexar a mesma sessão. Se o estado não
puder ser determinado, informar a incerteza; não iniciar uma segunda execução.

## Sessões e terminal interativo

Separar roteamento de execução. Os quatro modos usam o mesmo gestor de sessões,
e as melhorias de terminal também atendem quem usa somente assinatura.

Para capacidades nativas suportadas, iniciar uma sessão persistente desde o
primeiro pedido, em diretório resolvido a partir do projeto ou do diretório de
trabalho atual do Jarvis. Registrar ID nativo e IDs dos turnos. Conversas gerais
podem ficar em background; abrir um terminal normal ao atingir o prazo atual ou
quando o usuário pedir para acompanhar/continuar. A janela recebe a interface
nativa e teclado. Anexar não envia o pedido novamente.

Na máquina inspecionada: Codex CLI 0.156.1 expõe app-server, sessões e fila;
Claude Code 2.1.280 expõe `--bg`, `agents --json`, `attach` e `--resume`.
Isso prova disponibilidade dos comandos, não a integração completa com Jarvis.

- Codex: adaptador de app-server local com autenticação do CLI; eventos por thread/turn e TUI anexada à mesma sessão. Esse IPC local não é uma assinatura da API de respostas.
- Claude: sessão nativa em background e `attach`; eventos por hooks locais `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop` e `StopFailure`, escopados à sessão. Validar o canal de entrada por voz e a associação exata ao ID nativo antes de habilitar o adaptador.
- Se faltar capacidade comprovada de iniciar, obter resposta, cancelar, continuar ou anexar respeitando `off/ask/full`, selecionar o executor atual **antes** de enviar o pedido. Manter a indicação de modo de compatibilidade; não apresentar o viewer antigo como terminal interativo.

O modo `legacy` permanece como saída explícita de compatibilidade. A entrega da
melhoria de terminal exige provas reais nos dois CLIs suportados; a existência
do fallback não substitui esses testes.

Guardar estado privado fora do repositório. Registrar estado (`idle`, `running`,
`handed_off`, `completed`, `interrupted`, `unknown`), projeto, provedor, política,
ID nativo, ID de turno e dono atual da entrada (voz ou terminal). Serializar envios
por sessão para evitar colisão entre teclado e voz. Fechar/anexar uma janela não
reinicia uma tarefa. Após conclusão, uma nova entrada é um novo turno na mesma
conversa. Atualizar o histórico com a resposta real, inclusive após handoff.

Interrupção antes do handoff preserva o comportamento de parar o trabalho atual,
mas conserva o identificador da conversa quando o CLI permite. Encerrar a janela
de voz depois de entregar ao terminal não deve matar o trabalho entregue.
Prazos e limites de saída continuam valendo por turno; não encerrar uma sessão
ociosa apenas por ter atingido a idade máxima de uma tarefa.

## Instalação e experiência de configuração

A instalação padrão continua sem GLiNER, PyTorch adicional ou SDK obrigatório de
roteamento. API e Jev usam o padrão HTTP já existente no projeto. O classificador
fica em venv/worker separado; instalação por comando explícito, dependências com
lock/hash e revisão de pesos fixada. O worker força CPU, não é serviço sempre
ativo, e carrega apenas quando o modo local foi escolhido. Durante aquecimento,
pedidos podem seguir o agente direto. Não colocar downloads na conversa por voz.

Painel, configuração em terminal e versão sem Qt apresentam os mesmos quatro
modos. O modo direto diz claramente que usa o login do CLI. Exibir modo efetivo e
motivo de fallback sem revelar chaves. API não configurada e classificador não
instalado aparecem como tais; não interrompem o uso normal.

## Critérios de aceitação

1. Instalação sem chaves e sem classificador continua respondendo e executando ações pelo CLI autenticado selecionado.
2. Configuração antiga mantém os mesmos valores operacionais; nenhuma API nova é chamada mesmo se houver chave de STT/narração.
3. `pense bem` e `think hard` são texto comum e não forçam Claude, outro modelo ou esforço.
4. Os quatro modos são selecionáveis, com dependências explícitas e fallback observável.
5. Falha/cancelamento/reconexão não duplica efeitos ou reenvia automaticamente tarefas de estado incerto.
6. `off`, `ask` e `full`, interrupção, ditado, STT/TTS, narração e Picoh mantêm os contratos atuais.
7. Nos adaptadores nativos suportados, terminal e voz continuam a mesma conversa antes e depois de uma resposta, no projeto correto.
8. Instalação padrão e modo `agent` não carregam pesos do classificador nem reservam GPU para roteamento.
9. Retornar a `routing_mode=agent` e `agent_session_mode=legacy` restaura o executor por assinatura, sem apagar configurações opcionais.

## Fontes consultadas

- Checkout `f318c33`: `bin/voice-launcher.py`, `bin/jarvis_config.py`, testes, painel, instalador e README. Há alteração anterior não relacionada em `t3.json`.
- [OpenAI: chamadas de funções](https://developers.openai.com/api/docs/guides/function-calling).
- [OpenAI: app-server e ciclo de threads/turnos](https://learn.chatgpt.com/docs/app-server).
- [Anthropic: sessões em background e attach](https://claude.com/blog/agent-view-in-claude-code).
- [Anthropic: hooks de sessão, ferramentas e conclusão](https://code.claude.com/docs/en/hooks).
- [TypeSafe: documentação do Jev](https://docs.typesafe.ai/introduction).
- [Fastino: GLiNER2.5-multi-Decide](https://huggingface.co/fastino/GLiNER2.5-multi-Decide).
