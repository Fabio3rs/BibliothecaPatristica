# Pipeline Técnico — Patrística Digital

*English abstract*: End-to-end pipeline that ingests PDFs, renders pages, runs OCR (Tesseract or LLM-based with deterministic gating), writes per-page summaries/keywords in PT-BR only, generates embeddings/clusters, and builds a custom Pagefind index (sharded) because the raw corpus exceeds GitHub Pages limits. Prompts and outputs are PT-BR only; English text here is reference. Manual review is limited due to volume, so treat all AI outputs as draft and validate against sources.

## Visão geral
- **Objetivo**: manter um fluxo reprodutível de ingestão → OCR → validação → resumos/keywords → embeddings/clustering → busca web estática.
- **Público**: operadores técnicos do projeto e colaboradores que rodem jobs localmente ou em CI.
- **Escopo**: scripts CLI (Python/Node/Bash) e artefatos em disco. Fora de escopo: detalhes de UI Astro além do build.

## Pré-requisitos rápidos
- Python 3.10+, Node 18+ (ESM/top-level await), Tesseract com traineddata lat/grc, Poppler (`pdftocairo`).
- `pip install -r requirements.txt`; para front-end: `cd web && npm install` (+ `npm install --save-dev pagefind`).
- Variáveis de ambiente: `OPENAI_API_KEY`, endpoints opcionais (`OPENAI_BASE_URL`, `OLLAMA_URL`).
- Hardware sugerido: OCR/LLM CPU multi-core; UMAP/HDBSCAN e Pagefind completo pedem >16–32 GB RAM; paralelismo alto em keywords causa tráfego/custo em provedores externos.

## Arquitetura de dados (diretórios/artefatos)
- Corpus OCR: `teste/P*/text/*` (~289.852 páginas, ~1B tokens). PDFs/imagens não são versionados.
- Intermediários: `ocr_out/`, `pages/` (imagens, XML, blocos), `data/tesseract.db` (cache OCR), `data/ocr_eval.db` (logs LLM judge).
- Resumos/keywords: `data/patristica_resumos.db`, `data/patristica_keywords.db`.
- Web build: `web/dist/` (Astro), `web/public/` shards (volumes/page_blocks/keywords), índice Pagefind custom em `web/public/pagefind/`.
- Logs: `logs/keywords_parallel`, `logs/keywords_parallel/*.log`, e saídas de avaliação em `data/ocr_eval.db`.

## Ingestão e OCR (`main2.py`)
- **Conversão**: PDF → PNG via `pdf2image`/`pdftocairo` (cache em `out/<pdf>/images`), texto em `out/<pdf>/text`.
- **Defaults**: `--dpi 300`, `--lang lat` (ajuste para fra+lat+grc+ell+syr), `--procs 12`, `--omp-threads 2` (limita OpenMP), `OMP_THREAD_LIMIT=4`.
- **Motores**: `--algorithm {tesseract,ollama,openai,gemini}`; modelos padrão `DEFAULT_LLM_MODEL=qwen3.5:397b-cloud` (Ollama) ou `gpt-5` (OpenAI). `--ollama-url`, `--openai-base-url`, `--openai-api-key` para overrides.
- **Flags úteis**: `--first/--last` (faixa), `--concat` (agrega em `texto_extraido.txt`), `--refresh-pages` (lista para reextrair), `--maxtasksperchild`, `--chunksize`, `--procs`.
- **Gating/validação**:
  - `--verify`: compara `.txt` existentes com Tesseract; `--verify-fix`: reprocessa falhas.
  - `--verify-judge-llm`: executa LLM judge só quando heurísticas detectam risco; `--judge-force` força LLM em todas.
  - Heurísticas: detecção de ruído/layout, sobreposição token/bloco LLM×Tesseract, CASOS_PERDIDOS (lista de páginas irrecuperáveis), classificação visual (vazia/capa/texto).
  - Avaliações gravadas em `data/ocr_eval.db` (campos: volume, página, provider, modelo, prompt_version, status, xml_raw/parse_result, duração).
- **Avisos**: custo de LLM, risco de alucinação ou omissão, ausência de revisão humana integral. Sempre validar contra o PDF original para uso acadêmico.

