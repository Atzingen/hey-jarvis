# Nemotron: avaliação local de transcrição — 30/09/2026

> Registro do teste isolado anterior à implementação. As duas opções foram
> integradas posteriormente; consulte a [validação da integração](stt-streaming-2026-09-30.md)
> para a configuração e os resultados atuais.

O Nemotron 3.5 ASR produziu texto parcial durante a reprodução de áudio em
português, tanto na RTX 4090 quanto usando somente o i7-14700KF desta máquina.
Foi um teste isolado: o Jarvis continua usando Whisper large-v3-turbo via
faster-whisper/CUDA. Não foi implementada escrita incremental no campo ativo.

## Resultado medido

Amostra Alex: 21 segundos de fala sintética Kokoro, enviada em tempo real,
em blocos de 160 ms. Cada bloco só foi enviado depois de decorrer sua duração.

| Execução Nemotron | Primeiro texto após início do áudio | Eventos parciais antes do encerramento | Final após encerramento |
|---|---:|---:|---:|
| RTX 4090 / CUDA | 1,284 s | 124 | 0,044 s |
| i7-14700KF / CPU | 1,344 s | 123 | 0,177 s |

Esses tempos incluem o conteúdo inicial da gravação; não representam a latência
de uma palavra individual. São medições pontuais com os serviços normais do
desktop ativos, sem garantia de desempenho em computadores mais fracos.

Uma segunda amostra, Dora, tem 20,725 segundos. Enviada tão rápido quanto possível,
foi processada em 0,413 s na GPU e 9,029 s na CPU. Esse teste mede capacidade de
processamento, não atraso de transcrição ao vivo.

Como referência, o Whisper atual levou 0,178 s para transcrever Alex e 0,182 s
para Dora com o modelo já carregado e aquecido. Ele recebe a gravação completa
nessa implementação; esses números não são latência de streaming. A primeira
inferência Whisper, antes dessas duas medições, levou 0,310 s.

## Qualidade e limites

- Ambos reconheceram o conteúdo principal das duas amostras.
- O Nemotron apresentou menos pontuação e maiúsculas, mesmo com pontuação
  automática habilitada; na amostra Dora também omitiu “horas”.
- As amostras são sintéticas e limpas. Não comprovam precisão com a voz do
  usuário, sotaques variados, ruído, ditados longos ou termos técnicos.
- MacBook não foi medido. O runtime oficial tem backends CPU e Metal para
  Apple Silicon, além de distribuição CPU para Mac Intel.
- Nenhuma chamada paga à OpenAI foi feita nesta avaliação.

## Reprodução e versões

Runtime NVIDIA NeMo-Speech.cpp 0.1.0, distribuição Linux x86_64 CUDA, também
capaz de executar com `--device cpu`. Modelo Q8_0 de 707,2 MiB, revisão
`1c8deaecc64b91f034d73e08dd8b64625eb3395d`. Download verificado por tamanho e SHA-256.

Servidor ligado somente em `127.0.0.1`, encerrado ao concluir cada backend:

```bash
nemo-speech serve --asr-model CAMINHO_DO_GGUF --device cuda \
  --host 127.0.0.1 --port PORTA_LOCAL --no-ui \
  --asr.endpointing.enable=false --asr.batching.enabled=false
```

Repetir com `--device cpu`; foi usado `OMP_NUM_THREADS=8`. O servidor faz seu
aquecimento normal. Pelo WebSocket `/v1/realtime`, configurar `language=pt-BR`,
`sample_rate=16000` e `automatic_punctuation=true`; enviar PCM16 mono, depois
`input_audio_buffer.commit`. O contexto direito RNNT padrão é 1. Dora foi
processada antes de Alex em cada backend.

[Resultados, transcrições e hashes](nemotron-stt-2026-09-30.json).

## Compatibilidade e API

A [instalação oficial do NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/install.md)
oferece CPU sem toolkit de GPU e Metal para Mac com Apple Silicon. A existência
do backend não garante tempo real em qualquer processador.

A OpenAI recomenda [gpt-live-transcribe para transcrição ao vivo](https://developers.openai.com/api/docs/guides/realtime-transcription).
O [preço consultado](https://developers.openai.com/api/docs/models/gpt-live-transcribe)
é US$ 0,017 por minuto, equivalente a US$ 1,02 por hora de áudio.

O Jarvis já contém um backend OpenAI Realtime, atualmente inativo nesta máquina.
Antes de ativá-lo com esse modelo, adequar o parâmetro `language` singular para
`languages`, conforme a documentação atual, e validar uma sessão real.
O fallback local existente deve permanecer disponível para quem não usa API.
