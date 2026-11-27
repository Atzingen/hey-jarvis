# Vocabulário no reconhecimento — 02/10/2026

O Jarvis aceita uma lista pessoal em `stt_vocabulary`, compartilhada pelo
reconhecedor escolhido e pelo fallback local. `stt_vocabulary_enabled=false`
desliga as pistas sem apagar a lista. O padrão de força do Nemotron é **0.5**.

## Modelo e instalação

- Runtime NVIDIA NeMo-Speech.cpp 0.1.0, Linux x86_64 CUDA.
- Nemotron 3.5 ASR 0.6B Q8, revisão
  `ea30d66debe3740a08b573244286791d423d6b3e`.
- GGUF de 742.090.464 bytes; SHA-256
  `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1`.
- O GGUF tem `asr.tokenizer.spm_model`, ausente no modelo anteriormente instalado.
- A atualização reutiliza o runtime da mesma versão/dispositivo, sem reescrever
  seus executáveis durante uma atualização somente do modelo.
- Sem o modelo atualizado, o reconhecimento continua disponível e o log informa
  que as pistas do Nemotron exigem `jarvis stt install-nemotron`.

Referências: [modelo oficial](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/tree/ea30d66debe3740a08b573244286791d423d6b3e),
[word boosting no runtime](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/docs/asr/configuration.md#word-boosting).

## Comparação de áudio

Foram usados áudios sintéticos em português: oito frases Piper e uma amostra
Kokoro Alex de 21 segundos. Uma lista local de 40 termos ocupou 146 tokens nos
tokenizadores Whisper small e large-v3-turbo. A lista pessoal não está neste repo.

O primeiro ensaio comparou força 0 e 2. Foram testados também 0.25, 0.5, 0.75 e 1.
Uma segunda rodada acrescentou 500 ms de silêncio inicial e 300 ms final aos
mesmos áudios, para avaliar o efeito das bordas da gravação.
Também foram comparados o GGUF antigo e o atualizado, ambos sem pistas: as nove
amostras produziram os mesmos textos após normalizar maiúsculas e pontuação.

| Força | Resultado observado |
|---|---|
| 2 | Corrigiu um caso de “Claude Code”/“README”, mas inseriu “README” no lugar de parte de um horário. Também distorceu uma frase com nomes de projetos. |
| 1 e 0.75 | Ainda alteraram indevidamente a expressão “quatorze horas” na amostra Alex. |
| 0.5 | Preservou os três controles de fala comum em relação à execução sem pistas. Os ganhos em nomes foram limitados; vários termos continuaram incorretos. |
| 0.25 | Na rodada inicial, não houve diferença textual nas seis amostras em relação à força 0. |

Por isso, o padrão foi reduzido a 0.5, em vez de adotar diretamente os valores
2–3 sugeridos pela documentação geral da NVIDIA. O resultado não demonstra
melhora geral de precisão nem garante comportamento com voz humana, sotaque,
ruído ou outro vocabulário. A configuração permite comparar sem pistas e ajustar
a força, sem introduzir uma etapa de revisão por LLM.

## Streaming e fallback

Na rodada final, o áudio Alex com silêncio nas bordas tinha 21,8 s. Enviado em
tempo real, em blocos de 80 ms, com força 0.5:

- 253 eventos parciais chegaram antes do commit;
- o primeiro parcial chegou em 1,648 s, incluindo o silêncio inicial;
- a sessão terminou em 21,850 s; o conteúdo do horário foi preservado.

Essa é uma observação pontual da máquina de teste, não uma garantia de latência.
As demais rodadas enviaram áudio tão rápido quanto possível e não medem atraso
de ditado ao vivo. Não foram simuladas teclas nem feitas chamadas pagas à API.

Uma conexão local recusada também foi exercitada com o `OpenAISession` real e
Whisper small em CPU, usando os pesos já em cache e os 40 termos. O fallback
transcreveu a frase completa sobre preparar o almoço e caminhar no parque,
sem GPU ou API, em 2,225 s incluindo o carregamento desse modelo.

## Testes automatizados

Os testes novos verificam o envio efetivo das pistas na configuração da sessão,
a desativação, substituição da lista automática, deduplicação e limite de 100
entradas, persistência do texto multilinha, compatibilidade com instalação antiga
e preservação do runtime existente na atualização do modelo. Foram executados
contra o código anterior e falharam nos comportamentos ainda ausentes.

Comando: `python -m unittest discover -s tests -q`, no ambiente `voice`.

Resultado: **232 testes, 230 passaram, duas falhas preexistentes**. A execução
antes das alterações teve as mesmas duas falhas em 224 testes:

- `test_picoh.Choreography.test_conversation_phases_are_all_distinct`;
- `test_picoh.Protocol.test_eye_brightness_quadratic`.

Essas falhas do Picoh não foram alteradas por esta implementação. O envio ao
OpenAI foi validado no contrato WebSocket em testes; não foi feita uma chamada
paga à API para medir reconhecimento remoto.

## Instalação em uso

Os quatro módulos modificados foram instalados em `~/.local/bin`, mantendo o
wrapper do ambiente Python. O modelo foi substituído atomicamente, com cópia de
recuperação do modelo anterior, manifesto, módulos e configuração. A configuração
pessoal recebeu os 40 termos e força 0.5; os outros valores foram preservados.

Após reiniciar `voice-launcher.service`, o serviço ficou ativo, sem reinícios
automáticos, e registrou `vocabulário: 40 termos` e Nemotron em CUDA. Um cliente
de teste conectado ao próprio subprocesso Nemotron desse serviço, usando os
módulos instalados e a configuração pessoal, entregou 35 eventos parciais antes
do commit e transcreveu corretamente a frase de controle sobre almoço e parque.
Os quatro arquivos instalados foram comparados byte a byte com o checkout.
