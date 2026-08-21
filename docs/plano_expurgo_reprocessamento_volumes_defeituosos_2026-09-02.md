# Plano de expurgo e reprocessamento dos volumes defeituosos

Data da inspeção: 2 de setembro de 2026.

**Estado em 4 de setembro de 2026:** o problema de fonte incorreta está
resolvido na camada OCR para os cinco volumes. A invalidação e reconstrução de
todos os derivados ainda não está concluída; veja a atualização operacional ao
fim deste documento.

Este documento separa o que precisa ser invalidado antes de executar novamente
o OCR do que só deve ser reconstruído depois dos resumos, keywords, citações e
índices. Nenhum expurgo descrito abaixo havia sido executado na data inicial
desta inspeção. O estado posterior à troca das imagens está registrado na
atualização operacional ao fim do documento.

## Escopo fechado

Volumes que devem voltar ao início da pipeline:

- `PG024`;
- `PG031`;
- `PG084`;
- `PG116`;
- `PL124`.

Os PDFs substitutos já estão em `/homessddata/patristica/` e tiveram série e
tomo conferidos nas páginas iniciais, por OCR e fac-símile.

`PL020` está explicitamente fora deste expurgo. Seu problema é duplicação sem
omissão: a segunda metade contém material de `PL021`. O volume deve ser mantido
até a comparação de qualidade entre os dois testemunhos.

## Conclusão operacional

Há quatro camadas distintas:

1. apagar ou pôr em quarentena a pasta OCR antiga e limpar os caches/histórico
   do `main2.py`;
2. limpar por volume resumos, embeddings, keywords e estimativas;
3. limpar por volume citações e índices editoriais/alfabéticos;
4. reconstruir shards, manifestos, dicionários e os índices compartilhados do
   site.

Os bancos multi-volume não devem ser apagados. Neles, a operação correta é
`DELETE ... WHERE ... IN (...)`, dentro de uma transação. As tabelas de catálogo
global, modelos, traduções e vocabulários compartilhados devem permanecer.

## Correção sobre Pagefind

O diretório `web/public/pagefind/` ainda existe localmente e ocupa cerca de
1,5 GiB em blocos de disco, mas é um artefato antigo, gerado em 12 de junho de
2026 e não versionado pelo Git. O runtime e o workflow atuais usam
`web/public/indexador/search/` e `web/public/indexador/indices/`.

Portanto:

- Pagefind não integra a reconstrução atual;
- `web/public/pagefind/` pode ser removido separadamente como lixo de build
  local, depois de uma confirmação explícita;
- `web/public/indices-pagefind/` está vazio;
- os nomes históricos `indexador-pagefind.js` e IDs HTML com `pagefind` não
  indicam uso do motor Pagefind; o módulo executa o PatrologiaIndexer.

O `web/dist/` local pode conter uma cópia desse Pagefind antigo porque o Astro
copia tudo que encontra em `public/`. No CI, o diretório não existe após o
checkout, pois não é versionado.

## Estado do OCR que deve ser substituído

| Volume | Imagens | Textos | Pasta + log (bytes) |
|---|---:|---:|---:|
| `PG024` | 509 | 509 | 269.004.612 |
| `PG031` | 943 | 943 | 476.658.144 |
| `PG084` | 656 | 656 | 375.629.767 |
| `PG116` | 732 | 732 | 365.057.553 |
| `PL124` | 507 | 507 | 293.857.305 |

Antes do novo `main2.py`, pôr em quarentena ou remover somente:

```text
teste/PG024/
teste/PG024.pdf.log
teste/PG031/
teste/PG031.pdf.log
teste/PG084/
teste/PG084.pdf.log
teste/PG116/
teste/PG116.pdf.log
teste/PL124/
teste/PL124.pdf.log
```

Isso é obrigatório porque `main2.py` reaproveita imagens e textos já existentes
e pode pular a rasterização/OCR. A quarentena em outro filesystem ou diretório
fora de `teste/` é preferível à remoção imediata.

Não mover, renomear ou apagar:

```text
/homessddata/patristica/PG024.pdf
/homessddata/patristica/PG031.pdf
/homessddata/patristica/PG084.pdf
/homessddata/patristica/PG116.pdf
/homessddata/patristica/PL124.pdf
```

## Bancos diretamente ligados ao `main2.py`

| Banco | Tabela/filtro | Linhas atuais | Ação |
|---|---|---:|---|
| `data/ocr_eval.db` | `evaluations.volume_id` | 507 | apagar os cinco volumes; hoje só `PL124` tem linhas |
| `data/ocr_versions.db` | `ocr_results.volume_id` | 14.230 | apagar todo o histórico dos cinco volumes |
| `data/tesseract.db` | `tesseract_cache.imgpath` | 7.275 | apagar entradas cujo caminho aponta às imagens antigas |