## Resumos e keywords
- **Resumos**: gerados a partir do OCR por script do projeto (salvos em `data/patristica_resumos.db`, tabela `resumos`).
- **Keywords — modo serial** (`keywords_serial.py`):
  - Fontes (`--source`): `resumo_pagina`, `resumo_global`, `pagina_texto`, `resumo+pagina`, `tudo` (default).
  - LLM: `--provider {ollama,openai}`, `--model`, `--reasoning-effort`, `--num-ctx`, timeouts separados.
  - Validação: `keyword_integrity` (NLTK/CLTK), `--verify` / `--verify-fix`, `--llm-judge`, `--no-skip-noise` (processa páginas marcadas como ruído).
  - Operação: DRY RUN por padrão (stdout); `--write` grava em `data/patristica_keywords.db` (schema `keywords`, índices em doc/página/source/modelo). `--preview-llm-judge` roda o judge mas não grava.
- **Keywords — modo paralelo** (`keywords_parallel.sh`):
  - Orquestra volumes em paralelo (default `--jobs 40`, provider OpenAI gpt-5-mini, reasoning minimal).
  - Filtros: `--pattern "PL*"`, `--source`, `--rerun-bad` (reprocessa páginas problemáticas), `--no-skip-noise`, `--verify/--verify-fix`, `--dry-run`.
  - Logs em `logs/keywords_parallel/<volume>.log`.
- **Avisos**: prompts/outputs somente em PT-BR; revisão humana limitada; valide keywords antes de uso editorial.

## Extração de índices internos (`scripts/run_index_extraction.py`)
- **Objetivo**: extrair a estrutura editorial dos índices que aparecem dentro de cada volume PG/PL, separando índice do começo do tomo, índices de abertura de obra e índices finais do volume.
- **Driver**: `scripts/run_index_extraction.py` executa um ciclo por volume, chama o prescan, monta o prompt do Codex, coleta o JSON final e importa o resultado no SQLite.
- **Prescan e prompt**:
  - `scan_volume.py` localiza páginas candidatas e títulos recorrentes em `teste/<VOLUME>/text/*`.
  - `build_volume_prompt.py` gera o envelope com blocos `TASK`, `VOLUME`, `PRESCAN`, `WORK INSTRUCTIONS`, `TODO`, `OUTPUT FILE` e `FINAL RESPONSE`.
  - O prompt manda preservar literais OCR e tratar a amostra inicial como mapa, não como verdade final.
- **Artefatos**:
  - `data/index_payloads/<VOLUME>_prescan.json`
  - `data/index_payloads/<VOLUME>_prompt.txt` quando `--dry-run`
  - `data/index_payloads/<VOLUME>_last_message.txt`
  - `data/index_payloads/<VOLUME>_indices.json`
  - logs persistentes em `data/index_logs/*`
- **Banco de dados**: o importador grava em `data/patristic_indices.db`, com tabelas `volumes`, `works`, `index_sections`, `index_entries` e `runs`.
- **Contrato de saída**:
  - payload JSON com top-level `volume`, `works`, `sections`, `notes`
  - chaves estáveis para `work_key` e `section_key`
  - preservação de `raw_json` em todos os níveis para auditoria posterior
