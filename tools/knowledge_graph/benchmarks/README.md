# Summary-v2 historical KG benchmark discovery

Read-only, local discovery and review preparation. No final KG extractor, model inference,
API requests, OCR repairs, generation promotion or changes to production pipelines.
Generated files belong in the ignored `.work/kg_benchmark_v1/` directory.

The actual inspected database has no promoted v2 generations. Its available benchmark
is explicitly **experimental shadow**, with valid and metadata_pending kept separate.
Default discovery still requires promoted generations and never falls back to legacy.

## Commands

Run from the repository root (Python 3.10+ and existing project dependencies):

```bash
python tools/knowledge_graph/benchmarks/discover_candidates.py \
  --db data/patristica_resumos.db --corpus-root teste \
  --output-dir .work/kg_benchmark_v1 --seed 20261031 --pool-size 1100
```

To deliberately select experimental shadow runs, supply a JSON array of actual run IDs.
The completed discovery has this explicit inventory in `shadow_runs.json` and details
in `shadow_run_inventory.json`. Those files are provenance, not authorization to promote.

```bash
python tools/knowledge_graph/benchmarks/discover_candidates.py \
  --scope shadow --shadow-run-ids-file .work/kg_benchmark_v1/shadow_runs.json \
  --db data/patristica_resumos.db --corpus-root teste \
  --indices-db data/patristic_indices.db --ocr-versions-db data/ocr_versions.db \
  --output-dir .work/kg_benchmark_v1 --seed 20261031 --pool-size 1100

python tools/knowledge_graph/benchmarks/select_samples.py \
  --output-dir .work/kg_benchmark_v1 --seed 20261031 --sample-size 240 --pair-count 40

python tools/knowledge_graph/benchmarks/enrich_selected.py \
  --output-dir .work/kg_benchmark_v1 --corpus-root teste \
  --curation .work/kg_benchmark_v1/source_inspections.json --scripture

python tools/knowledge_graph/benchmarks/validate_sources.py \
  --output-dir .work/kg_benchmark_v1 --db data/patristica_resumos.db \
  --corpus-root teste --check-db

python -m pytest -q tests/test_kg_benchmark_discovery.py \
  tests/test_ocr_xml_utils.py tests/test_resumo_v2.py

python tools/knowledge_graph/benchmarks/prepare_review_batches.py \
  --output-dir .work/kg_benchmark_v1 --batch-size 12 --max-input-tokens 60000

python tools/knowledge_graph/benchmarks/write_discovery_report.py \
  --output-dir .work/kg_benchmark_v1
```

The inspection file is a curated input, not generated automatically by a heuristic.
It contains exact segment/OCR quotations and revisable source readings. Omit `--curation`
on a new sample until it has been inspected. A changed sample can invalidate this file’s
IDs; the enrichment stage fails rather than silently applying annotations to other pages.
Run selection before reapplying inspections, as it reconstructs the original pair pool.

Use a fresh output directory for a new discovery snapshot, especially when changing the
seed or corpus. Reusing a directory replaces named outputs; frozen source files from
earlier discovery runs can remain. Reproduction of selection uses `candidate_pool.jsonl`
and the seed; reproduction of annotations also requires `source_inspections.json`.
Live database changes are deliberately detected by `--check-db`.

## Evidence contract

- One record counts one physical page. All v2 segments are preserved, with one focal
  segment supplying the exact `summary_quote`. `metadata_pending` is never relabelled valid.
- Cumulative summaries supply context only, never direct current-page evidence.
- `verified` OCR support applies only to a bounded inspected quotation. Review status
  and `gold_status=not_gold` remain explicit; agreement by another model is not gold.
- Raw OCR snapshots, byte hashes and code-point offsets are distinct from translated
  summary quotations and deterministic scripture block-relative offsets.
- The original v2 source hash is composite and cannot fully be reconstructed from the
  stored inputs. It is preserved and compared with SQLite, not called an OCR digest.
  The v2 output hash and independent raw-byte/segment/snapshot hashes are recomputed.
- Historical OCR mismatch excludes a candidate from selection; an unproven numerical
  filename fallback cannot silently substitute a different source.
- Deduplication compares page-local summaries and OCR body, excluding cumulative
  summaries and repeated headers. Exact full-file duplicates are also excluded.
- Blind holdout has whole-volume, work-key and catalog author/title isolation. Pairs
  never cross splits. Isolation is procedural; restrict access to holdout packets.
- The representative split is diversity-balanced and oversamples editorial material.
  It is not a probability estimate of accuracy over the full 288,000-page corpus.

`review_batches/` contains full-page OCR and original v2 summaries. Token estimates use
characters/3 (range characters/4–characters/2), not a particular model’s tokenizer.
Long pages reduce batch size. Packet outputs must remain proposals pending adjudication.

The corpus profile includes the validated SQLite schema, status/scope distributions,
volumes, page kinds, segment counts, length bins, work/author hints, scripture density,
metadata conflicts and script/OCR indicators. See `discovery_report.md` for the actual
sample, strengths, misleading candidates, coverage gaps and review readiness.