Linhas em `ocr_versions.db`:

| Volume | Linhas |
|---|---:|
| `PG024` | 2.048 |
| `PG031` | 4.396 |
| `PG084` | 2.816 |
| `PG116` | 2.907 |
| `PL124` | 2.063 |

O histórico antigo precisa sair porque `main2.py` conta reprocessamentos já
registrados. Mantê-lo poderia influenciar os limites de tentativas aplicados ao
PDF novo.

No cache Tesseract, o filtro deve usar `imgpath`, não apenas hash:

```sql
DELETE FROM tesseract_cache
 WHERE imgpath LIKE '%/teste/PG024/images/%' OR imgpath LIKE 'teste/PG024/images/%'
    OR imgpath LIKE '%/teste/PG031/images/%' OR imgpath LIKE 'teste/PG031/images/%'
    OR imgpath LIKE '%/teste/PG084/images/%' OR imgpath LIKE 'teste/PG084/images/%'
    OR imgpath LIKE '%/teste/PG116/images/%' OR imgpath LIKE 'teste/PG116/images/%'
    OR imgpath LIKE '%/teste/PL124/images/%' OR imgpath LIKE 'teste/PL124/images/%';
```

Preservar `ocr_engine_versions`, pois descreve engines/modelos compartilhados.
Não apagar os próprios arquivos `.db`, `-wal` ou `-shm`.

## Resumos, embeddings e keywords

### `data/patristica_resumos.db`

Excluir por `documento`:

| Tabela | Total nos cinco volumes |
|---|---:|
| `resumos` | 3.347 |
| `resumo_runs` | 2 |
| `resumo_generations` | 718 |
| `resumo_review_queue` | 161 |
| `resumo_context_anchors` | 698 |
| `resumo_pagina_embedding` | 3.347 |
| `resumo_pagina_embedding_reduced` | 3.347 |
| `resumo_pagina_clusters` | 3.347 |
| `resumo_global_embedding` | 3.347 |
| `resumo_global_embedding_reduced` | 3.347 |
| `resumo_global_clusters` | 3.347 |

Não há hoje traduções nem associações de geração a clusters para esses cinco
volumes. Mesmo assim, a rotina de expurgo deve contemplar as tabelas filhas para
continuar correta no momento em que for executada.

Ordem segura:

1. apagar `resumo_translations` ligadas às gerações-alvo;
2. apagar `resumo_generation_clusters` e `resumo_review_queue`;
3. anular `previous_generation_id` nas gerações-alvo;
4. apagar `resumo_generations` e `resumo_runs`;
5. apagar `resumos`, anchors, embeddings reduzidos, embeddings e clusters pelo
   `documento`.

Preservar `resumo_cluster_runs` e demais metadados globais. Depois de gerar os
novos embeddings, refazer a clusterização global, pois a posição dos cinco
volumes no espaço semântico mudou.

Também existem dois locks vazios:

```text
data/.resumo_locks/patristica_resumos.db.PG024.lock
data/.resumo_locks/patristica_resumos.db.PG031.lock
```

Eles só devem ser removidos depois de confirmar novamente que não há
`resumo_serial.py` ativo. Na inspeção não havia escritor ativo.

### `data/patristica_keywords.db`

Excluir apenas `keyword_occurrence WHERE documento IN (...)`:

| Volume | Ocorrências |
|---|---:|
| `PG024` | 15.115 |
| `PG031` | 25.645 |
| `PG084` | 19.504 |
| `PG116` | 16.756 |
| `PL124` | 16.081 |
| **Total** | **93.101** |

Preservar as tabelas globais `keywords`, `keyword_alias`, `keyword_category` e
`keyword_embedding`. Uma keyword pode ser compartilhada por muitos volumes.
Orphans podem ser auditados depois, mas não precisam ser apagados para garantir
a correção do reprocessamento.

Preservar também:

- `data/patristica_normalizations.db`, cache global de normalização;
- `data/cache/keyword_cache.db`, cache por conteúdo;
- IDs canônicos já publicados.

## Estimativas de página editorial

Em `data/editorial_page_estimator.db`, apagar as 3.347 linhas de
`file_observation_cache WHERE volume_id IN (...)`.

Não há `page_overrides` para os cinco volumes. Se aparecer algum antes da
execução, ele deve ser revisado manualmente, não removido às cegas.

`data/editorial_page_estimates.db` não contém linhas dos alvos e deve permanecer.

## Índices editoriais e alfabéticos

### `data/patristic_indices.db`

O expurgo seletivo atingirá:

| Objeto | `PG024` | `PG031` | `PG084` | `PG116` | `PL124` | Total |
|---|---:|---:|---:|---:|---:|---:|
| volumes | 1 | 1 | 1 | 1 | 1 | 5 |
| runs | 1 | 1 | 1 | 1 | 1 | 5 |
| works | 4 | 27 | 1 | 19 | 2 | 53 |
| sections | 3 | 4 | 4 | 13 | 2 | 26 |
| entries | 6 | 199 | 16 | 663 | 18 | 902 |