- **Taxonomia**:
  - front-matter do volume: `ELENCHUS`, `AUCTORUM ET OPERUM`, `ORDO RERUM` quando funciona como inventário do tomo
  - índice de abertura de obra: `INDEX CAPITUM`, `PROLEGOMENA`, `CAPUT`, `LIBER`, `SECTIO`
  - índice final do volume: `ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, `INDEX GRÆCITATIS`
  - referência editorial em vez de numeração do arquivo OCR: o sufixo `*.txt` é apenas identificação técnica
- **Flags úteis**:
  - seleção: `--volume-id`, `--all-volumes`, `--blob`, `--limit`
  - execução: `--codex-bin`, `--model`, `--use-json`, `--replace`, `--skip-done`
  - operação: `--output-dir`, `--log-dir`, `--keep-temp`, `--dry-run`, `--verbose`
- **Avisos**:
  - o prescan é apenas ponto de partida; o volume precisa ser conferido em OCR antes do fechamento
  - números, colunas e referências internas devem ser mantidos literalmente quando houver dúvida
  - a importação substitui por volume quando `--replace` é usado; sem isso, o fluxo preserva o que já existe
  - a documentação canônica da classificação editorial continua em `docs/taxonomia_indices.md`
  - para o desenho ainda em aberto de um fluxo separado para índices alfabéticos, remissivos, onomásticos e bíblicos, ver `docs/levantamento_indices_alfabeticos.md`

## Extração de índices alfabéticos e remissivos (`scripts/run_alphabetical_index_extraction.py`)
- **Objetivo**: extrair payloads JSON para índices alfabéticos, analíticos, onomásticos, remissivos, bíblicos e de concordância em `PG`, `PL` e `PO`, usando o helper de localização material para apoiar a decisão do agente.
- **Driver**: `scripts/run_alphabetical_index_extraction.py` executa um ciclo por volume, consome páginas pré-filtradas quando existirem, monta o prompt do Codex para `$alphabetical-index-extractor`, coleta o JSON final, valida o payload e importa automaticamente o resultado no SQLite alfabético.
- **Entradas**:
  - JSON externo de páginas filtradas por volume via `--filtered-pages-json` ou `--filtered-pages-dir`
  - fallback heurístico interno quando não houver JSON externo; o script avisa quando isso acontecer
  - payload anterior opcional via `--previous-result-json` ou `--previous-result-dir`
- **Prompt e helper**:
  - o envelope segue `.codex/skills/alphabetical-index-extractor/references/prompt-contract.md`
  - o agente recebe `FILTERED PAGES`, `PREVIOUS RESULT`, `HELPER PATHS`, `OUTPUT FILE`
  - o helper material é `scripts/index_target_locator.py`, com request/response JSON temporários por volume
- **Artefatos**:
  - `data/alphabetical_index_payloads/<VOLUME>_filtered_pages.json`
  - `data/alphabetical_index_payloads/<VOLUME>_prompt.txt` quando `--dry-run`
  - `data/alphabetical_index_payloads/<VOLUME>_helper_request.json`
  - `data/alphabetical_index_payloads/<VOLUME>_helper_output.json`
  - `data/alphabetical_index_payloads/<VOLUME>_last_message.txt`
  - `data/alphabetical_index_payloads/<VOLUME>_alphabetical_indices.json`
  - logs persistentes em `data/alphabetical_index_logs/*`
- **Banco de dados**:
  - `scripts/init_alphabetical_index_db.py` inicializa `data/alphabetical_indices.db`
  - `scripts/import_alphabetical_index_json.py` importa um payload JSON canônico para o banco
  - o schema usa `alphabetical_volumes`, `alphabetical_sections`, `alphabetical_nodes`, `alphabetical_entries`, `alphabetical_refs`, `alphabetical_scripture_refs` e `alphabetical_runs`
- **Contrato de saída**:
  - payload JSON com top-level `schema_version`, `generated_at`, `volume`, `sections`, `nodes`, `entries`, `refs`, `scripture_refs`, `coverage`, `notes`
  - os aliases legados `section_start_file`, `editorial_anchor_file` e `target_file_best`
    permanecem distintos; no banco v8 use `index_section_start_ocr_file`,
    `index_entry_source_ocr_file` e `resolved_target_ocr_file`
  - o helper não decide o payload final; sua evidência deve ser preservada em `raw_json`
- **Flags úteis**:
  - seleção: `--volume-id`, `--all-volumes`, `--blob`, `--limit`
  - entradas externas: `--filtered-pages-json`, `--filtered-pages-dir`, `--previous-result-json`, `--previous-result-dir`
  - execução: `--codex-bin`, `--model`, `--use-json`, `--replace`, `--skip-done`, `--db`
  - operação: `--output-dir`, `--log-dir`, `--keep-temp`, `--dry-run`, `--verbose`
- **Avisos**:
  - o fallback interno de páginas filtradas é heurístico e mais fraco que um JSON externo curado
  - headings detectados automaticamente são apenas candidatos; a decisão editorial continua com o agente
  - `--skip-done` olha o status `imported` em `alphabetical_runs`, não apenas a presença do JSON no `output-dir`
  - `--replace` sobrescreve o payload de saída e substitui as linhas do mesmo volume no banco alfabético
  - a estratégia operacional de chunking, intermediários, `todo.json` e builders está em `docs/estrategia_pipeline_extracao_indices_alfabeticos.md`
  - o fluxo canônico está documentado em `docs/levantamento_indices_alfabeticos.md`, `docs/contrato_extrator_indices_alfabeticos.md` e `docs/normalizacao_indices_biblicos.md`

## Embeddings e clustering (`hdbscan_embedding.py`)
- Entrada: tabela `keyword_embedding` (BLOB float32) em `data/patristica_keywords.db` (pode filtrar por `--model`).
- Pipeline: UMAP (`--umap-components`, `--umap-neighbors`) → HDBSCAN (`--min-cluster-size`, `--min-samples`, `--cluster-selection-epsilon`).
- Saídas: recria `keyword_clusters`, `keyword_cluster_meta`; atualiza `keywords.hdbscan_group_id` (NULL para noise -1).
- Recursos: `--chunksize` (default 50k) para equilibrar RAM/tempo; usar máquinas com RAM adequada.

## Geração dos shards do site
1) Dicionário canônico: `python tools/export_keywords_dicts.py --db data/patristica_keywords.db --out web/public/dict --min-count 1` (ajuste `--include-noise`/`--top` conforme necessidade).
2) Renderização das páginas/shards: `python tools/render_publication_from_shards.py --index data/shards/enrichment/index.json --out web/public --db data/patristica_keywords.db --keywords-json web/public/dict/keywords.json --page-block-size 100 --raw-base-url <URL raw>`.
3) Export dos índices do banco: `python tools/export_indices_from_db.py --db data/patristic_indices.db --out web/public/indices`.
4) Build Astro + índice Pagefind custom (seção abaixo). O `npm run build` em `web/` chama `npm run build:indices` antes do build principal para garantir que `/indices` receba os JSONs atualizados.

## Busca web (Pagefind custom por shards)
- Por que custom: o índice completo excede limites do GitHub Pages; o Pagefind padrão em `dist/` não é usado diretamente.
- Script: `node tools/build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica --min-count 1 --concurrency 4`.
  - Inputs: `web/public/volumes.json`, shards em `web/public/page_blocks/*`, dicionário `web/public/dict/keywords.json` (keyword_id→label/count/grupo).
  - Para cada volume: lê blocos, monta `content` com `summary_page` + labels de keywords filtradas por `min-count`, addCustomRecord com URL `viewer?doc=<vid>&page=<p>` (filtros collection/volume, language `pt`).
  - Output: limpa `outDir`, grava índice em `web/public/pagefind/` via Pagefind API (`createIndex`, `writeFiles`), fecha com `close()`.
- Build completo: `cd web && npm run build && node ../tools/build_pagefind_from_shards.mjs --public dist --out dist/pagefind --base /BibliothecaPatristica` (ajuste `--base` se trocar path de publicação), publicar `dist/` + `pagefind/` juntos.

## Operação e monitoração (happy path)
1) OCR: `python main2.py input.pdf --out ocr_out --lang lat --algorithm tesseract` (ou `--verify-judge-llm` para gating).
2) Resumos: rodar pipeline de resumos (ex.: `python resumo_serial.py --volume-dir teste/PL001 --provider openai --model gpt-5-mini`).
3) Keywords: `./keywords_parallel.sh --pattern "PL%" --provider openai --model gpt-5-mini --jobs 20 --write` (ajuste flags/verify conforme necessidade).
4) Embeddings/clusters: `python hdbscan_embedding.py --db data/patristica_keywords.db --model qwen3-embedding:8b --chunksize 50000 --umap-components 5 --min-cluster-size 50`.
5) Web: `cd web && npm run build && node ../tools/build_pagefind_from_shards.mjs --public dist --out dist/pagefind --base /BibliothecaPatristica`.
6) Publicação: enviar `dist/` + `dist/pagefind/` (ou `web/public/pagefind/`) ao GitHub Pages.
- Logs/checagens: `logs/keywords_parallel/*.log`; `data/ocr_eval.db` para decisões do judge; smoke tests com PDFs pequenos; pytest sugeridos `test_limpeza_ocr.py`, `test_process_index.py`.

## Avisos e boas práticas
- **IA**: risco de alucinação, omissão e viés; sempre conferir no PDF original antes de uso acadêmico ou editorial.
- **Cobertura**: corpus parcialmente processado; ausência no índice não prova ausência no texto.
- **Custo e privacidade**: LLMs externos geram custo e podem trafegar dados sensíveis; avalie antes de enviar conteúdo.
- **Escala**: evitar rodar sobre todo `teste/` sem filtros; usar subconjuntos (`--pattern`, `--first/--last`).
- **Segurança**: chaves em variáveis de ambiente; não versionar PDFs/imagens; limpar dados sensíveis de logs se necessário.

## QA e testes rápidos
- Smoke pytest: `pytest test_limpeza_ocr.py test_process_index.py test_stream_pg002_630.py -q` (raiz).
- Keywords: `pytest test_keywords_clean.py test_keyword_integrity.py -q`.
- Regex/citações: `pytest test_scripture_ref_variants.py test_regex.py -q`.
- Perf rápido: `python -m cProfile -o /tmp/kw_profile.prof keywords_serial.py --verify --doc PG001 --limit 500`.

## Apêndice (schemas e flags)
- `data/patristica_resumos.db`: tabela `resumos(documento, pagina_num, resumo_pagina, resumo_global, modelo, criado_em, keywords_json, ...)`.
- `data/patristica_keywords.db`: `keywords(id, documento, pagina_num, source, keywords_json, keywords_raw, modelo, prompt_hash, criado_em, hdbscan_group_id)` + `keyword_embedding(keyword_id, model, embedding BLOB)` + `keyword_clusters`, `keyword_cluster_meta` (sobrescritos a cada run).
- `data/ocr_eval.db`: registros do judge com XML bruto, parse e status.
- CLIs chave:
  - `main2.py --algorithm ... --verify-judge-llm --judge-force --verify/--verify-fix --refresh-pages --concat`.
  - `keywords_serial.py --source ... --provider ... --model ... --write --verify/--verify-fix --llm-judge --preview-llm-judge --no-skip-noise --timeout --num-ctx --rerun-bad --openai-url/--ollama-url`.
  - `keywords_parallel.sh --jobs --pattern --source --provider --model --verify/--verify-fix --rerun-bad --no-skip-noise --dry-run --log-dir`.
  - `hdbscan_embedding.py --db --model --chunksize --umap-components --umap-neighbors --min-cluster-size --min-samples --cluster-selection-epsilon`.
  - `build_pagefind_from_shards.mjs --public --out --base --min-count --concurrency`.
  - Auxiliares: `tools/ingest_keywords_from_resumos.py` (backfill/parse em SQL), `tools/export_keywords_dicts.py` (dict para web), `tools/generate_keyword_embeddings.py` e `tools/generate_resumo_embeddings.py` (embeddings), `tools/fill_canon_keywords.py` (nomes canônicos), `tools/preview_related_pages.py` (relação de páginas via embeddings).

## Tabela rápida de ferramentas (`tools/`)
- **OCR / validação**: `rerun_bad_evals.py` (reprocessa páginas com avaliações ruins, filtros de data/provedor/modelo), `classify_scan_kind.py` (capa/vazia/texto), `bbox_playground.py` (anotar boxes).
- **Resumos**: `backfill_clean_resumos.py --limit 1000 --batch-size 200` (preenche campos limpos/flags em `resumos`).
- **Keywords & judge**: `ingest_keywords_from_resumos.py` (carrega keywords do DB de resumos), `generate_keyword_embeddings.py --db data/patristica_keywords.db --batch-size 512`, `cluster_keywords.py` (embedding faltante + HDBSCAN), `fill_canon_keywords.py` (nomes canônicos), `preview_related_pages.py --db data/patristica_resumos.db --kind page --queries DOC:PAGE`, perf/bench: `bench_keywords.py`, `profile_keywords.py`.
- **Resumos embeddings**: `hdbscan_resumo_embeddings.py` (+ utils em `resumo_embedding_utils.py`) para embeddings/clusters dos resumos.
- **Export/publicação**: `export_enrichment_shards.py --all --no-text` (gera `data/shards/enrichment/*.ndjson` + `index.json`), `export_keywords_dicts.py --db ... --out web/public/dict --top 80 --min-count 1` (dicts), `render_publication_from_shards.py --index data/shards/enrichment/index.json --out web/public --db data/patristica_keywords.db --keywords-json web/public/dict/keywords.json --page-block-size 100 --raw-base-url <raw_url>`, `build_dict_bundle.py` (all.json), `build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica`.
- **Playground/diagnóstico**: `opencv_playground.py` (cadeias OpenCV), `color_band_compare.py` (deltaE borda/centro), `read_poribp.py` (lex PorIBP), `bbox_playground.py` (visual de layout).

*Limitação de idioma*: prompts, resumos e keywords são mantidos somente em PT-BR; este README em inglês é apenas referência.
