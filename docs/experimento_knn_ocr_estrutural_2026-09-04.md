# Experimento estrutural de KNN sobre o OCR

Data: 2026-09-04.

## Escopo desta primeira implementacao

O experimento novo complementa, sem substituir, o baseline descrito em
`piloto_embeddings_ocr_2026-09-04.md`. Ele implementa somente o nucleo
necessario para comparar representacoes antes de escalar:

- IR estrutural auditavel por segmento;
- preservacao de linhas vazias antes da normalizacao;
- inferencia conservadora de funcao editorial e script;
- V0: todos os blocos mantidos por pagina;
- V1: somente corpo inferido por pagina;
- V2: corpo separado por script dentro da pagina;
- relacao N:N entre chunks e segmentos de origem;
- embedding documental puro, sem `Instruct:`;
- centroide de pagina com peso igual por unidade V2;
- KNN pagina-pagina bruto ou excluindo vizinhos fisicos;
- benchmark inicial grego-latim por pagina.

Ainda nao foram implementados nesta etapa:

- streams continuos entre paginas (V3);
- dehyphenation entre paginas;
- alinhamento automatico por numero de secao;
- expansao da Vulgata no texto de embedding;
- vetores separados de aparato e referencias;
- reranking por chunks.

Esses itens dependem do resultado de V0/V1/V2 e permanecem extensoes
comparaveis, nao pre-condicoes do primeiro embedding.

## Arquivos

- `patristica_pipeline/ocr_embedding_ir.py`: extracao da IR, normalizacao e
  montagem de V0/V1/V2;
- `tools/generate_ocr_embedding_experiment.py`: auditoria, preparacao do SQLite,
  embedding e centroides;
- `tools/query_ocr_page_knn.py`: KNN pagina-pagina sem query textual;
- `tools/benchmark_ocr_parallel_pages.py`: Recall@1, Recall@5, MRR e margem para
  pares grego-latim na mesma pagina;
- `tests/test_ocr_embedding_experiment.py`: regressoes focadas.

O banco padrao e:

```text
data/patristica_ocr_embedding_experiment.db
```

Ele e independente dos bancos de resumos e do piloto OCR anterior.

## IR

Cada registro JSONL contem, entre outros:

```text
segment_key
volume_id
work_label
page_num
source_file/source_hash
block_index/part_index
tag_name/xml_type
declared_script/inferred_script/script_confidence
inferred_role/role_confidence
bbox
section
raw_text
normalized_text
flags
```

O JSONL e opcional e deve ficar em `.work/`. O SQLite nao grava os campos de
texto: guarda somente seus hashes e metadados. Os chunks mantem suas origens em
`chunk_sources`, permitindo que uma versao futura atravesse paginas sem mudar o
modelo relacional.

`xml_type` e preservado mesmo quando `inferred_role` diverge dele. A inferencia
atual e propositalmente conservadora e deve ser auditada na amostra antes do
embedding.

## Auditoria de PG026, paginas 66--74

Comandos de auditoria, sem SQLite e sem Ollama:

```bash
.venv/bin/python tools/generate_ocr_embedding_experiment.py \
  --volumes PG026 \
  --page-start 66 \
  --page-end 74 \
  --work-label "Oratio I Contra Arianos" \
  --stage audit \
  --ir-dir .work/ocr_embedding_ir \
  --verbose
```

Resultado observado em 2026-09-04:

```text
pages=9
xml=9
malformed=0
segments=104
roles={body:24, critical_apparatus:30, header:9, locator:2,
       marginal_anchor:29, scripture_references:10}
chunks={v0:25, v1:18, v2:18}
```

Os numeros nao constituem ground truth editorial. Servem para detectar
mudancas nas heuristicas e escolher trechos para inspecao.

## Preparar o banco sem gerar embeddings

Use exatamente o mesmo recorte e configuracao:

```bash
.venv/bin/python tools/generate_ocr_embedding_experiment.py \
  --volumes PG026 \
  --page-start 66 \
  --page-end 74 \
  --work-label "Oratio I Contra Arianos" \
  --stage prepare \
  --ir-dir .work/ocr_embedding_ir \
  --verbose
```

Esse passo e opcional: `--stage embed` tambem prepara o banco. Ele e util para
auditar tabelas, hashes e locators antes de chamar o backend.

## Gerar os embeddings

O script usa por padrao:

```text
modelo: qwen3-embedding:8b
endpoint: http://localhost:11434/api/embed
batch: 16
```

O documento e enviado como texto puro. Nao ha prefixo `Instruct:` ou `Query:`.

Smoke pequeno:

```bash
.venv/bin/python tools/generate_ocr_embedding_experiment.py \
  --volumes PG026 \
  --page-start 66 \
  --page-end 74 \
  --work-label "Oratio I Contra Arianos" \
  --stage embed \
  --limit-chunks 8
```

Como nem todas as paginas terao todos os componentes, o smoke pode deixar
centroides incompletos. Para concluir o recorte, repita sem `--limit-chunks`:

```bash
.venv/bin/python tools/generate_ocr_embedding_experiment.py \
  --volumes PG026 \
  --page-start 66 \
  --page-end 74 \
  --work-label "Oratio I Contra Arianos" \
  --stage embed
```

O rerun e incremental: um chunk cujo hash de entrada e modelo nao mudaram nao
e enviado novamente. Centroides sao escritos somente quando todos os chunks da
pagina/variante possuem vetor.

## KNN pagina-pagina

KNN bruto:

```bash
.venv/bin/python tools/query_ocr_page_knn.py PG026:72 \
  --variant v2 \
  --top-k 10
```

Lista para descoberta, retirando a continuacao fisica em mais ou menos tres
paginas:

```bash
.venv/bin/python tools/query_ocr_page_knn.py PG026:72 \
  --variant v2 \
  --exclude-nearby 3 \
  --top-k 10
```

Rode o mesmo locator com `--variant v0`, `v1` e `v2` para comparar as
representacoes.

## Benchmark paralelo inicial

```bash
.venv/bin/python tools/benchmark_ocr_parallel_pages.py PG026 \
  --page-start 66 \
  --page-end 74
```

Este primeiro benchmark considera grego e latim na mesma pagina como par. Ele
nao substitui o benchmark por secoes (`G57` contra `L57`), que depende da etapa
seguinte de alinhamento e streams cross-page.

## Vulgata e `ibid.`

A colecao local possui a Vulgata Clementina em dominio publico e loader pronto
em `scripts/scripture_keywords/vulgate_clementine.py`. Foram conferidos os
lookups da pagina PG026:71:

```text
Joao 1:3
Salmo 103:24
Jo 1:2
Genesis 21:5
```

O detector deterministico atual reconhece referencias explicitas, listas,
intervalos e algumas continuacoes do mesmo livro. Uma verificacao direcionada
nas paginas PG026:66, 67, 68, 71 e 72 encontrou 15 ocorrencias textuais de
`ibid.`/`ibidem`, mas nenhuma delas foi incorporada a um `ref_raw` normalizado.
Portanto nao ha suporte explicito a heranca por `ibid.` no detector atual.

A expansao pela Vulgata deve ser implementada como camada separada depois do
primeiro experimento:

```text
printed_reference
canonical_reference
vulgate_text
resolution_status/confidence
```

O `ibid.` precisara herdar o ultimo antecedente valido no mesmo fluxo de
referencias, nunca apenas o ultimo livro visto globalmente na pagina. Ate essa
regra existir e ser testada, referencias nao resolvidas nao devem receber texto
biblico por aproximacao.