Apagar primeiro `runs WHERE volume_id IN (...)` e depois
`volumes WHERE volume_id IN (...)`. As FKs removem works, sections e entries em
cascata.

Preservar `index_strings`, traduções, análises e tokens globais. Preservar ainda
`data/patristic_indices.rebuilt_20260712.db` e todos os backups históricos.

### `data/alphabetical_indices.db`

Só `PG116` e `PL124` estão importados no banco ativo:

| Objeto | `PG116` | `PL124` | Total |
|---|---:|---:|---:|
| volumes/runs/sections/quality | 1 cada | 1 cada | 2 cada |
| entries | 4 | 93 | 97 |
| refs | 4 | 93 | 97 |
| nodes | 2 | 2 | 4 |

Apagar as raízes em `alphabetical_volumes WHERE volume_id IN (...)`; as FKs
removem os descendentes. Preservar `alphabetical_translations`.

### `data/alphabetical_analysis.db`

| Objeto | Total nos cinco volumes |
|---|---:|
| `analysis_volumes` | 5 |
| `analysis_volume_stages` | 25 |
| `analysis_stage_runs` | 5 |
| `analysis_stage_artifacts` | 5 |
| `analysis_discovered_pages` | 2.032 |
| `analysis_discovered_segments` | 313 |

Ordem segura:

1. apagar `analysis_volume_stages` dos alvos, liberando `last_run_id`;
2. apagar `analysis_stage_runs` dos alvos, com cascade dos artifacts;
3. apagar `analysis_volumes` dos alvos, com cascade de páginas, segmentos,
   sections, entries, occurrences e referências.

As tabelas FTS são mantidas sincronizadas pelos triggers do banco; não devem ser
editadas diretamente.

## Citações bíblicas

### `data/scripture_citations.db`

| Objeto | `PG024` | `PG031` | `PG084` | `PG116` | `PL124` | Total |
|---|---:|---:|---:|---:|---:|---:|
| files | 509 | 943 | 656 | 732 | 507 | 3.347 |
| file pages | 1.743 | 2.903 | 2.270 | 2.357 | 1.609 | 10.882 |
| groups | 3.928 | 3.354 | 800 | 531 | 141 | 8.754 |
| occurrences | 4.244 | 3.790 | 905 | 550 | 142 | 9.631 |
| book aliases | 0 | 0 | 46 | 0 | 0 | 46 |
| format profiles | 0 | 0 | 2 | 0 | 0 | 2 |
| index seeds | 0 | 0 | 3.527 | 0 | 0 | 3.527 |

Apagar primeiro `citation_seed_links` ligados aos seeds dos alvos; depois
`citation_index_seeds`, `citation_book_aliases` e `citation_format_profiles` pelo
`volume_id`; por fim apagar `citation_volumes`. O último delete remove files,
file pages, groups e occurrences em cascata. Preservar `citation_runs` e os
catálogos bíblicos globais.

O relatório global `data/scripture_citations.db.report.json` contém dados de
`PG084` e deve ser regenerado depois da nova varredura, não apagado antes do OCR.

### `data/summary_scripture_citations_v3.db`

O banco atual contém 1.913 páginas, 9.969 menções e 244 rejeições pertencentes
aos cinco volumes. Apesar de ser possível apagá-las com `DELETE WHERE`, o
construtor `build_summary_scripture_db_v3.py` exige que o arquivo de saída ainda
não exista e cria um banco novo.

Por isso este banco é uma exceção: depois de atualizar resumos e
`scripture_citations.db`, gerar uma cópia completa nova em `/tmp`, validá-la e
só então substituir atomicamente o banco ativo. Não apagar as referências
bíblicas compartilhadas no banco atual para tentar fazer um rebuild parcial.

Depois, regenerar integralmente `web/public/scripture/v3/` com
`export_summary_scripture_book_shards_v3.py`.

## Artefatos de arquivos que ficam inválidos

Os seguintes arquivos são produtos do OCR antigo. Devem ser postos em
quarentena/removidos antes de suas pipelines respectivas, usando apenas nomes
dos cinco volumes:

```text
data/alphabetical_index_payloads/<VOL>_*
data/alphabetical_prefiltered/<VOL>_*
data/index_payloads/<VOL>_*
data/index_payload_audits/<VOL>_*
data/intermediate_payloads/<VOL>/
data/index_intermediate_payloads/<VOL>/
data/index_logs/<VOL>/
data/index_logs/<VOL>_codex_*.log
data/alphabetical_index_logs/<VOL>/
data/alphabetical_index_logs/<VOL>_codex_*.log
```

