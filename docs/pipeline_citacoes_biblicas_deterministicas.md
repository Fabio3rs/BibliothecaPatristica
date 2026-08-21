# Pipeline determinística de citações bíblicas

Esta etapa cria um índice persistente de ocorrências bíblicas no OCR de PG,
PL e PO. O banco é evidência auxiliar para a pipeline alfabética: o índice
impresso continua sendo a autoridade semântica.

## Construção

```bash
python scripts/build_scripture_citation_db.py --all --workers 8 --pretty
```

Também é possível limitar por coleção ou volume:

```bash
python scripts/build_scripture_citation_db.py --collection PL --workers 8
python scripts/build_scripture_citation_db.py --volume-id PL001 --workers 4
```

O destino padrão é `data/scripture_citations.db`; o relatório de inventário é
gravado em `data/scripture_citations.db.report.json`. A retomada é automática:
arquivos cujo tamanho, `mtime`, versão do detector e perfil do volume não
mudaram são ignorados. `--force` obriga nova leitura.

O relatório também expõe `stored_detector_versions`,
`outdated_detector_file_count` e `outdated_detector_volume_count`. Uma mudança
do detector invalida somente os arquivos em versão anterior; a reconstrução
pode ser retomada normalmente e não requer apagar o banco.

Os workers apenas leem e analisam OCR. Um único processo pai escreve no
SQLite, em lotes, para evitar contenção e manter resultados reproduzíveis.

## Pesquisa

A consulta exige o volume e exatamente um tipo de página:

```bash
python scripts/search_scripture_citations.py \
  --volume-id PL001 --editorial-page 1260 --book "I Cor." --json

python scripts/search_scripture_citations.py \
  --volume-id PO022 --physical-page 217 --text caritate
```

`--physical-page` corresponde ao sufixo numérico de `*-NNN.txt`. A pesquisa
aceita ainda `--fascicle`, `--ref`, `--context` e busca textual FTS5 por
`--text`. Ocorrências ambíguas e páginas-fonte dos índices são excluídas por
padrão; use `--include-ambiguous` ou `--include-index` explicitamente.

## Contrato dos dados

- `citation_files` separa volume, fascículo, sequência física e classificação
  de página.
- `citation_file_pages` guarda zero, uma ou mais hipóteses de página editorial,
  com lado, confiança e evidência.
- `citation_groups` preserva a citação e o snippet OCR brutos, offsets, bloco,
  contexto e regra determinística.
- `citation_occurrences` contém um átomo por referência normalizada. Listas
  como `Gen. 1,4,5,7` produzem três ocorrências ligadas ao mesmo grupo.
- `citation_index_seeds` preserva as referências dos payloads alfabéticos e
  `citation_seed_links` registra sua ligação a ocorrências fora do índice.
- `citation_occurrences_fts` fornece FTS5 sobre livro, referência e snippet.

Quebras alfabéticas como `Corin-\nthios` são unidas apenas na camada lógica de
matching. Intervalos numéricos como `18-\n20` mantêm o hífen. O OCR original
nunca é reescrito.

Numerações históricas dependentes da coleção são resolvidas localmente
(`Regum` em PG/PL e formas observadas nos índices de PO); aliases ambíguos não
recebem uma normalização global inventada.

## Integração alfabética

`alphabetical_compact_driver.py` e a etapa nomeada `locate` de
`alphabetical_analysis_pipeline.py` consultam o banco depois de montar os
locators. Se `citation_volumes.scan_status` for `complete`, as ocorrências
persistidas são usadas como candidatos compactos, excluindo as próprias
páginas do índice. Se o volume estiver ausente ou parcial, a pipeline usa o
localizador determinístico anterior.

A fusão é persistida em `scripture_candidate_stage.json`, com sidecar de
checkpoint validado pelo snapshot do OCR, assinatura do banco, perfis locais
de citação e conteúdo dos locators. Assim, uma falha posterior em `locate`
não obriga a repetir a consulta ou a varredura bíblica. `--force` invalida
essa retomada interna.

O comando nomeado `extract` não faz essa consulta: ele extrai a semântica do
índice impresso. Para cruzar as referências extraídas com ocorrências no
corpo do volume, execute `locate` em seguida ou use `run`, que percorre
`discover`, `extract`, `locate`, `verify` e `assemble`.
