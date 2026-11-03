# Experimento simplificado: navigation-v1-topics-arguments

Atualização do localizador: [citações descontínuas e revalidação offline com local-quote-v6](kg_discontinuous_evidence.md).

Contrato independente `kg-navigation-v1`, voltado a assuntos pesquisáveis e argumentos de uma página física. Não substitui contratos anteriores nem altera resultados históricos. A implementação não executou inferência paga.

## Semântica e redução

A saída contém `schema_version` e quatro arrays: `concepts`, `arguments`, `evidence`, `warnings`. Sem entidades, claims, atribuições, eventos, participantes, unidades discursivas, relações ou autoconfiança.

- Conceitos: `id`, rótulo português curto/reutilizável, sentido contextual, `salience` (`primary`: assunto central/desenvolvido; `secondary`: discussão secundária relevante), referências a evidências.
- Argumentos: `id`, `text` compreensível fora do parágrafo, `concept_ids`, `evidence_ids`. Preservam negação, condições, hipóteses, objeções e contraposições sem exigir identificar o titular da posição. Uma tese discutida não vira fato histórico estabelecido.
- Evidências: mesmos campos de proveniência (`id`, `source`, `locator`, `quote`). OCR é primário; segmentos atuais de resumo também podem fundamentar objetos. Contexto acumulado não é evidência. Cada conceito/argumento exige ao menos uma evidência; `concept_ids` pode ser vazio se não houver associação temática segura. Arrays de objetos vazios são válidos.
- Avisos: `code`/`message`, com códigos para problemas de OCR, conflito de fontes, ambiguidade interpretativa e cobertura incompleta.

Com `concepts_relations`, o system congelado tem 409 palavras (contagem por espaços), contra 1.246 no v3 e 1.327 no v3.1. O schema JSON compacto ocupa 1.975 caracteres, contra 5.187/6.755. São medidas de complexidade textual, não estimativas de qualidade ou tokens faturados.

`concepts_relations` permanece como seletor existente de CLI/UI, mas a variante substitui suas instruções de modo: conceitos + argumentos, sem relações. `concepts_only` exige argumentos vazios; `full_multimodal` mantém o contrato novo e permite a imagem. A política de fac-símile é independente do modo. Nenhum componente de confiança ou exemplo antigo é anexado.

## Integração

`validation_current.py` encaminha somente este schema ao validador independente `navigation_contract.py`, antes do código histórico. Ele verifica JSON estrito, schema, campos extras, enums, IDs globais dentro da página, referências tipadas a conceitos/evidências e localização das citações. Usa a política congelada `local-quote-v5`: microdiferenças localizáveis permanecem diagnosticadas; citações/fontes não são reescritas. Locators inventados são rejeitados. Offsets, substrings e diagnósticos são preservados nos resultados do localizador. Localização, posição única e interpretação correta continuam sendo critérios distintos. Truncamento do provedor invalida a resposta e preserva o conteúdo bruto.

A interface mostra conceitos/argumentos, permite selecionar evidências e seguir os assuntos associados a um argumento, salvar revisões e comparar runs. Não cria arestas `AFFIRMS`/`REFUTES`. Comparação entre contratos alinha conceitos conservadoramente; argumentos não são automaticamente equiparados a claims. Argumentos aparecem em JSONL, CSV e relatórios Markdown com contagens próprias. Os JSONs históricos não são preenchidos ou migrados para satisfazer o novo contrato.

Uma política exclusiva da variante rejeita metadados extras de hints/índices editoriais no payload. Em `index_works`, projeta somente campos bibliográficos conhecidos e exclui `raw_json`, `source_section_key` e campos extras; os caminhos omitidos ficam em `input_preparation`. O sinal estrutural `static_analysis.page_kind_hints` é preservado: ele descreve tipos de página, não assuntos de um índice externo. Textos originais de OCR/resumo, inclusive conteúdo editorial da própria página, continuam preservados. Nenhum índice externo é consultado na preparação a partir de `--case-ids`.