`<VOL>` significa somente `PG024`, `PG031`, `PG084`, `PG116` ou `PL124`.

O manifesto manual
`data/index_repairs/manual_work_anchor_repairs_2026-08-23.json` contém 15
reparos de `PG031`, inclusive caminhos e evidência visual da digitalização
errada. Não reutilizar esses reparos no tomo novo. Como o arquivo também contém
105 reparos de outros volumes, não apagá-lo inteiro: remover ou marcar como
obsoletas somente as 15 entradas de `PG031` quando a nova extração de índices
começar.

`data/index_payload_audits/summary.json` é um resumo global que inclui os cinco
volumes e deve ser refeito depois que os payloads novos existirem.

## Shards e publicação web

### Arquivos seletivos

Remover/regenerar os shards dos cinco volumes:

```text
data/shards/enrichment/<VOL>.ndjson
data/shards/enrichment/<VOL>-part*.ndjson
web/public/meta/<VOL>.json.gz
web/public/meta/<VOL>-pages-*.json.gz
web/public/snapshots/<VOL>.json.gz
web/public/indices/<VOL>.json.gz
```

Atualmente são:

- 24 arquivos de enrichment;
- 75 arquivos em `web/public/meta/`;
- 5 snapshots;
- 5 exportações de índices.

### Manifestos globais

Não produzir manifestos globais a partir de apenas cinco volumes, pois alguns
exportadores substituem o arquivo inteiro. Depois que os cinco estiverem
prontos, executar a exportação com `--all` para preservar os 407 volumes:

```text
data/shards/enrichment/index.json
web/public/volumes.json
web/public/indices/manifest.json
web/public/dict/
```

O dicionário de keywords é global e precisa ser reexportado porque as contagens
mudam quando as 93.101 ocorrências antigas são substituídas.

### Índices de busca atuais

Reconstruir integralmente, não editar shards binários à mão:

```text
web/public/indexador/search/
web/public/indexador/indices/
```

Preservar:

```text
web/public/indexador/runtime/
web/public/indexador/indices-current-test/
```

O workflow `.github/workflows/pages.yml` já reconstrói search e indices em
diretórios versionados pelo SHA do commit antes do `astro build`.

`web/dist/` é inteiramente derivado e deve ser recriado ao final. Não é
necessário fazer expurgo seletivo dentro dele.

## Arquivos que devem permanecer como evidência

Não apagar:

- `data/volume_similarity_audit/`;
- `data/page_group_audit_pl020/`;
- `data/page_group_audit_duplicates/`;
- `data/page_group_audit_misnamed/`;
- `data/page_group_audit_neighbors/`;
- `data/backups/`;
- `data/rerun_manifests/` e `data/rerun_reports/` históricos;
- `data/index_audit*.json` e `data/bad_index_volumes_20260712.txt` históricos;
- scripts manuais em `scripts/pipeline_index_extraction/`;
- os PDFs novos e todos os volumes fora do conjunto fechado.

Os scripts manuais específicos dos tomos defeituosos podem servir como memória
do processo, mas não devem ser executados automaticamente contra o novo OCR sem
revisão.

## Bancos inspecionados sem linhas dos alvos

Não exigem limpeza:

- `data/patristica_text.db`;
- `data/patristica_catalog.db`;
- `data/patristica_rlm_results.db`;
- `data/indices.db`;
- `data/editorial_page_estimates.db`.

Também não se deve alterar bancos `*_test.db`, snapshots `*.rebuilt_*.db` nem
backups.

## Sequência segura proposta

1. Confirmar novamente ausência de processos OCR/resumo/keywords/índices e de
   handles abertos nos bancos.
2. Fazer backup seletivo das linhas que serão removidas e registrar contagens e
   hashes; não é necessário duplicar todos os bancos gigantes.
3. Executar os deletes SQL em transações `BEGIN IMMEDIATE`/`COMMIT`, com
   `PRAGMA foreign_keys=ON`; em erro, `ROLLBACK`.
4. Mover as cinco pastas e logs de `teste/` para uma quarentena recuperável.
5. Executar `main2.py` com `.venv/bin/python` sobre os cinco PDFs novos.
6. Conferir folha de rosto, número de imagens/textos, páginas inicial/final,
   lacunas e hashes; fazer nova comparação contra vizinhos.
7. Recriar resumos, embeddings, clusters e keywords somente após aprovar o OCR.
8. Recriar estimativas, citações e índices, com revisão manual dos itens mais
   suspeitos no fac-símile.
9. Reexportar todos os manifestos globais, dicionários e shards públicos.
10. Reconstruir PatrologiaIndexer, `web/dist/`, rodar testes e validar que os
    cinco volumes antigos não aparecem mais nos derivados.

## Validação antes e depois

Antes do expurgo, salvar as contagens desta auditoria. Depois de cada fase:

- as consultas dos alvos devem retornar zero antes da reconstrução;
- depois do OCR, imagens e textos devem corresponder ao total do PDF novo;
- os `ocr_results` correntes devem apontar apenas às imagens novas;
- resumos/keywords/citações devem cobrir as páginas novas, não os totais antigos;
- manifests globais devem continuar contendo 407 volumes;
- `PG023`/`PG025`, `PG030`/`PG032`, `PG083`/`PG085`, `PG115`/`PG117` e
  `PL123`/`PL125` devem permanecer intocados;
- `PL020` e `PL021` devem permanecer intocados.

Toda automação destrutiva deve começar em modo dry-run, imprimir alvos e
contagens e exigir uma opção explícita de aplicação.

## Scripts de limpeza preparados

Os scripts abaixo foram criados para que o SQL e a seleção dos arquivos possam
ser revisados antes da aplicação:

```text
data/volume_similarity_audit/defective_volume_cleanup_manifest.json
scripts/cleanup_defective_volume_databases.py
scripts/quarantine_defective_volume_files.py
test_cleanup_defective_volumes.py
```

O manifesto fixa o conjunto autorizado, as contagens antigas e os totais de
páginas antigos/novos. Os scripts recusam um manifesto que não contenha
exatamente `PG024,PG031,PG084,PG116,PL124`.

### Auditoria dos bancos, sem escrita

```bash
.venv/bin/python scripts/cleanup_defective_volume_databases.py \
  --report /tmp/pdfocr-db-cleanup-dry-run.json
```

O comando abre os bancos em modo somente leitura. Para auditar apenas um banco:

```bash
.venv/bin/python scripts/cleanup_defective_volume_databases.py \
  --database ocr_versions.db \
  --report /tmp/pdfocr-ocr-versions-cleanup-dry-run.json
```

### Aplicação futura nos bancos

Não executar antes da revisão do relatório dry-run. O formato deliberadamente
verboso é:

```bash
.venv/bin/python scripts/cleanup_defective_volume_databases.py \
  --apply \
  --confirm PG024,PG031,PG084,PG116,PL124 \
  --report data/volume_similarity_audit/cleanup_reports/database-apply.json
```

Cada banco recebe sua própria transação `BEGIN IMMEDIATE`. A rotina verifica
que todas as linhas selecionadas chegaram a zero e executa
`PRAGMA foreign_key_check` antes do commit. Em erro, o banco corrente recebe
rollback. O processo é idempotente para bancos já limpos.

Contagens diferentes tanto do estado antigo registrado quanto de zero impedem
`--apply`. Existe `--allow-count-drift`, mas ele só deve ser usado depois de
explicar e registrar a divergência. O script não executa `VACUUM`, não apaga
arquivos `.db` e não toca em `-wal`/`-shm`.

`summary_scripture_citations_v3.db` sempre aparece como
`rebuild_required`: nenhuma linha é removida dele por esse script.

### Auditoria dos arquivos, sem movimentação

```bash
.venv/bin/python scripts/quarantine_defective_volume_files.py \
  --report /tmp/pdfocr-file-cleanup-dry-run.json
```

Por padrão são auditadas as fases `ocr`, `derived` e `publication`. Pode-se
examinar uma fase isoladamente repetindo `--phase` quando necessário:

```bash
.venv/bin/python scripts/quarantine_defective_volume_files.py \
  --phase ocr \
  --report /tmp/pdfocr-file-cleanup-ocr-dry-run.json
```

### Aplicação futura por quarentena

Não há `unlink`/`rm`: os caminhos são movidos preservando sua posição relativa
dentro de uma quarentena. Exemplo para as três fases operacionais:

```bash
.venv/bin/python scripts/quarantine_defective_volume_files.py \
  --apply \
  --confirm PG024,PG031,PG084,PG116,PL124 \
  --quarantine-dir /homessddata/patristica/quarantine/wrong-source-20260902 \
  --report data/volume_similarity_audit/cleanup_reports/files-apply.json
```

O script valida que cada pasta OCR ainda tem a contagem antiga de imagens e
textos. Se encontrar os totais dos PDFs novos ou qualquer estado intermediário,
aborta para não retirar um reprocessamento já iniciado.

O manifesto compartilhado de reparos manuais de work anchors não é movido. O
relatório aponta separadamente as 15 entradas antigas de `PG031`, que devem ser
preservadas como histórico e não reutilizadas no OCR novo.

### Pagefind legado

Pagefind não participa das fases padrão. Sua auditoria é explicitamente
separada:

```bash
.venv/bin/python scripts/quarantine_defective_volume_files.py \
  --phase legacy-pagefind \
  --report /tmp/pdfocr-pagefind-cleanup-dry-run.json
```

