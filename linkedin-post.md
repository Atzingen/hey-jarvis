# Como o Claude Code mudou meu fluxo de desenvolvimento — e por que montei um setup com 3 agentes em paralelo

Faz alguns meses que eu escrevo código diferente.

Não "com ajuda de IA". Com *vários* agentes trabalhando em paralelo enquanto eu oriento.

O gatilho foi simples: percebi que estava subutilizando o Claude Code rodando um agente por vez. Se eu tenho três tasks independentes — investigar um bug, refatorar um módulo, escrever testes — por que fazer uma de cada vez?

Daí nasceu o **hey-jarvis**, meu launcher de voz que automatiza o setup inteiro no Linux + Hyprland (Omarchy).

Eu digo: **"hey jarvis, abrir iaprev"**

E em 2 segundos:

- Grid 2×2 de terminais Ghostty no workspace 1
- 3 deles já rodando `claude --dangerously-skip-permissions` no diretório do projeto
- 1 shell limpo pra comandos manuais
- VS Code no workspace 6, dividindo a tela com o Chrome
- Chrome já com GitHub + claude.ai abertos

Cada agente cuida de uma parte da feature. Um investiga, outro implementa, outro testa. Eu viro coordenador — leio diffs, dou feedback, deixo rodar.

**A stack:**
- `openWakeWord` pra detectar "hey jarvis" localmente (~2% de CPU, sempre ligado)
- `faster-whisper` pra transcrever pt-BR
- `piper` TTS pra falar de volta em português
- Hyprland/Omarchy pelo controle declarativo de layout (`hyprctl dispatch`)
- Claude Code fazendo o trabalho pesado

Ainda tem o modo **"pense bem..."** que chama Opus com effort alto pra problemas de arquitetura, enquanto perguntas rápidas vão pro Codex gpt-5.4/low e voltam em ~5s — tudo narrado por voz. Falar "hey jarvis" de novo durante uma resposta cancela o TTS e a chamada em andamento.

**O que mudou pra mim:**

1. O tempo entre "ter a ideia" e "ver código rodando" caiu pra segundos.
2. Rodar múltiplos agentes em paralelo me forçou a pensar em tasks menores e mais independentes — uma boa prática que eu fingia seguir.
3. A voz tirou uma fricção mental que teclado-e-mouse criam. Eu continuo olhando pro código, o ambiente se configura sozinho.

A lição maior: **o gargalo deixou de ser "quanto código consigo escrever" e virou "quantos agentes consigo coordenar bem".** Essa é uma habilidade diferente, e acho que é pra onde o desenvolvimento de software tá indo.

Repo aberto, MIT: github.com/Atzingen/hey-jarvis

Alguém mais tá rodando múltiplos agentes em paralelo? Como vocês orquestram?

#ClaudeCode #Omarchy #Hyprland #DevTools #AI