## Arquivos

Novos:

- `tools/knowledge_graph/schemas/kg-navigation-v1.schema.json`
- `tools/knowledge_graph/prompts/extract_navigation_v1.system.txt`
- `tools/knowledge_graph/prompts/navigation_v1_modes.json`
- `tools/knowledge_graph/playground/navigation_contract.py`
- `tools/knowledge_graph/playground/fixtures/page-output.navigation.demo.json` (sintético)
- `tests/test_kg_navigation_v1.py`
- `docs/kg_navigation_v1.md`

Integrações alteradas:

- `.gitignore` (artefatos locais `.work/kg_navigation_v1/`)

- `tools/knowledge_graph/prompts/registry.json`
- `tools/knowledge_graph/playground/{prompt_registry,validation_current,experiment_runner,storage,comparison,reporting}.py`
- `tools/knowledge_graph/playground/static/{index.html,playground.js}`

O CLI existente já suporta `--case-ids`, variantes registradas, dry-run, resume, inspect e export; não foi necessário alterar seu parser. SQLite continua em WAL, com novas runs separadas. Bases de OCR/resumos e campanhas anteriores não são regravadas.

## Verificação local

Executado via Native Lab MCP, sem transporte real aos provedores:

```bash
.venv/bin/python -m pytest \
  tests/test_kg_navigation_v1.py tests/test_kg_discourse_v31.py \
  tests/test_kg_statement_v3.py tests/test_kg_playground.py \
  tests/test_kg_provider_adapters.py tests/test_kg_playground_web.py \
  tests/test_kg_reporting.py tests/test_kg_evidence_validation.py \
  -q --disable-warnings --junitxml=.work/kg_navigation_v1/tests.xml
node --check tools/knowledge_graph/playground/static/playground.js
```

Resultado: **239 passed**, sintaxe JS válida. Inclui schema, arrays vazios, IDs/referências, OCR grego/latino e resumo atual, quotes inventadas/microdiferenças, fixtures com negação/condições/objeções, ausência de hints, Meta com imagem e transporte mockado, armazenamento/retomada/revisão/exportação/comparação, imagem arquivada/tampering e regressões dos contratos anteriores. As fixtures verificam preservação dos textos qualificados, não a capacidade de um modelo real de interpretá-los.

Interface validada com a skill `playwright-local-browser-validation` e Native Lab Playwright usando **somente fixtures sintéticas** em `.work/kg_navigation_v1/ui_fixture.db`:

```bash
.venv/bin/python scripts/playgrounds/kg_extractor_playground.py serve \
  --db .work/kg_navigation_v1/ui_fixture.db \
  --summaries-db .work/kg_navigation_v1/absent.db \
  --indices-db .work/kg_navigation_v1/absent_indices.db \
  --benchmark-dir .work/kg_navigation_v1/absent_benchmark --port 8779
```

URL: `http://127.0.0.1:8779/`; viewports 1920×1080 e 375×812. Seleção de argumento, vínculo ao conceito, localização com destaque no OCR, revisão separada e comparação v3/navigation funcionaram. Zero erros/warnings de console, requests observados HTTP 200, sem overflow horizontal. Screenshots inspecionados: `.work/kg_navigation_v1/desktop.png` e `mobile.png`. Logs `browser-console.txt` e `browser-network.txt` na mesma pasta. Não há build frontend: este playground serve HTML/JS/CSS diretamente pela biblioteca padrão Python.

Limites: nenhuma resposta real produzida pelo novo prompt foi avaliada; compatibilidade Meta foi exercitada na construção da requisição e com mocks, não em chamada ao serviço. Semântica permanece pendente de revisão. Nenhum ganho de cobertura/qualidade/custo é inferido das fixtures.

## Run real preparada — nenhuma inferência