Se posteriormente for decidido recuperar o espaço local, a mesma fase pode ser
aplicada com `--apply`, confirmação, relatório e uma quarentena. Ela não deve ser
misturada conceitualmente com o expurgo dos cinco volumes.

## Validação dos scripts

Foi executado:

```bash
.venv/bin/python -m pytest -q test_cleanup_defective_volumes.py
```

Resultado: 13 testes passaram. Eles cobrem:

- preservação de `PL020` e de outros volumes não selecionados;
- filtro do Tesseract por caminhos absolutos e relativos;
- cadeias e tabelas filhas dos resumos;
- cascatas dos índices gerais e alfabéticos;
- ordem especial de stages da análise alfabética;
- seeds, links e cascatas das citações;
- trava de contagens parciais ou inesperadas;
- seleção de arquivos sem capturar vizinhos;
- Pagefind fora das fases padrão;
- movimentação recuperável mantendo o caminho relativo.

Os dois dry-runs também foram executados contra o estado real. Não houve escrita
nos bancos nem movimentação de arquivos. O dry-run dos bancos não encontrou
drift depois de incorporar as 15 entradas Tesseract com caminho relativo que a
primeira consulta manual não havia contado.

## Atualização operacional: imagens novas e runner LLM

Data da conferência: 3 de setembro de 2026.

As imagens substitutas já estão instaladas em `teste/<VOLUME>/images/`. A
sequência foi conferida sem lacunas, do arquivo `001` até a última página, e
coincide com o total de páginas de cada PDF atual:

| Volume | Imagens/PDF | Folha de rosto conferida | Identificação visível |
|---|---:|---:|---|
| `PG024` | 648 | 7 | `PATROLOGIAE GRAECAE TOMUS XXIV` |
| `PG031` | 942 | 1 | `PATROLOGIAE GRAECAE TOMUS XXXI` |
| `PG084` | 658 | 7 | `PATROLOGIAE GRAECAE TOMUS LXXXIV` |
| `PG116` | 747 | 1 | `PATROLOGIAE GRAECAE TOMUS CXVI` |
| `PL124` | 667 | 1 | `PATROLOGIAE TOMUS CXXIV` |

Além da leitura visual do fac-símile, cada uma dessas páginas foi renderizada
novamente do PDF corrente com `pdftocairo` a 300 DPI e comparada com o PNG já
instalado. As cinco amostras foram idênticas pixel a pixel. No momento da
conferência havia zero `.txt` nos cinco diretórios, totalizando 3.662 imagens
prontas para o primeiro passe.

O runner auditável é `patristica_defeituosos_llm.sh`. Ele contém uma lista fixa
dos cinco volumes, usa `.venv/bin/python`, valida o total exato dos PDFs e dos
PNGs e reproduz o paralelismo dos runners PG/PL (`JOBS=11`, `PROCS=1`,
`OMP_THREADS=2`). Os logs do primeiro e do segundo passe têm nomes distintos.

O primeiro passe não envia `--verify`, `--verify-fix`,
`--verify-judge-llm` nem `--tesseract-cache-only` ao `main2.py`. Nesse caminho,
`ocr_images_to_text_parallel()` chama `_ocr_one()` com
`reprocess_reason=None`, que segue diretamente para
`llm_process_image_autoretry()`. Portanto não há OCR Tesseract preliminar:

```bash
./patristica_defeituosos_llm.sh --dry-run --first-pass
./patristica_defeituosos_llm.sh --first-pass
```

O script cria em cada pasta um marcador `.llm_first_pass_source`, vinculado ao
caminho, tamanho e número de páginas do PDF. Isso permite retomar um primeiro
passe parcial, mas impede misturar textos de outra fonte. O segundo passe só é
aceito quando os 3.662 textos e os cinco marcadores estiverem presentes:

```bash
./patristica_defeituosos_llm.sh --dry-run --verify-fix
./patristica_defeituosos_llm.sh --verify-fix
```

Nesse segundo modo, e somente nele, o runner envia `--verify-fix --lang migne`.
A língua pode ser substituída explicitamente, por exemplo:

```bash
TESSERACT_LANG=lat+grc ./patristica_defeituosos_llm.sh --verify-fix
```

Uma tentativa anterior de aquecimento deixou 866 resultados parciais com
`reprocess_reason=tesseract_cache_warm`: 217 de `PG024`, 197 de `PG031`, 198 de
`PG084`, 183 de `PG116` e 71 de `PL124`. Eles não são consultados pelo caminho
normal do primeiro passe LLM e também estão excluídos da contagem de tentativas.
Podem permanecer para serem reutilizados posteriormente pelo `verify-fix`; se
for desejada proveniência Tesseract inteiramente nova, a limpeza seletiva deve
ser auditada e feita antes do primeiro passe. O expurgo amplo por volume não
deve ser executado depois do primeiro passe, pois também removeria as novas
versões LLM.

