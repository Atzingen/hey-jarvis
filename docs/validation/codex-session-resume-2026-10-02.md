# Retomada de conversa Codex no Jarvis — 2026-10-02

## Diagnóstico

O Jarvis estava na rota `agent`, provedor Codex, sessão nativa persistente. O servidor
respondia a `account/read` com conta `chatgpt` autenticada. `thread/read` encontrava a
conversa salva, mas informava `status.type = notLoaded`; `turn/start` rejeitava a entrada
com código `-32600`, `thread not found`. O Jarvis convertia isso em resultado incerto e
orientava conferir o terminal. O login Claude também estava válido (`claude.ai`).

## Correção

Antes de enviar um pedido novo, o adaptador consulta a conversa. Se estiver `notLoaded`,
chama `thread/resume` com o mesmo `threadId` e `excludeTurns=true`. Depois confere novamente
se há turno ativo. Não cria outra conversa, não usa `--last`, não repete entradas anteriores
e não altera o modelo, a política de acesso ou o roteamento do usuário.

## Evidência

- Reprodução real antes da mudança: `thread/read` → `notLoaded`; `turn/start` → rejeitado.
- Retomada manual do mesmo ID: `thread/resume` → `idle`, conta e histórico preservados.
- 47 testes de adaptadores, sessões, roteamento de conversa e consentimento passaram.
  Dois testes novos cobrem retomada antes do envio e bloqueio quando o turno retomado
  já está ativo. Ambos falhavam antes da correção.
- Teste real isolado com Codex 0.159.2: primeiro pedido gravou o marcador `LAGO-427`;
  o servidor foi encerrado e relançado; `thread/read` confirmou `notLoaded`; o segundo
  pedido passou pelo adaptador corrigido e devolveu `LAGO-427`. Mesmo ID nativo,
  duas entradas distintas e nenhum reenvio.
- Teste com o `ConversationExecutor` instalado e a conversa real: respondeu
  `JARVIS_CONEXAO_OK`, backend `native`, mesmo ID nativo, sem fallback legado.
- Somente `~/.local/bin/jarvis_agent_codex.py` foi atualizado, com backup local;
  `voice-launcher.service` foi reiniciado. Configuração e mudanças de reconhecimento
  de fala previamente instaladas foram preservadas.

Comando dos testes relevantes:

```bash
python -m unittest tests.test_agent_codex tests.test_sessions tests.test_conversation_routing tests.test_agent_claude tests.test_session_consent
```

A validação acima exercita a sessão e a resposta do agente. Não simula fala pelo microfone.