- Experimento: **EXP-1004e90f0360**; run **1715**, status `planned`, exatamente uma run.
- Caso histórico **1034**, `PG026:72`; geração v2 **45223**, shadow `valid`, mesmo snapshot dos controles 1691/1693/1694/1697.
- Snapshot SHA-256: `a6dd9c52389e2ee45040fe4dc5aba0f06acf291dc32b95072db1b7d0a3f65095`.
- 10 blocos de OCR, dois segmentos atuais, contexto acumulado identificado separadamente.
- OCR histórico SHA-256: `8dec2c7de0d5d69f57fdc6a843d791c41ce5f34cb9d9e4ee66a1a34619f0031f`.
- Imagem arquivada SHA-256: `62282baf05761d1053659f740af944c5daacef8aee264f8fceccb8d84e2c498a`.
- Imagem de transporte SHA-256: `0609728ef96cb93e84a766a541aaafcb7ccb32e9dce11f74f87ac25897894f4e`, igual ao transporte histórico.
- Meta / `muse-spark-1.3-contributor`, reasoning `medium`, fac-símile `on`, uma repetição, bypass de cache.

Comando **efetivamente executado**, preservando o snapshot em vez de recarregar arquivos atuais:

```bash
.venv/bin/python scripts/playgrounds/kg_extractor_playground.py run \
  --case-ids 1034 \
  --provider meta --model muse-spark-1.3-contributor \
  --prompts navigation-v1-topics-arguments \
  --mode concepts_relations --facsimile on --reasoning-effort medium \
  --repetitions 1 --bypass-cache --dry-run \
  --name 'Navigation v1 · PG026:72 · frozen source'
```

A preparação não acessa o OCR atual nem consulta índices externos. O caso arquivado permanece integral; somente a projeção de entrada exclui excertos brutos de índices. A normalização existente do OCR para transporte ao modelo continua registrada em `ocr_preparation`, com o original intacto. A inspeção read-only está em `.work/kg_navigation_v1/check_preflight.py`; resultado em `preflight.json`, cópias do prompt/schema em `system_prompt.txt` e `response_schema.json`. Verifica hashes, ausência de resposta/validação antes de execução, uma única run criada, imagem realmente incluída na requisição congelada, ausência de hints e igualdade dos registros históricos usados como controles.

**A executar manualmente pelo usuário**, com `META_API_KEY` no ambiente:

```bash
python scripts/playgrounds/kg_extractor_playground.py resume \
  --experiment EXP-1004e90f0360 --concurrency 1
```

Esse comando não foi executado durante a implementação. Não repita `run --dry-run` para iniciar o experimento já preparado; use `resume`.

Inspeção e exportação (a inspeção da preparação já funciona; comparação de conteúdo exige a resposta):

```bash
python scripts/playgrounds/kg_extractor_playground.py serve
# http://127.0.0.1:8766 — Histórico & relatórios → run 1715

python scripts/playgrounds/kg_extractor_playground.py inspect \
  --run 1715 --output .work/kg_navigation_v1/run_1715.json

python scripts/playgrounds/kg_extractor_playground.py report \
  --experiment EXP-1004e90f0360 --format markdown \
  --output .work/kg_navigation_v1/report.md

python scripts/playgrounds/kg_extractor_playground.py compare \
  --runs 1715 1691 1693 --output .work/kg_navigation_v1/comparison.json

python scripts/playgrounds/kg_extractor_playground.py export \
  --experiment EXP-1004e90f0360 --output .work/kg_navigation_v1/results.jsonl

python scripts/playgrounds/kg_extractor_playground.py validate \
  --run 1715 --dry-run --output .work/kg_navigation_v1/validation.json
```

1691 é Muse/v3; 1693 é Muse/v3 com prompt reordenado. Para GPT-5.4-mini/v3, use 1697. O comparador preserva coleções diferentes como diferentes; a utilidade temática e a fidelidade dos argumentos exigem leitura e revisão. Não interprete ausência de arestas no novo contrato como perda de cobertura argumentativa.

