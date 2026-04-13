# Contributing to the Patrística Digital Project
- Corpus ~1B tokens in `teste/P*/text/*`; don't run jobs over the full set without coordination. Use subsets (`--first/--last`, `--pattern`, `--limit`).
- Do **not** commit PDFs/images. Large deploy artifacts (Pagefind/dist) only in the publish branch.
- SQLite databases (`data/*.db`) and Parquet files are **not versioned** — they exceed GitHub's file-size limit. Recreate them locally by running the pipeline. Keep the schemas intact; see `docs/PIPELINE.md` for the expected structure of `patristica_resumos.db`, `patristica_keywords.db`, and others.stica Digital Project

**Language note:** Project artifacts (OCR text, summaries, keywords, prompts) are PT-BR only. This guide is in English first to reduce friction for external collaborators; a PT-BR mirror follows below.

## What this repo is (and isn’t)
- OCR + LLM pipeline for Patrologia (Graeca/Latina/Orientalis), per-page summaries/keywords in PT-BR, embeddings, and a static search site (Pagefind custom).
- PDFs/images are **not** versioned here; only derived text/metadata.
- Outputs stay in PT-BR; English docs are reference only.

## How to contribute
- **Pipeline/OCR/LLM**: heuristics in `main2.py`, gating (`--verify`, `--verify-judge-llm`), text cleaning, reruns (`tools/rerun_bad_evals.py`).
- **Summaries/keywords**: extraction/normalization improvements in `keywords_serial.py`, canonical dictionaries (`export_keywords_dicts.py`, `fill_canon_keywords.py`).
- **Embeddings/clustering**: `generate_keyword_embeddings.py`, `generate_resumo_embeddings.py`, `hdbscan_embedding.py`, `hdbscan_resumo_embeddings.py`.
- **Search/web**: Pagefind custom (`build_pagefind_from_shards.mjs`), shard generation (`render_publication_from_shards.py`), viewer UX/i18n.
- **Observability/QA**: focused pytest, perf/bench scripts, integrity checks.

## Quick setup
1) Python 3.10+, Tesseract (lat/grc), Poppler (`pdftocairo`).
2) `pip install -r requirements.txt`
3) Front-end: `cd web && npm install && npm install --save-dev pagefind`
4) Env vars: `OPENAI_API_KEY` (if using provider openai); optionally `OPENAI_BASE_URL` / `OLLAMA_URL`.
5) Read `docs/PIPELINE.md` for the end-to-end flow and artifacts.

## Data and limits
- Corpus ~1B tokens in `teste/P*/text/*`; don’t run jobs over the full set without coordination. Use subsets (`--first/--last`, `--pattern`, `--limit`).
- Do **not** commit PDFs/images. Large deploy artifacts (Pagefind/dist) only in the publish branch.
- Keep SQLite schemas intact (`data/patristica_resumos.db`, `data/patristica_keywords.db`); see summaries in `docs/PIPELINE.md`.

## Quality & tests
- If you change parsing/normalization: `pytest test_keywords_clean.py test_keyword_integrity.py -q`.
- OCR/verify: `pytest test_limpeza_ocr.py test_process_index.py -q`.
- Smoke keywords: `python keywords_serial.py --verify --doc <DOC> --limit 5`.
- Report in PRs the commands/flags, volumes, and pages you used.

## Style
- Python: 4 spaces, short functions; comments only for non-obvious logic.
- Front-end: follow existing `web/` patterns; avoid “AI slop”; outputs remain PT-BR.
- Do not translate outputs to EN; docs may be bilingual, content stays PT-BR.

## Security & privacy
- Never commit secrets; use env vars.
- Consider cost/privacy when sending OCR to external LLMs; prefer `--provider ollama` if sensitive.
- Scrub logs with private data before sharing.

## PR checklist
- Small, focused scope; explain what/why.
- Include commands run + results (mention doc/page ranges).
- Screenshots/samples when changing UI or search output.
- Link to relevant docs: `docs/PIPELINE.md`, `README.pt-BR.md`.

---
# Contribuindo para o Projeto Patrística Digital (PT-BR)

**Nota de idioma:** Artefatos do projeto (OCR, resumos, keywords, prompts) são apenas em PT-BR. Este guia reflete a mesma informação da seção em inglês, com termos locais.

## O que é / não é
- Pipeline de OCR + LLM para Patrística, resumos/keywords por página em PT-BR, embeddings e site estático com Pagefind custom.
- PDFs/imagens **não** são versionados; só textos/derivados.
- Outputs permanecem em PT-BR; inglês é referência.

## Como contribuir
- **Pipeline/OCR/LLM**: heurísticas no `main2.py`, gating (`--verify`, `--verify-judge-llm`), limpeza, reruns (`tools/rerun_bad_evals.py`).
- **Resumos/keywords**: extração/normalização em `keywords_serial.py`, dicionário canônico (`export_keywords_dicts.py`, `fill_canon_keywords.py`).
- **Embeddings/clustering**: `generate_keyword_embeddings.py`, `generate_resumo_embeddings.py`, `hdbscan_embedding.py`, `hdbscan_resumo_embeddings.py`.
- **Busca/web**: Pagefind custom (`build_pagefind_from_shards.mjs`), shards (`render_publication_from_shards.py`), UX do viewer/i18n.
- **Observabilidade/QA**: testes pytest focados, perf/bench, verificações de integridade.

## Setup rápido
1) Python 3.10+, Tesseract (lat/grc), Poppler.
2) `pip install -r requirements.txt`
3) Front-end: `cd web && npm install && npm install --save-dev pagefind`
4) Variáveis: `OPENAI_API_KEY`, `OPENAI_BASE_URL` / `OLLAMA_URL` se aplicável.
5) Leia `docs/PIPELINE.md` para entender o fluxo.

## Dados e limites
- Corpus ~1B tokens em `teste/P*/text/*`; evite rodar em tudo. Use filtros (`--first/--last`, `--pattern`, `--limit`).
- Não commitar PDFs/imagens; artefatos grandes só no branch de publicação.
- Bancos SQLite (`data/*.db`) e arquivos Parquet **não são versionados** — excedem o limite de tamanho do GitHub. Recrie-os localmente rodando o pipeline. Preserve os schemas; veja a estrutura esperada de `patristica_resumos.db`, `patristica_keywords.db` e demais em `docs/PIPELINE.md`.

## Qualidade & testes
- Mudou parsing/normalização: `pytest test_keywords_clean.py test_keyword_integrity.py -q`.
- OCR/verify: `pytest test_limpeza_ocr.py test_process_index.py -q`.
- Smoke keywords: `python keywords_serial.py --verify --doc <DOC> --limit 5`.
- Documente no PR os comandos/flags, volumes e páginas usados.

## Estilo
- Python com 4 espaços; comentários só para lógica não óbvia.
- Front-end: siga o padrão de `web/`; evitar “AI slop”; outputs seguem em PT-BR.
- Não converter outputs para EN.

## Segurança e privacidade
- Nada de segredos no repo; use env vars.
- Avalie custo/privacidade antes de enviar OCR a LLM externo; prefira Ollama quando sensível.
- Limpe logs com dados privados.

## Checklist de PR
- Escopo pequeno e explicado.
- Comandos/resultados (inclua faixas de docs/páginas).
- Prints/amostras se mexer em UI/busca.
- Links úteis: `docs/PIPELINE.md`, `README.pt-BR.md`.