Foram executados `bash -n`, o dry-run do primeiro passe e o dry-run do
`verify-fix`. O primeiro aceitou o estado atual. O segundo recusou corretamente
o estado ainda sem textos e sem marcadores, sem iniciar OCR.

## Atualização operacional: resolução na camada OCR

Data da conferência: 4 de setembro de 2026.

O estado descrito na seção anterior — imagens instaladas, mas ainda sem textos —
foi superado. O primeiro passe LLM e várias rodadas focalizadas de
`--verify-fix` foram executados. Os cinco logs correntes terminam em
`Verificação concluída`, e os joblogs mais recentes do runner registram código
de saída zero para todos os volumes. Esse término indica que a varredura e o
reprocessamento das páginas selecionadas foram concluídos; não significa que
cada algarismo editorial isolado tenha CER zero.

### Resultado material

| Volume | Imagens | Textos XML | Intervalo físico |
|---|---:|---:|---|
| `PG024` | 648 | 648 | `001–648` |
| `PG031` | 942 | 942 | `001–942` |
| `PG084` | 658 | 658 | `001–658` |
| `PG116` | 747 | 747 | `001–747` |
| `PL124` | 667 | 667 | `001–667` |
| **Total** | **3.662** | **3.662** | — |

A auditoria focal dos textos correntes encontrou:

- zero arquivos vazios;
- zero páginas rejeitadas por `main2.is_page_xml()`;
- zero vazamentos reconhecíveis de prompt ou resposta de API;
- zero entidades escapadas `&amp;#160;`, `&#160;`, `&amp;nbsp;`, `&nbsp;` ou
  variantes hexadecimais;
- zero ocorrências de NBSP Unicode e zero sequências de espaçamento horizontal
  com cem ou mais caracteres;
- correspondência entre o número de PNGs, textos e páginas dos PDFs atuais.

As folhas de rosto de `PG024`, `PG031`, `PG084`, `PG116` e `PL124` continuaram
correspondendo aos tomos XXIV, XXXI, LXXXIV, CXVI e CXXIV. As amostras instaladas
foram comparadas com novas renderizações a 300 DPI dos PDFs e permaneceram
idênticas pixel a pixel.

### Limpeza de NBSP na origem

Foi acrescentada ao caminho comum de limpeza do `main2.py` uma normalização de
execuções arbitrariamente longas de:

- `&amp;#160;` e `&#160;`;
- `&amp;nbsp;` e `&nbsp;`;
- entidades hexadecimais de NBSP;
- o caractere Unicode U+00A0.

Cada sequência é substituída por um único espaço. O teste de regressão cobre
dez mil repetições de cada forma e misturas entre elas. A suíte focal de
`main2.py` terminou com 24 testes aprovados, além da compilação com
`py_compile` e `git diff --check`.

### Conferência visual e numeração editorial

As páginas anteriormente mais suspeitas de `PL124` foram confrontadas
novamente com o fac-símile. `PL124-498` passou a registrar corretamente as
colunas editoriais 995–996 e deixou de conter o preenchimento excessivo. Em
`PL124-661`, a leitura isolada dos algarismos degradados era ambígua; a sequência
das páginas vizinhas demonstrou que o par correto é 1321–1322:

```text
PL124-659 = 1317–1318
PL124-660 = 1319–1320
PL124-661 = 1321–1322
PL124-662 = 1323–1324
PL124-663 = 1325–1326
```

O arquivo `PL124-661.txt` foi corrigido manualmente. Erros isolados de OCR no
número editorial não invalidam por si só uma página: o identificador primário
continua sendo a página física (`PL124-661`), e os estimadores de página
editorial usam sequência, vizinhança e outras evidências. Permanecem graves os
casos de fonte ou tomo incorreto, omissão de texto, duplicação com perda,
colunas ausentes ou conteúdo pertencente a outra página.

### Estado dos resumos e keywords

Uma auditoria somente leitura confirmou zero linhas dos cinco volumes na tabela
legada `patristica_resumos.db.resumos` e zero ocorrências em
`patristica_keywords.db.keyword_occurrence`.

Os resumos 2.0 ficam no mesmo `patristica_resumos.db`, nas tabelas
`resumo_runs`, `resumo_generations`, `resumo_review_queue`,
`resumo_context_anchors` e tabelas filhas. Uma execução anterior de
`resumo_parallel.sh`, iniciada enquanto o OCR ainda recebia novos passes,
deixou em shadow o seguinte estado antes do expurgo:

| Objeto v2 | `PG024` | `PG031` | Total |
|---|---:|---:|---:|
| `resumo_generations` | 275 | 397 | 672 |
| `resumo_review_queue` | 161 | 79 | 240 |
| `resumo_runs` com estado `running` | 1 | 1 | 2 |

