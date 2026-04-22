# Sessão de Trabalho — 14 de abril de 2026

**Projeto:** BibliothecaPatristica  
**Branch:** `codex`  
**Objetivo:** Implementar sistema completo de versionamento OCR com SQLite

---

## Resumo Executivo

Nesta sessão implementamos o sistema de versionamento OCR do zero até produção,
cobrindo planejamento, código, testes, backfill de ~290 mil páginas e ferramentas
de consulta. O `main2.py` já está rodando com Ollama (`qwen3.5:cloud`) e
registrando versões automaticamente.

---

## Fases Concluídas

### P0 — Módulo core + Testes (arquivos novos)

| Arquivo | Linhas | Descrição |
|---|---|---|
| `ocr_versions_db.py` | ~465 | Módulo principal: schema, API, diff dinâmico, revert |
| `tests/test_ocr_versions_db.py` | ~540 | 37 testes unitários — todos passando |
| `scripts/backfill_versions.py` | ~388 | Backfill multiprocesso (N compute + 1 writer) |
| `scripts/reingest_tesseract_cache.py` | ~250 | Re-ingestão do `tesseract_cache` |

**Schema criado:**
- `ocr_engine_versions` — catálogo de engine+model+prompt (8 registros)
- `ocr_results` — cada versão de texto OCR com metadados completos

**Índices (7 no total):**
- `idx_ocr_results_vol_page`, `idx_ocr_results_image_path`, `idx_ocr_results_is_current`
- `idx_ocr_results_text_hash`, `idx_ocr_results_image_hash`
- `idx_ocr_results_dedup` — covering index `(image_hash, engine_version_id, text_hash)`
- `idx_ocr_results_vol_page_current` — partial index `WHERE is_current = 1`

### P1 — Integração no `main2.py`

9 edits no `main2.py`:
- `import ocr_versions_db`
- Assinatura de `_ocr_one`: `reprocess_reason: str | None`
- Blocos de versionamento nos 3 caminhos: initial, reprocess, legacy
- Todos os callers atualizados com `reprocess_reason` semântico
- **66/66 testes passando**, zero erros de lint

### P2 — Backfill Completo

**Desafio de performance:**

| Versão | Arquitetura | Velocidade | Problema |
|---|---|---|---|
| v1 | Single-process, 1 tx/arquivo | ~15 pgs/s | SHA256 + fsync por arquivo |
| v2 | Batch flush + hash cache | ~50 pgs/s | Single-process |
| v3 | `mp.Pool` + `mp.Process` | ~22 pgs/s | `BEGIN IMMEDIATE` serializa writers |
| v4 | N compute + 1 writer thread | ~4000 pgs/s (skip) | Writer per-record loop |
| v4+ | + bulk temp table + índices compostos | **4000+ pgs/s** | ✅ Resolvido |

**Otimizações aplicadas:**
1. Covering index `idx_ocr_results_dedup` — elimina lookup de tabela na idempotência
2. Partial index `idx_ocr_results_vol_page_current` — UPDATE cirúrgico
3. Writer com temp table `_staging` + `executemany` em batch de 5000
4. N=20 compute workers paralelos (SHA256 de imagens + leitura de .txt)
5. 1 writer thread no processo principal (single SQLite connection)

**Resultado final:**

```
═══ OCR Versions DB ═══
  DB:          data/ocr_versions.db  (2.5 GB)
  Rows total:  339,740
  Correntes:   288,610
  Históricas:  51,130
  Volumes:     408
  Páginas:     288,580
  Engines:     8
  Cobertura:   100.0%
```

**Engines registradas:**

| Engine | Model | Total | Current |
|---|---|---|---|
| unknown | - | 290,807 | 287,858 |
| tesseract | lat+grc | 17,427 | 640 |
| tesseract | fra+lat+grc+ell+syr+hye+ara+heb+eth | 16,094 | 0 |
| tesseract | lat+grc+ell | 15,346 | 48 |
| ollama | qwen3.5:cloud | 59+ | 59+ |
| tesseract | lat | 5 | 5 |