## Repetição Muse e comparação OpenAI Flex (2026-11-03)

Após a run 1715, foram preparados dois novos experimentos, sem inferência:

| Experimento | Modelo | Repetições | Runs |
|---|---|---:|---|
| EXP-653a35adda56 | Muse Spark 1.3 Contributor | 1 | 1716 |
| EXP-b173842581fd | gpt-5.4-mini | 2 | 1717, 1718 |
| EXP-b173842581fd | gpt-5.4-nano | 2 | 1719, 1720 |
| EXP-b173842581fd | gpt-5.6-luna | 2 | 1721, 1722 |
| EXP-b173842581fd | gpt-6-luna | 2 | 1723, 1724 |

Todas usam o caso 1034, o mesmo prompt/schema/payload e imagem da run 1715, reasoning medium e bypass de cache. A orientação de Unicode não foi modificada nesta comparação. Arquivos locais: `.work/kg_navigation_v1/openai_flex_models.json` e `multimodel_preflight.json`.

O CLI agora aceita `--service-tier flex` em `run` e `corpus-create`, e o campo `service_tier` nos perfis JSON. É opcional para preservar requisições antigas e aceito somente no provedor OpenAI. Todas as novas runs OpenAI acima enviam explicitamente `flex`, timeout 900 s e até duas retentativas de transporte com backoff/jitter, sempre mantendo Flex. Não há fallback automático para Standard. A resposta bruta do provedor preserva o tier efetivamente retornado, quando informado. O tier participa da chave de cache via configuração e corpo da requisição.

Foi adicionada uma projeção do schema navigation para o subconjunto OpenAI: `uniqueItems` e `minLength` são omitidos somente do schema de transporte; `pattern`, `minItems`, enums e estrutura permanecem. As restrições originais continuam validadas localmente; schema local e prompt congelados são iguais aos do Muse. A projeção fica registrada em `request.schema_projection` e não altera schemas ou respostas históricos.

Referências oficiais consultadas: [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna). A disponibilidade da conta não foi testada com chamadas reais. Não foram inventadas tarifas Flex: os perfis OpenAI usam `prices: {}`, mantendo o custo desconhecido até fornecer tarifas específicas.

Comandos de preparação já executados:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --case-ids 1034 --provider meta --model muse-spark-1.3-contributor \
  --prompts navigation-v1-topics-arguments --mode concepts_relations \
  --facsimile on --reasoning-effort medium \
  --repetitions 1 --bypass-cache --dry-run \
  --name 'Navigation v1 · Muse retry · frozen PG026:72'

python scripts/playgrounds/kg_extractor_playground.py run \
  --case-ids 1034 --model-configs .work/kg_navigation_v1/openai_flex_models.json \
  --prompts navigation-v1-topics-arguments --mode concepts_relations \
  --facsimile on --repetitions 2 --bypass-cache --dry-run \
  --name 'Navigation v1 · OpenAI Flex four models × two repetitions'
```

Para iniciar manualmente, com META_API_KEY e OPENAI_API_KEY no ambiente:

```bash
python scripts/playgrounds/kg_extractor_playground.py resume \
  --experiment EXP-653a35adda56 --concurrency 1
python scripts/playgrounds/kg_extractor_playground.py resume \
  --experiment EXP-b173842581fd --concurrency 8
```

Testes via Native Lab: **200 passed** em `tests/test_kg_service_tier.py`, `test_kg_provider_adapters.py`, `test_kg_navigation_v1.py`, `test_kg_discourse_v31.py`, `test_kg_statement_v3.py`, `test_kg_playground.py` e `test_kg_corpus_runner.py`. Incluem CLI, recusa de tier em outros provedores, preservação de Flex após 429 com transporte mockado, invalidação de cache e conservação da validação local do schema. Nenhuma chamada paga foi executada.