Todas as 672 gerações estavam com `is_current=0` e, portanto, não haviam sido
publicadas. Dezenove já apontavam para versões OCR superadas por reruns
posteriores.

Foi criado um expurgo dedicado que preserva a tabela legada `resumos` e não
toca no banco de keywords:

```text
scripts/cleanup_defective_resumos_v2.py
scripts/cleanup_defective_resumos_v2.sh
test_cleanup_defective_resumos_v2.py
```

O wrapper usa `.venv/bin/python`, é dry-run por padrão, adquire os mesmos locks
por volume usados por `resumo_serial.py`, exige confirmação explícita para
aplicar, executa uma única transação e verifica tanto resíduos quanto chaves
estrangeiras antes do commit. A auditoria atual pode ser repetida com:

```bash
scripts/cleanup_defective_resumos_v2.sh
```

O expurgo foi aplicado em 4 de setembro de 2026, das 11:29:20 às 11:29:49 no
horário local, com o comando auditável:

```bash
scripts/cleanup_defective_resumos_v2.sh \
  --apply \
  --confirm PG024,PG031,PG084,PG116,PL124 \
  --report data/cleanup_defective_resumos_v2_report.json
```

O relatório `data/cleanup_defective_resumos_v2_report.json` registra a remoção
das 672 gerações, 240 itens de revisão, dois runs e 670 elos de cadeia. A
verificação executada dentro da mesma transação encontrou zero linhas restantes
em todas as tabelas v2 selecionadas, zero relações cruzadas entre documentos e
zero violações de chave estrangeira. A tabela legada `resumos` permaneceu sem
linhas dos cinco volumes.

Às 11:30:13, imediatamente após a limpeza, `resumo_parallel.sh` iniciou novos
runs para `PG024` e `PG031` a partir da página física 1. Durante a conferência,
as contagens voltaram a crescer e os logs avançaram página a página. Esses
novos registros são a reconstrução esperada sobre o OCR final, não resíduos do
estado removido. A execução continua em shadow, sem `--promote`; portanto, as
novas gerações permanecem `is_current=0` até uma promoção explícita de segmentos
completos e seguros.

Os testes do novo expurgo, executados junto com a suíte anterior de limpeza,
totalizaram 15 testes aprovados.

### Estado dos JSONL e bancos de índices

Os artefatos intermediários e montados dos índices de obras e índices
alfabéticos também foram conferidos após a limpeza. A auditoria da fase
`derived` de `scripts/quarantine_defective_volume_files.py` retornou zero
candidatos ativos para os cinco volumes.

Não foram encontrados arquivos, diretórios ou JSONL pertencentes a `PG024`,
`PG031`, `PG084`, `PG116` ou `PL124` nos caminhos:

```text
data/alphabetical_index_payloads/
data/alphabetical_prefiltered/
data/intermediate_payloads/
data/index_intermediate_payloads/
data/index_payloads/
data/index_payload_audits/
data/index_logs/
data/alphabetical_index_logs/
```

A busca foi feita tanto por nomes/prefixos quanto pelo conteúdo dos JSONL
remanescentes, cobrindo casos em que o arquivo pudesse ter apenas nome de chunk
ou fragmento. Alguns logs de outros volumes ainda mencionam os identificadores
em prompts ou inventários globais; isso não constitui material de índice
pertencente aos cinco volumes.

Os bancos pós-montagem e de análise também foram abertos em modo somente
leitura e retornaram zero em todas as tabelas relacionadas aos alvos:

| Banco | Raízes e descendentes conferidos | Resultado |
|---|---|---:|
| `data/patristic_indices.db` | volumes, runs, works, sections e entries | 0 |
| `data/alphabetical_indices.db` | volumes, runs, sections, spans, boundaries, nodes, entries, refs, scripture refs e quality | 0 |
| `data/alphabetical_analysis.db` | volumes, stages, runs, artifacts, páginas/segmentos descobertos, sections, entries, occurrences e scripture refs | 0 |

O arquivo compartilhado
`data/index_repairs/manual_work_anchor_repairs_2026-08-23.json` foi
deliberadamente preservado como histórico. Suas 15 entradas antigas de
`PG031` não devem ser reutilizadas contra o OCR substituto sem nova validação.

### Situação final desta etapa

O defeito original — cinco pastas alimentadas por tomos incorretos — está
resolvido no nível dos PDFs, imagens e OCR corrente. `PL020` e `PL021`
permaneceram fora do expurgo, conforme a decisão de tratar separadamente a
duplicação sem omissão.

A resolução integral da pipeline permanece condicionada a:

1. concluir e promover os novos resumos v2 produzidos a partir do OCR final;
2. regenerar keywords, embeddings, clusters, citações, índices e artefatos de
   publicação dos cinco volumes;
3. executar as validações finais dos derivados e do site.
