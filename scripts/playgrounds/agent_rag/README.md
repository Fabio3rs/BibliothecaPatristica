# Playground do agente RAG

O runner Node usa o mesmo loop, os mesmos schemas e as mesmas implementações de tools do site. A busca carrega os índices estáticos e o WASM do diretório `web/public`; o OCR é lido diretamente do corpus local em `teste/`. Nenhuma chave é gravada nos logs.

## Preparação

Requer Node 20 ou superior. Para Ollama Cloud:

```bash
ollama signin
ollama run gemma4:cloud
```

O daemon continua acessível em `http://127.0.0.1:11434/v1`. Para usar OpenAI, defina `OPENAI_API_KEY` e passe `--provider openai`; o modelo padrão desse perfil é `gpt-5.4-nano`.

## Fluxo recomendado

No root do repositório:

```bash
node scripts/playgrounds/agent_rag_benchmark.mjs plan
node scripts/playgrounds/agent_rag_benchmark.mjs validate --no-model
node scripts/playgrounds/agent_rag_benchmark.mjs validate
node scripts/playgrounds/agent_rag_benchmark.mjs run
```

`run` imprime o ID do experimento. Use-o nas etapas seguintes:

```bash
node scripts/playgrounds/agent_rag_benchmark.mjs review --experiment ID
node scripts/playgrounds/agent_rag_benchmark.mjs report --experiment ID
node scripts/playgrounds/agent_rag_benchmark.mjs inspect --experiment ID --case ocr-current-page
node scripts/playgrounds/agent_rag_benchmark.mjs inspect --experiment ID --run ocr-current-page__current_web__legacy_dsl__r1
```

Também é possível chamar pelo diretório `web`: `npm run benchmark:agent -- plan`.

## Screening e custo

A suíte tem 27 casos. Por padrão o runner executa somente os três finalistas do primeiro screening: `current_web__legacy_dsl`, `compact_strict__legacy_dsl` e `compact_strict__multi_query_rrf`, totalizando 81 execuções por repetição. `compact_strict` é o prompt compacto acrescido da regra explícita que impede inferir autenticidade, autoria e datação a partir de resumo ou OCR.

Use `--all-configs` para cruzar os quatro prompts com as duas estratégias, ou `--configs` para escolher um subconjunto. Faça primeiro recortes baratos:

```bash
node scripts/playgrounds/agent_rag_benchmark.mjs run \
  --tags context,negative \
  --configs compact__legacy_dsl,compact__multi_query_rrf \
  --max-runs 20
```

O benchmark é retomável. Uma execução já concluída é ignorada; `--retry-errors` repete somente erros. `--reasoning-effort none` permite a comparação posterior com o padrão inicial `low`.

Os casos adicionais verificam três regressões observadas no primeiro screening:

- query ausente no OCR deve retornar `matched_query=false`, nunca um trecho plausível do início;
- uma pergunta sobre a página inteira deve cobrir o OCR completo ou declarar a cobertura parcial;
- o mesmo `source_id` não pode mudar de significado entre turnos.

As validações mecânicas também conferem os argumentos produzidos pelo modelo contra o JSON Schema publicado pelas tools. A execução continua mesmo quando os argumentos são inválidos, para registrar como o runtime real os tratou.

## Artefatos

Cada experimento fica em `logs/agent-rag/ID/`:

- `manifest.json`: ambiente, corpus de casos e configuração geral;
- `configurations.json`: prompts e schemas completos efetivamente enviados;
- `runs.jsonl`: um registro estruturado por execução;
- `mechanical.jsonl`: verificações mecânicas separadas da avaliação semântica;
- `audit.json`: métricas e verificações recalculadas pelo código atual, inclusive para experimentos antigos;
- `raw/*.jsonl`: requests/responses integrais do provider e resultados integrais das tools;
- `traces/*.md`: transcrição legível por humano, incluindo eventos e reasoning exposto pelo provider;
- `reviews.jsonl`: julgamentos cegos por caso;
- `review.json`: síntese e mapa revelado das configurações;
- `report.md` e `report.json`: agregação final sem escolher um vencedor automaticamente.

Por padrão, `runs.jsonl` e os traces não duplicam cada delta de streaming nem os payloads integrais enviados ao modelo; esses dados continuam preservados em `raw/*.jsonl`. Use `--log-deltas` somente quando precisar depurar o streaming.

Execuções multi-turn preservam o `sourceRegistry` e registram, em cada turno,
`context_state` e `compaction`. Eventos `context_pressure`,
`context_compaction_start` e `context_compaction_end` permitem inspecionar quando
a histerese 112K/128K/144K do perfil Gemma 4 manteve o prefixo ou iniciou uma nova
época de contexto.

Em erros de limite, rounds, tool calls e tokens são derivados dos eventos já observados e ficam marcados como métricas parciais. O comando `report` reaplica as regras mecânicas atuais sobre runs antigos, portanto é possível melhorar a avaliação sem repetir chamadas ao modelo.

Os logs podem conter OCR e respostas extensas. Eles são ignorados pelo Git e devem ser inspecionados antes de qualquer decisão arquitetural; as verificações mecânicas e as heurísticas de resultado aceitável não substituem a leitura dos traces.