### P3 — CLI + Integração ingest

**`scripts/ocr_versions_cli.py`** — 7 subcomandos:

```
ocr_versions_cli.py history PL001 42    # Histórico de versões
ocr_versions_cli.py current PL001 42   # Versão corrente (-t para texto)
ocr_versions_cli.py diff PL001 42      # Diff chain entre versões
ocr_versions_cli.py diff --ids 123 456 # Diff entre dois IDs
ocr_versions_cli.py revert 1234        # Reverter (com confirmação)
ocr_versions_cli.py stats              # Estatísticas globais
ocr_versions_cli.py engines            # Engine versions (-v para prompts)
ocr_versions_cli.py search --engine ollama --limit 10  # Busca filtrada
```

Todos os subcomandos têm aliases curtos (`h`, `c`, `d`, `s`, `e`, `f`).

**`patristica_pipeline/db.py`** — Schema atualizado:
- Coluna `ocr_result_id INTEGER` adicionada à tabela `pages`
- Migração automática via `ALTER TABLE` para DBs existentes

**`patristica_pipeline/ingest.py`** — Integração:
- Import opcional de `ocr_versions_db` (funciona sem ele)
- Parâmetro `versions_db: Optional[Path]` retrocompatível
- Lookup automático de `ocr_result_id` corrente para cada página

---

## Arquivos Criados

| Arquivo | Tipo |
|---|---|
| `ocr_versions_db.py` | Módulo Python (core) |
| `tests/test_ocr_versions_db.py` | Testes unitários |
| `scripts/backfill_versions.py` | Script de backfill |
| `scripts/reingest_tesseract_cache.py` | Script de re-ingestão |
| `scripts/ocr_versions_cli.py` | CLI de consulta |
| `docs/VERSIONING_PLAN.md` | Plano detalhado (868 linhas) |
| `data/ocr_versions.db` | SQLite WAL (2.5 GB, auto-criado) |

## Arquivos Modificados

| Arquivo | Mudança |
|---|---|
| `main2.py` | +194 linhas — import, versionamento em 3 caminhos |
| `patristica_pipeline/db.py` | +6 linhas — coluna `ocr_result_id`, migração |
| `patristica_pipeline/ingest.py` | +20 linhas — lookup de `ocr_result_id` |

---

## Decisões de Design

1. **Texto inline no SQLite** — portável, sem dependência de filesystem, diff direto
2. **Banco separado** (`ocr_versions.db`) — separation of concerns do `ocr_eval.db`
3. **Diffs não persistidos** — calculados sob demanda via `compute_diff()`
4. **SHA256 everywhere** — imagem, texto, system prompt, user prompt
5. **`user_prompt_hash`** calculado sobre template (`{placeholder}`), não valor renderizado
6. **Erros de versionamento nunca propagam** — wrapped em try/except
7. **WAL + synchronous=NORMAL** — safe com WAL, ~3× mais rápido que FULL
8. **Single writer** — 1 thread de escrita, sem `BEGIN IMMEDIATE` (desnecessário)
9. **`ocr_result_id` nullable** — ingest funciona com ou sem `ocr_versions.db`

---

## Itens Pendentes (P3 restante)

- [ ] `traineddata_hash` em `ocr_engine_versions` (baixa prioridade)
- [ ] Re-rodar ingest do `patristica_text.db` para popular `ocr_result_id`
- [ ] Atualizar `docs/VERSIONING_PLAN.md` com checkmarks P2/P3
- [x] Integrar `ocr_result_id` no `resumo_serial.py` → `patristica_resumos.db` (banco principal, 15 GB, 287K resumos)

---

## Ambiente

- Python 3.12, venv em `.venv/`
- 24 cores CPU, SSD NVMe
- SQLite WAL, `busy_timeout=30s`
- Branch: `codex`
