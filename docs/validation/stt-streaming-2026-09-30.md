# Transcrição contínua integrada — 30/09/2026

Implementados `stt_provider = "nemotron"` e `"openai"`, mantendo Whisper e a
seleção `auto` anterior. `dictation_live = true` mostra palavras provisórias na
janela e digita frases confirmadas nas pausas. Ao terminar, copia o texto completo
sem colar novamente e sem revisão por LLM. A configuração padrão de instalações
novas mantém `dictation_live = false`.

Nesta máquina, o runtime foi instalado e o serviço reiniciado com
`stt_provider = "nemotron"` e `dictation_live = true`. O journal confirmou
`nemotron 3.5 0.6b/cuda0` e o estado pronto. A chave OpenAI existente, as
preferências do roteador e o wrapper do ambiente de voz foram preservados.

## Verificação real

Máquina: Linux/Hyprland, i7-14700KF, RTX 4090. Áudio sintético Kokoro Alex em
português, enviado em blocos de 160 ms no ritmo da gravação. Nenhum áudio privado
foi necessário para esta verificação. O fallback foi desabilitado no smoke de
cada backend, para comprovar qual deles realmente transcreveu.

| Backend | Áudio | Primeira parcial | Parciais antes de terminar o áudio | Resultado |
|---|---:|---:|---:|---|
| Nemotron 3.5 0.6B Q8 / CUDA | 21 s | 1,246 s | 232 | Três trechos confirmados pelo `LiveDictation` com Silero VAD real |
| OpenAI `gpt-live-transcribe` | 8 s | 2,809 s | 21 | Sessão real, sem erro nem fallback |

No Nemotron, as confirmações ocorreram em 2,525 s, 12,130 s e 21,050 s desde o
início do áudio. Os primeiros trechos chegaram enquanto a gravação continuava.
A OpenAI foi verificada por chamada de API; sua integração com o controlador de
ditado compartilhado também está coberta pelo teste do controlador.

São medições pontuais de áudio limpo, não garantias de latência ou precisão para
outros computadores, vozes ou ambientes. O teste anterior de CPU e os limites de
macOS estão no [registro da avaliação isolada](nemotron-stt-2026-09-30.md).

A escrita também foi verificada em um QTextEdit descartável no Hyprland, usando
`LiveOutput` e o `wtype` reais: dois trechos com acentos chegaram na ordem correta,
o clipboard recebeu o texto completo e não houve segunda colagem ao finalizar.
O foco e o clipboard anteriores foram restaurados. Não equivale a uma validação
com a voz do usuário nem com todos os editores e terminais.

## Regressões e interface

- 41 testes direcionados passaram: STT, instalação, ditado e entrega incremental.
- 13 verificações QML passaram, incluindo ESC, texto parcial, aviso de foco e
  identificação do backend efetivo.
- Suíte Python completa: 222 testes, 220 passaram. As duas falhas preexistentes
  de Picoh continuam: `test_conversation_phases_are_all_distinct` e
  `test_eye_brightness_quadratic`.
- `install.sh --stage` incluiu os módulos novos sem baixar o modelo opcional.

Os testes cobrem falha de conexão/protocolo preservando o áudio para Whisper,
cancelamento durante a finalização e durante a espera antes de digitar, mudança
de janela, ausência de instalação/chave, confirmação sem duplicação e preservação
do fluxo anterior com revisão opcional.

## Reproduzir os checks sem chamadas pagas

```bash
python -m unittest tests.test_stt tests.test_nemotron tests.test_dictation_stream \
  tests.test_dictation tests.test_install_routing -q
QT_QPA_PLATFORM=offscreen QML_IMPORT_PATH="$PWD/app" \
  /usr/lib/qt6/bin/qmltestrunner -input tests
bash install.sh --stage /tmp/jarvis-stt-stage
```

Use o Python do ambiente de voz. A localização do `qmltestrunner` depende da
distribuição. A [seção de configuração no README](../../README.md#texto-enquanto-você-fala)
explica instalação, escolha de backend e como voltar ao Whisper.

## Correção da inserção ao parar

Após o teste do usuário, a saída ao vivo passou a respeitar `dictation_output`:
`paste` cola os trechos em vez de simular suas letras. Uma consulta ao estado dos
modificadores do Hyprland substitui a suposição de que eles seriam soltos dentro
dos 300 ms iniciais. Quebras de linha são removidas também na colagem incremental.

Dois testes novos falharam antes da correção: a configuração `paste` era ignorada
e a inserção acontecia com Ctrl ainda pressionado. Depois, passaram junto aos
testes de foco, cancelamento e ditado. Em um editor Qt descartável, segurando Ctrl
por um teclado de teste via uinput, a versão corrigida esperou sua liberação e
inseriu o texto sem acionar o atalho de impressão. O sintoma exato da janela de
imprimir relatado no aplicativo do usuário não foi reproduzido nesse editor.

A suíte completa após essa correção teve 224 testes: 222 passaram e permaneceram
somente as duas falhas de Picoh listadas acima. Os dois módulos corrigidos foram
instalados, com reinício do serviço e confirmação de Nemotron/CUDA pronto.
