# Historical Knowledge Graph Extraction Playground

Local research instrument for **one physical page per extraction**. The model extracts contextual concepts first, with optional entities, claims, events and relations. IDs are local to that page. No global merging, production graph writes, summary generation, OCR edits or public-site changes occur.

For the consolidated Portuguese account of decisions, benchmark results, pilot runs and the running corpus campaign, see the [KG documentation index](knowledge_graph/README.md) and [dated results report](knowledge_graph/estado_e_resultados_2026-11-01.md).

## Launch

From the repository root, with Python 3.10+:

```bash
python -m pip install -r tools/knowledge_graph/playground/requirements.txt
python scripts/playgrounds/kg_extractor_playground.py serve
```

Open **http://127.0.0.1:8766**. The server uses the Python standard library, static JavaScript/SVG and CSS. No Node dependencies, frontend bundle or CDN is needed. Opening the UI performs no model calls. Production databases use SQLite `mode=ro` plus `PRAGMA query_only=ON`; research data defaults to `data/playgrounds/kg_extractor_playground.db`.

Select a page, inspect its sources, configure a model and click **Preparar e inspecionar prompt**. This saves a complete, resumable dry-run plan. Inspect each final prompt/payload in the selector. **Executar extração** is the explicit inference action and can incur provider charges. The demo button selects a synthetic fixture replay with no model call.

The UI also supports benchmark filters, adding physical pages to a batch, a matrix of provider profiles, repetitions, few-shot comparison, custom system-prompt revisions, graph zoom/pan/filter, source/evidence navigation, image overlays, side-by-side run comparison, semantic reviews and exports. Profiles added to the matrix replace the single currently edited profile; the displayed matrix is the execution list. Select stored runs with the checkboxes in Histórico to compare them.

## Inspected repository infrastructure and architecture

Inspected `resumo_v2.py`, `resumo_serial.py`, `resumo_parallel.sh`, `docs/pipeline_resumos_v2.md`, `scripts/playgrounds/resumo_prompt_playground.py`, `tools/ocr_xml_utils.py`, `tools/corpus_utils.py`, `tools/indexing/index_target_locator.py` and `facsimile_transport.py`, plus the six starter-kit files.

The live `data/patristica_resumos.db` has `resumo_generations`, including `id`, `run_id`, `ocr_result_id`, `documento`, `pagina_num`, `pagina_file`, `is_current`, `status`, `tainted_by_page`, `segments_json`, `page_kinds_json`, `summary_display_pt`, `cumulative_summary`, `static_analysis_json`, `source_conflicts_json`, `contributors_json`, `source_hash`, `output_hash` and `previous_generation_id`. The loader checks the necessary columns before querying. The inspected `data/patristic_indices.db` has a `works` table keyed by `work_key`, with `volume_id`, `author_raw`, `title_raw` and physical/index provenance fields. Linked historical OCR lives in the separate `data/ocr_versions.db` table `ocr_results` (`id`, `volume_id`, `page_num`, `text_content`), not in the summary database.

The initial v1 response schema and system prompt remain at their **original starter-kit paths** in `scripts/playgrounds/`. The experimental v2 schema lives at `tools/knowledge_graph/schemas/kg-extraction-v2.schema.json`; these are explicitly different contracts. The starter validator is a compatibility entry point into the full validator; pass `--schema` for standalone v2 files. Saved runs always revalidate against their frozen schema. The original input/output demo files are unchanged.

| Module | Responsibility / reuse |
|---|---|
| `snapshot_v2.py` | Read-only v2 selection, complete segments, source locators, output hash verification, historical OCR checks, frozen benchmark loading. Reuses `resumo_v2.sha256_text`, `tools.corpus_utils` and the safe paired-image resolver. |
| `ocr_adapter.py` | Reuses tolerant OCR diagnostics, preserves uncleaned visible block text, unknown block types and notes. Deterministic Python IDs in document order. |
| `prompt_registry.py` + `prompts/registry.json` | Versioned starter/few-shot variants, three modes, prompt revisions and hashes. |
| `providers.py` | Side-effect-free request construction and explicit provider capability profiles; bounded HTTP transport retries. |
| `storage.py` | Separate SQLite history, atomic complete experiment plans and content-addressed image archive. |
| `experiment_runner.py` | Page/model/prompt matrix, exact request snapshots, input hashes, cache, repetitions, progress, bounded concurrency and resume. Reuses `facsimile_transport.encode_facsimile_for_transport`. |
| `validation.py`, `evidence_matching.py` | Full schema, local identifiers/references, exact and approximate source location, review diagnostics. |
| `comparison.py` / `reporting.py` | Conservative page-local matching, differing evidence/relations, costs, latency and exports. |
| `service.py` / `web_app.py` / `static/` | Shared application boundary, localhost HTTP API, source and graph interface. |
| `scripts/playgrounds/kg_extractor_playground.py` | CLI entry point. |

The existing summary playground imports the summary execution script. This implementation instead imports only small reusable helpers and does not import its execution logic. No inference, API key lookup or database write happens on module import.

## Source policy and fidelity

Normal loading prefers an eligible current generation: `is_current=1`, status `valid` or `metadata_pending`, and no taint. **As explicitly authorized for the current corpus, absence of an eligible current generation falls back to the eligible shadow with greatest generation ID.** The snapshot records `selection_policy=latest_eligible_shadow_fallback`, `scope=shadow`, generation ID, run ID and status. It never promotes anything. `metadata_pending` remains visible as its own status. Explicit `--generation-id` or `--run-id` overrides automatic selection; tainted/context-provisional generations are rejected.

All current-page segments survive, with their original order, summaries and work keys. IDs `s1`, `s2`, … are assigned by Python in array order. Bibliographic context includes stored v2 analysis/contributors/conflicts and matching current index rows where available. Index rows are context and may differ from the older generation's hints; both are snapshotted separately. `cumulative_summary` is explicitly under `context_only` and **cannot be cited as current-page evidence**. No previous-page summaries are added.

The stored OCR filename is authoritative. Physical-page suffixes are validated; editorial pages/Migne columns are never used as physical locators. An ambiguous numeric fallback is rejected. An unambiguous fallback requires a matching linked historical OCR record. Existing files are checked against historical OCR when that database is available; drift fails by default and requires `--allow-source-drift`. If historical OCR is unavailable, that limitation is recorded. Invalid UTF-8 fails rather than replacing source characters.

The v2 `output_hash` is recomputed with the existing algorithm. Its composite `source_hash` is preserved as opaque provenance: the original historical index/scripture prompt inputs cannot always be reconstructed. Raw OCR bytes, visible blocks and summaries receive separate reproducible hashes through the frozen case snapshot. Do not confuse the v2 composite hash with OCR SHA256.

The shared OCR cleaner removes line-break hyphenation and can omit unfamiliar XML children. The adapter therefore preserves visible text before cleaning, including line breaks, ligatures, scripts and unknown types. It retains the original OCR XML as well. Malformed XML uses a tolerant text-chunk fallback; chunk boundaries receive a warning and need review. XML entities are decoded as visible text; quotations are located within that **stored block text**. It does not pretend that a decoded XML entity is the raw serialization. Exact and approximate matches remain distinguishable.

Before building a new model request, `prepare_model_ocr` applies the existing `tools.ocr_xml_utils.join_linebreak_hyphenation` exclusively to a copy of each block's `text`. For example, `inter-\npretatio` becomes `interpretatio`; ordinary line breaks and inline hyphens remain. The playground sends the XML's structured block representation as JSON: block IDs, ordering, types, scripts, attributes, notes and geometry remain unchanged. No joining crosses block or physical-page boundaries. The original XML file and source snapshot/viewer keep their original text. Source validation maps dehyphenated quotes back to original spans using the approximate matcher.

Every new prepared request records `ocr_preparation` with policy version `kg-ocr-model-text-1`, helper name, changed-block count and source/model text hashes for every block. This audit is stored separately from the model payload. It participates in the inference cache key alongside the actual prepared request. Saved plans and historical runs remain frozen; prepare a new experiment to apply the change. Loading a case or resuming an old plan does not rewrite its source or request. This step is text preparation only, not OCR regeneration or correction of historical vocabulary.

Images are resolved with the existing unambiguous physical-page resolver, checked for decoding and size, hashed and copied into the playground's content-addressed asset directory. The source image is never modified. `off`, `on`, and `auto` are independent of extraction mode; `auto` uses the stored v2 facsimile score with the existing threshold of 3. Attachment also requires a profile declaring vision support. The archived original and the exact encoded transport image have distinct hashes. Preparation records image selection; a completed run separately records whether it actually sent an image. A cached result makes no request and is marked accordingly.

Overlay coordinates come only from parsed OCR. The default is the repository's normalized 0–1000 convention (see `main2.py` and `tools/bbox_playground.py`). The viewer lets the reviewer switch to image pixels or disable overlays. It never fabricates a missing rectangle. OCR-generated geometry still requires visual checking.

## Prompts and response contract

- `concepts-v1`: starter system prompt without examples.
- `concepts-v1-fewshot`: same prompt plus the explicitly synthetic starter example, adapted for concepts-only mode.
- `concepts-v1.1`: starter plus explicit namespaces (`e*` for entities, `ev*` for evidence), literal-quote copying and bare-JSON instructions. Select with `--prompts concepts-v1.1`; the original variants and default are preserved for comparison.
- `concepts-v1.2`: a separate prompt focused on coherent proposition ownership, separating the critic's evaluation from the positions reported, preserving conditions, and citing enough context to substantiate a relation. It asks for the actual actor of REFUTES/DEFENDS/AFFIRMS, qualifies indirect CITES/INTERPRETS links, and keeps Portuguese descriptions consistent. It uses the same response schema and contains no page-specific answers or historical examples. Select with `--prompts concepts-v1.2`; the default remains `concepts-v1`. The prompt is an experimental hypothesis, not a demonstrated model-quality improvement.
- `concepts-v2-confidence`: the same v1.2 extraction instructions plus a separately versioned confidence protocol and `kg-extraction-v2` schema. Each concept, entity, claim, event and relation requires `model_confidence: {"score": 0.0, "rationale": "..."}`. `score` is a number from 0 to 1; `rationale` is a short source-based explanation, at most 400 characters. Evidence and warnings have no confidence field. Old runs receive no backfilled scores. See the [research and evaluation protocol](kg_model_confidence.md).
- `concepts-v1.3`: strengthens actor and stance coverage. In relation-enabled modes, an actor supported by the current summary or appropriate work metadata should not be omitted solely because their name is absent from OCR. Identity and stance use separate evidence where needed; unresolved attribution produces a warning. Multiple works, quoted opponents, editorial voices and uncertain authorship remain distinct. Metadata-only identity uses `bibliographic_context`, with a warning instead of fabricated quotation locators. The final check covers omissions as well as incorrect attribution. It retains the v1 schema and leaves `concepts_only` unchanged.
- `concepts-v2.1-confidence`: v1.3 actor-coverage instructions with the same confidence protocol and v2 schema as `concepts-v2-confidence`. Compare those two variants to evaluate the attribution reinforcement without changing the confidence contract. The older variants remain available; these revisions require explicit selection and have no demonstrated model-quality gain until new outputs are reviewed.
- `concepts-v1.4` / `concepts-v2.2-confidence`: retain actor coverage and add original/translation distinctions for bilingual pages. They ask whether parallel passages represent one text in two languages before duplicating concepts, claims or voices, require evidence from the actual language/block, and preserve material disagreements and editorial additions. Language or column position alone cannot establish translation direction. The confidence variant keeps the v2 schema and confidence protocol; the non-confidence variant keeps v1. Select these versions explicitly for new experiments; the previous prompts remain reproducible.
- Modes: `concepts_only`, `concepts_relations`, `full_multimodal`.
- `concepts_only` requires empty entities/claims/events/relations; evidence and warnings remain available.
- `--system-prompt-file` or the UI editor creates a persisted prompt revision; the mode instructions are appended and the resulting final prompt is shown and hashed.
- System instructions are English. Keys/predicates remain English; Portuguese descriptions and original terminology/quotations are preserved.

Each run stores the exact final system prompt, user JSON payload, canonical schema, their hashes and the actual provider request body. For OpenAI-compatible strict output, the schema's constant version is represented by an equivalent singleton enum in the transport schema; the authoritative starter schema remains unchanged and is always used locally. Uploaded/documentary content is treated as untrusted source material.

The **v1 schema has no confidence field**. In experimental v2, confidence concerns the fidelity of an extracted item to its sources, including meaning, attribution, modality and relation direction. It does not assess whether a reported theological or historical assertion is objectively true. The interface labels it **autodeclarada / não calibrada**, shows its rationale when selecting a node or relation, and displays differences for matched items in comparisons without treating higher confidence as better accuracy. The default remains v1 for reproducibility.

`grounding` describes explicit versus inferred support; `salience` describes importance; claim modes and warnings express modality or ambiguity. The validator's quote-matching score measures lexical similarity. These remain separate from model confidence and independent semantic review. A score of 1 never overrides invalid evidence or marks an item as gold; 0 never fails structural validation solely for low confidence. JSONL exports preserve each self-report and its exact elicitation prompt. No calibration metrics or acceptance thresholds are inferred without adjudicated references.

Prepare the confidence variant on the real page without inference:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider meta --model muse-spark-1.3-contributor \
  --prompts concepts-v2-confidence --mode concepts_relations --facsimile off --dry-run
```

Remove `--dry-run` only when intentionally executing the model. Select `concepts-v1.2` alongside `concepts-v2-confidence` for an experiment isolating confidence elicitation from the attribution prompt changes. The offline demonstration accepts `--demo --provider fixture --model synthetic-confidence --prompts concepts-v2-confidence`: its v2 fixture assigns explicit placeholder scores of 0.5, labels every rationale as synthetic, and has no model token usage. These scores do not assess the passage.

For the actor-coverage revision, select `concepts-v2.1-confidence` in the prompt selector or prepare a new run:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider meta --model muse-spark-1.3-contributor \
  --prompts concepts-v2.1-confidence --mode concepts_relations --facsimile on --dry-run
```

The revised prompt contains no page-specific names or expected answers. Existing runs and saved plans retain their original prompts; resuming a plan does not apply the new instructions. `concepts-v1.3` and `concepts-v2.1-confidence` form the corresponding pair for testing confidence elicitation with the reinforced attribution instructions.

## CLI

Every command accepts `--db`, `--summaries-db`, `--indices-db`, `--ocr-versions-db`, `--corpus-root` and `--benchmark-dir`. Use `COMMAND --help` for all arguments.

Prepare a real page without inference:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider ollama --model gemma4:31b \
  --mode concepts_relations --facsimile off --dry-run
```

Run the page with an explicitly chosen model (this command **does call that model**):

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --generation-id 45218 \
  --provider ollama --model gemma4:31b --mode concepts_relations \
  --facsimile auto --vision --num-ctx 32768 --timeout 300
```

The real page example is illustrative, not a claim that this model was installed or tested. Do not use `--generation-id` with multiple pages. A run ID can select the relevant generation per page in a batch.

Run the offline synthetic demonstration:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --demo --provider fixture --model starter-demo --repetitions 2
```

Fixture replay is refused for real corpus pages. It is clearly marked `synthetic_replay`, has no token usage and never implies a successful provider test.

Configure two or more models in a JSON array, for example `.work/kg_playground_v1/models.json`:

```json
[
  {"provider":"ollama","model":"gemma4:31b","vision":true,"num_ctx":32768},
  {"provider":"meta","model":"muse-spark-1.3","api_key_env":"META_API_KEY","reasoning_effort":"high"}
]
```

Then prepare the comparison matrix:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --model-configs .work/kg_playground_v1/models.json \
  --prompts concepts-v1 concepts-v1-fewshot \
  --mode concepts_only concepts_relations --facsimile off auto \
  --concurrency 2 --dry-run
```

The command prints an experiment ID and run IDs. After inspecting them, execute that saved plan with `resume` or the UI:

```bash
python scripts/playgrounds/kg_extractor_playground.py inspect --run 1 --output .work/kg_playground_v1/run1.json
python scripts/playgrounds/kg_extractor_playground.py resume --experiment EXP_ID --concurrency 2
python scripts/playgrounds/kg_extractor_playground.py compare --runs 1 2 --output .work/kg_playground_v1/comparison.json
python scripts/playgrounds/kg_extractor_playground.py validate --run 1
python scripts/playgrounds/kg_extractor_playground.py report --experiment EXP_ID --format markdown
python scripts/playgrounds/kg_extractor_playground.py report --experiment EXP_ID --format csv --output .work/kg_playground_v1/report.csv
python scripts/playgrounds/kg_extractor_playground.py export --experiment EXP_ID --output .work/kg_playground_v1/results.jsonl
```

`EXP_ID`, `1` and `2` above are placeholders; use IDs printed by your commands. Reports also accept `--format json` and configurable `--corpus-pages` (default 288000). JSONL exports include complete cases, prepared requests, raw responses, validation history and reviews. CSV cells beginning with spreadsheet formulas are escaped.

`resume` skips completed/cached/invalid runs. It recovers interrupted executions by adding new runs while retaining the old interrupted records; remote completion/billing before interruption can be unknown. `--retry-errors` explicitly retries transport failures. Invalid JSON/semantics is never retried automatically. Retries are bounded, and successful recovery is not repeated on later resumes. A process lock prevents concurrent execution of the same experiment. The UI admits one active experiment at a time; within it, concurrency is configurable from 1 to 16. No server cancellation UI is provided yet.

## Benchmark loading

The default directory is `.work/kg_benchmark_v1/`, using its actual `selected_pages.jsonl`, generation snapshots and raw OCR snapshots. The importer verifies both content hashes and generation/page identity and does not treat discovery labels or model reviews as gold.

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --benchmark-ids KG-BENCH-000049 --provider ollama --model gemma4:31b --dry-run
```

The browser's benchmark selector filters by collection, work key, page kind and category. On the inspected dataset, normal mode shows **192 pages**, with the **48 blind-holdout pages hidden**. The explicit evaluation checkbox/`--evaluation` unlocks holdout. Normal manual page lookup also blocks holdout volumes, limiting leakage through neighboring pages. Stored holdout cases, runs, reports and exports require evaluation mode in the web API. Turning evaluation mode off clears the prior source/result/prompt views. CLI inspection/export commands are deliberate direct access to locally stored experiments; they are not a prompt-calibration browsing view.

Frozen benchmark snapshots remain loadable if production databases are absent; missing images are represented explicitly. Manual pages require an actual eligible v2 generation and OCR. Legacy summaries are never substituted.

## Providers and capabilities

Capabilities are **explicit model profiles**, not assumptions about every model behind an endpoint. Unsupported settings fail before sending. Unknown configuration keys, URL credentials/query strings and direct secret fields are rejected. API keys are read from the configured environment variable only during execution; authorization headers are never stored. HTTP redirects are refused. Provider error bodies and model responses have the configured secret redacted if echoed.

| Adapter | Protocol / structured output | Optional settings |
|---|---|---|
| `ollama` | Native `/api/chat`; local JSON Schema `format`, explicit JSON mode or prompt-only | Images when `vision=true`; `options.num_ctx`, `num_predict`, optional temperature and seed; `think` only with declared reasoning support. |
| `openai` | `/v1/chat/completions`; strict `response_format.json_schema`, JSON-object or prompt-only | `max_completion_tokens` default; explicit vision/reasoning/temperature capabilities. No unconditional service-tier or sampling knobs. |
| `compatible` | Configurable OpenAI-compatible base URL; JSON-object default, opt into native schema when supported | `max_tokens` default, configurable `max_completion_tokens`; explicit vision/reasoning/temperature. Suitable for vLLM and other compatible servers. |
| `meta` | Documented Meta Model API at `https://api.meta.ai/v1`, Chat Completions, strict schema and `developer` instructions | Muse image input, reasoning effort, `max_completion_tokens`; temperature omitted unless selected. `none` reasoning is rejected; `max` is limited to the configured Muse Spark 1.3 profile. |
| `fixture` | Offline replay of the starter response | Synthetic pages only; no API call. |

Ollama cloud's documented structured-output limitation is handled explicitly: an `ollama.com` endpoint or a cloud model tag uses prompt-only JSON instructions and the same local schema validation. This is recorded in the effective profile, not silently retried with different parameters. [Ollama structured output documentation](https://docs.ollama.com/capabilities/structured-outputs).

OpenAI native schema output and JSON mode are distinct; local validation runs in both cases. Profiles can declare model capabilities without guessing from a model-name prefix. [Official OpenAI structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Meta uses its documented Chat Completions parameter names and role behavior, with schema enforcement and base64 image content. Adapter capabilities alone do not imply successful inference for every profile. Subsequent user executions stored actual responses from `muse-spark-1.3-contributor`, including multimodal runs; see the [pilot results](knowledge_graph/estado_e_resultados_2026-11-01.md#6-o-que-obtivemos-em-pg02671). Sources: [Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), [structured output](https://dev.meta.ai/docs/structured-output), [reasoning](https://dev.meta.ai/docs/reasoning), [image understanding](https://dev.meta.ai/docs/image-understanding), checked 2026-10-31.

Retries apply only to transport failures and transient HTTP statuses (408/429/500/502/503/504), at most four retries. A 400/schema rejection is preserved for inspection. Timeout is per attempt; output/usage is recorded per successful request envelope. No undocumented automatic fallback or infinite repair loop runs.

Usage includes input/output tokens, cached input and reasoning tokens when reported. Costs use configurable USD rates (`input_per_million`, `output_per_million`, optional `cached_input_per_million`); absent rates or usage produce **unknown**, not zero. Reports separate synthetic/cached runs from actual inference and show pricing coverage. Full-corpus cost is an extrapolation over priced page calls, not a budget guarantee.

The editable catalog `tools/knowledge_graph/playground/prices.json` contains these **user-supplied rates recorded on 2026-11-01**, not independently verified provider prices. No prices are fetched from the network:

| Provider / model | Input USD / million | Output USD / million |
|---|---:|---:|
| Meta `muse-spark-1.3-contributor` | 0.10 | 0.20 |
| Meta `muse-spark-1.3` (Standard) | 1.25 | 4.25 |
| Meta `muse-spark-1.2-contributor` | 0.10 | 0.20 |
| Ollama Cloud `gemma4:cloud`, `gemma4:31b-cloud` | 0.14 | 0.40 |
| Ollama Cloud `gpt-oss:20b-cloud` | 0.07 | 0.30 |

The CLI and UI apply matching prices automatically when no explicit `prices` are supplied. Ollama models without a cloud tag receive these rates only at the `ollama.com` endpoint; ordinary local `gemma4:31b` or `gpt-oss:20b` runs remain unpriced. Unknown models remain unpriced. The catalog lists exact aliases; it never matches an arbitrary substring or applies the 20B price to 120B.

The UI suggests registered model names and fills the editable prices field on model/provider/endpoint changes. **Usar tarifa cadastrada** restores the catalog rate. Custom JSON or CLI `--prices rates.json` takes precedence; explicit `{}` disables estimation. A blank UI field uses catalog defaults. Without an explicit cached-input rate, all input tokens use the supplied input price; reasoning is already included in total output and is not added again.

New plans freeze the effective prices and `pricing_reference` (source, catalog version/hash, date) before any inference. Existing plans are never repriced on resume. Older unpriced completed/invalid calls with reported usage receive a **read-time reestimate** in `cost_estimate`, clearly identified as `catalog_reestimate`; stored results, configurations and input hashes are unchanged. These projections follow the current catalog until exported. JSONL exports include the estimate and its exact tariff provenance; Markdown/CSV reports and comparisons use the same estimate. Saved non-null costs, cache replays and synthetic fixtures retain their original values. Edit the catalog and prepare a new plan when tariffs change.

### Output budget and interrupted JSON

New experiments default to **32,768 output tokens** in the CLI, web interface and adapters. Override explicitly with `--max-output-tokens` or the UI field. Existing saved plans retain their original budget when resumed; changing the default does not rewrite historical requests or runs. A model may impose its own maximum or a combined input/output context constraint.

For Meta Chat Completions this becomes `max_completion_tokens`, which includes both internal reasoning and visible output. Omitting `reasoning_effort` still allows model-controlled reasoning. [Meta reasoning/token accounting](https://dev.meta.ai/docs/reasoning), [Chat Completions parameters](https://dev.meta.ai/docs/protocols/chat-completions).

Observed run 6 (`muse-spark-1.3-contributor`) exhausted its old 8,192-token cap: 6,824 reasoning tokens and 1,368 other completion tokens by subtraction. The provider returned `finish_reason=length` and an unfinished JSON string. Increasing the output budget addresses this cutoff; parsing cannot recover the missing response.

The viewer and reports distinguish total output tokens, the included reasoning subset and the configured cap. Reasoning tokens are not charged twice. Visible output is computed by difference only when both counts are reported and consistent; missing breakdowns remain unknown. `kg-validation-3` preserves the provider cutoff diagnostic during CLI/UI revalidation. A response stopped by the output cap is not accepted as a complete extraction even if its received JSON happens to parse. No automatic repair, continuation or repeat inference is performed.

To explicitly run a new experiment with the larger budget:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider meta --model muse-spark-1.3-contributor \
  --mode concepts_relations --facsimile off \
  --max-output-tokens 32768 --timeout 300
```

Add `--dry-run` to inspect/save the request without inference. No reasoning setting is changed automatically.

## Review goal: navigation through Patristics

The intended product is a navigable graph of Patristics. Review should prioritize whether an item helps a reader find and understand relevant passages. Verbatim terminology is not a requirement for concept labels: a familiar explanatory label can be useful even when the source expresses the idea without that technical name. Contextual support matters more than lexical identity.

For manual review, apply these criteria:

| Finding | Treatment for navigation |
|---|---|
| Helpful conceptual paraphrase, translation or broader subject supported by the passage | Accept; use `grounding=inferred` where interpretation supplies the conceptual connection. Absence of the label from the source is not itself an error. |
| Minor terminological imprecision that preserves the subject, participants and direction of the argument | Record as a refinement; it need not disqualify an otherwise useful page graph. |
| Reversed endorsement/refutation, wrong speaker, unrelated concept or invented event | Prioritize correction: these can guide the reader toward the wrong interpretation or passage. |
| Uncertain but plausible contextual connection | Keep the uncertainty visible and revisable; do not silently turn a thematic association into a specific historical assertion. |

For example, an argument about the Son's relation to the Father can support an inferred connection to consubstantiality without that word appearing literally. The connection can aid navigation while preserving the passage's own wording in its quotation and description. This does not require global canonicalization during page extraction.

Assess usefulness using the full current-page context, not just an isolated evidence snippet. If the page supports an item but its attached quote is too narrow, record an evidence-link improvement separately from a conceptual error. Keep source locators and original quotations inspectable. Automatic validation still measures structure and source location; it does not score navigational usefulness. These are manual review criteria, not a new automated acceptance gate or a change to saved prompts, confidence scores or historical runs.

## Persistent corpus campaigns

The corpus runner uses `ProcessPoolExecutor` with the **spawn** start method (1–32 workers). Each worker loads and prepares its own page, opens its own SQLite connections, performs inference and validation, and commits its result. The coordinator submits at most one outstanding job per worker. The existing bounded research matrix executor remains available separately.

The playground database uses **WAL**, a 30-second connection/busy timeout and short write transactions. New tables are `corpus_campaigns`, `corpus_jobs` and `corpus_attempts`; production databases stay read-only. Runs, snapshots, validation and reviews use the existing tables and remain inspectable in the UI. Images are published atomically so parallel workers cannot read a partial archived file.

Create a campaign without inference (one provider/model/prompt/mode per campaign):

```bash
python scripts/playgrounds/kg_extractor_playground.py corpus-create \
  --provider meta --model muse-spark-1.3-contributor \
  --prompt concepts-v2.2-confidence --mode concepts_relations --facsimile on \
  --retries 3 --retry-backoff-base 1 --retry-backoff-max 60 \
  --timeout 300 --name "Patrística v2"
```

Save the returned `EXP-...` identifier. Without collection/volume filters, discovery considers all eligible v2 pages. Optional `--collections PG PL`, `--volumes PG026`, `--page-from 71 --page-to 80` and `--max-pages 10` constrain the campaign. `--max-pages` is a persistent total-page cap, not a per-invocation limit. Holdout volumes are excluded unless the campaign was created with `--evaluation`.

```bash
# Prepare and save up to two actual page inputs, with no model request.
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP_ID --workers 2 --dry-run --max-jobs 2

# Explicit inference action. Drain the queue, or resume the same campaign.
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP_ID --workers 4

# Also discover newly available pages every 60 seconds until stopped.
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP_ID --workers 4 --follow --poll-interval 60

# Retry failed attempts, preserving their original runs and error history.
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP_ID --workers 4 --retry-errors

python scripts/playgrounds/kg_extractor_playground.py corpus-status --campaign EXP_ID
python scripts/playgrounds/kg_extractor_playground.py serve
```

Use the same `--db PATH` for creation, execution, status, export and `serve` if overriding the default database. Source database/corpus paths, provider configuration, prices, prompt text and schema are frozen at creation. Resume uses that saved configuration; create another campaign to change it. Current OCR/image snapshots and exact request inputs are frozen as each job is prepared, not for the entire corpus in advance.

Discovery uses keyset pagination within each volume, reading at most 500 metadata rows per chunk without opening OCR files. This reuses the production index beginning with `documento,status,pagina_num` and avoids repeatedly grouping the entire remaining corpus. It prefers eligible current generations and falls back to the latest eligible shadow; `metadata_pending` remains identifiable in the stored generation metadata. Ambiguous multiple-current pages are reported and skipped. Each campaign stores one job per physical page and pins its selected generation. Repeated discovery adds only previously unseen pages. It does not reprocess a completed page when a newer generation appears, or replace an already selected shadow with a later promotion. A new campaign is the explicit way to study changed generations. Source/output hash changes after discovery fail visibly instead of silently changing the job.

`--dry-run` saves planned runs and leaves jobs `prepared`; a subsequent normal invocation executes those exact plans. `--max-jobs` bounds submissions in this invocation, including preparation failures. Terminal `completed`, `cached` and `invalid` jobs are skipped on resume. `--retry-errors` requeues provider/preparation failures once for that invocation; invalid extractions are not automatically retried. Every attempt records the worker PID, timestamps, run ID and error independently.

To accept a current OCR revision that differs from the OCR used by the summary, use `corpus-create --allow-source-drift`, or enable it on an existing campaign:

```bash
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP_ID --workers 5 --follow --allow-source-drift
```

The override is saved as an append-only `corpus_source_policies` revision; subsequent resumes retain it without repeating the flag. It leaves the original campaign plan and all saved runs/snapshots unchanged. Workers already running must be stopped with Ctrl-C and restarted to load the new policy/code. Newly prepared jobs use current OCR, retain a `SOURCE_CONFLICT` warning when it differs, and record the current OCR hash plus the historical OCR record ID/hash. Those values accompany the existing summary generation provenance. A reference to another physical page and an unverified numerical filename fallback are still rejected.

When enabled, resume automatically requeues only pre-inference jobs blocked by this precise OCR-drift error. Other source/provider errors and invalid/completed runs keep their status; use `--retry-errors` separately if desired. `corpus-run --no-allow-source-drift` saves a strict policy for future preparation. Previously saved inputs remain frozen regardless of later policy changes.

Provider retries use **full jitter** over an exponentially growing window capped by `retry_backoff_max`. An HTTP `Retry-After` delay (seconds or HTTP date) is honored before the jitter delay. If it exceeds `--retry-after-max` (default 300 seconds), the request fails for later inspection/retry instead of retrying early. Retry events and delays are stored in `result.retry_log`. Transient statuses remain 408/429/500/502/503/504; permanent errors and invalid model JSON are not automatically retried. The output budget and normal validation rules are unchanged. Worker count bounds concurrent requests; there is no global requests-per-minute/token-rate limiter.

Ctrl-C or SIGTERM sent to the coordinator stops new submissions and lets in-flight jobs finish. SIGTERM sent to the entire process group can also terminate workers and requires interruption recovery. A filesystem lock prevents two coordinators from executing the same campaign. Worker locks and a coordinator token prevent a resumed coordinator from racing surviving children after a crash. When a saved run is already terminal, recovery reconciles the queue without repeating inference; an interrupted running request gets a new linked run. A provider may have completed/billed a request before a local crash, so recovery cannot promise exactly-once remote billing. Killing the pool does not erase pending jobs; resume after remaining workers exit.

### Monitoring from `serve`

Open **Histórico & relatórios → Campanhas do corpus**. The monitor polls SQLite every five seconds while the tab is visible, including campaigns started in another terminal. It shows coordinator activity, total pages, pending/prepared/running/completed/cached/invalid/error counts, recent pages, attempt history and links to the existing run inspector. The progress bar counts terminal attempts, including failures; it is not a semantic-accuracy score. Starting or resuming a corpus campaign is a CLI action; the monitor does not start inference.

History is paginated in groups of 100 runs, with **Runs anteriores** and **Runs mais recentes**. Campaign reports in the browser summarize queue states. Full JSONL export remains available through the CLI and reads runs in bounded batches:

```bash
python scripts/playgrounds/kg_extractor_playground.py export \
  --experiment EXP_ID --output .work/corpus-export.jsonl
```

Full per-run analytical reports from the older `report` command still collect metrics in memory; use the campaign monitor/status for routine whole-corpus progress. No production graph publishing or global resolution was added.

## Storage, cache and review

Tables: `experiments`, `cases`, `prompt_variants`, `runs`, `run_validation`, `reviews`. WAL and bounded connection timeouts support concurrent workers; each operation owns its connection. Existing unrelated databases and known production paths are refused as experiment storage.

Plans commit atomically; request preparation streams through matrix combinations. Terminal runs are not overwritten. Additional validation and review records append separately. Reviews accept correct, incorrect, partially correct, duplicate, unsupported and uncertain, plus criteria for concept correctness, attribution, stance, missing subjects/relations, granularity and grounding. Choosing “correct” does **not** turn a result into verified gold.

Cache keys cover the case snapshot, exact request body, schema and effective configuration, including image transport, model and prompt. Relevant source/prompt/schema/image/configuration changes invalidate reuse. Repetitions automatically bypass the cache; `--bypass-cache` also forces independent inference. Cached runs preserve origin linkage and original usage separately and record no new inference cost. The current implementation conservatively invalidates on pricing/timeout changes as well.

Validation tiers:

1. JSON parseability (including duplicate-property and non-JSON number rejection), full Draft 2020-12 schema, required fields/enums/extra fields, unique IDs across all object kinds, node endpoints, evidence references and concepts-only mode requirements.
2. Nonempty quotations located in the referenced current OCR block or current segment, existing locators, and schema exclusion of model-supplied document/work/page/bbox metadata. `kg-validation-6` accepts exact or sufficiently similar local passages under `local-quote-v2`, per the revised user requirement. Missing evidence, summary-only grounding and duplicate labels generate review warnings.
3. Human semantic assessment of attribution, endorsement/refutation, concept specificity, redundancy, missing subjects and supported relations. Stance relations automatically receive review questions. Structural/source validity is **not** historical correctness.

Source matching first tries an exact substring, then a unique full-token sequence after case/diacritic/ligature, whitespace, punctuation and line-break hyphenation normalization. The fuzzy fallback uses the existing RapidFuzz dependency to calculate character-level Levenshtein distance over normalized candidate passages. Its score is `1 - distance / max(normalized lengths)`, with a minimum of 0.90. Exact-token anchors retrieve at most 512 starts; passage windows allow two to four tokens of length variation. Queries require at least 3 tokens and 20 non-space normalized characters, and at most 160 tokens / 4096 normalized characters. This is a bounded local search, not an exhaustive corpus or block-wide edit-distance matrix.

Token alignment also guards against misleadingly high whole-passage similarity. At most one fifth of tokens may change (minimum allowance three); individual substitutions require 3–128 character words, at most three edits and at most 40% difference. Short non-protected token insertions/deletions (up to three characters) and unchanged words split/joined by OCR are permitted. Changes involving digits, numeric separators, operators or recognized negations require review, including moved negations. A likely passage blocked by that guard is stored as an unaccepted `review_candidate`, with its original span and normalized diff; source validation remains unsuccessful. Numbers that resemble footnote markers are not automatically discarded. No heuristic here establishes that an omitted number is editorial rather than substantive.

Nearby boundary variants of one passage are grouped; distinct occurrences with score difference < 0.03 remain ambiguous. This is deterministic source location, not translation, semantic equivalence or an entailment check: even a small permitted lexical difference may change meaning and still needs semantic review. The prompts continue to encourage faithful quotations.

Validation stores each match's method, score, source substring and `[start, end)` Unicode code-point offsets in `evidence_matches`; model quotations and source text remain unchanged. `source_valid` means the evidence was located; `source_exact` is the separate strict metric. The UI shows both texts for approximate matches and highlights the actual source span, including supplementary Unicode characters. Reports distinguish located evidence from exact evidence and include validation version and approximate-match counts. Ambiguous passages remain invalid. No search crosses block, segment or page boundaries, or searches cumulative context.

Fuzzy matches additionally store `edit_distance`, `score_metric`, `normalized_quote`, `normalized_source` and edit operations in `diff`, directed from the model quote to the source. Diff offsets refer to those normalized strings, explicitly distinguished from offsets into the original OCR. Inspection/JSONL exports retain the audit. Revalidation appends a new validation record without changing the request, model response, original status or earlier validations, and performs no inference.

The `kg-validation-2` reader accepts one complete outer Markdown fence (`json` or unlabelled), records `markdown_fence_removed` and `format_compliant=false`, and preserves the raw response exactly. It does not extract JSON from surrounding prose, choose between multiple responses, repair invalid JSON, rename IDs or change quotations. All schema/reference/source checks still apply.

To revalidate an existing response **without calling a model**, use the UI's **Revalidar** button or `python scripts/playgrounds/kg_extractor_playground.py validate --run 3`. Validation history is appended; the original run status and original result remain unchanged. The latest validation controls the viewer. Schema-valid outputs with unambiguous graph nodes, endpoints and evidence references can be inspected even when other validation checks fail; the interface labels them as invalid. Duplicate graph IDs, duplicate evidence IDs or ambiguous endpoints still block graph rendering. Reviews of IDs shared by different object categories require qualified targets such as `entities/e1` or `evidence/e1`.

Observed user runs 3 and 4 with `gemma4:cloud` returned fenced JSON. Offline revalidation found entity/evidence ID collisions in both; run 3 also contained three nonliteral quotations under the original strict policy. ID collisions remain invalid under the new source-matching policy. The response schema itself was satisfied by both parsed objects. No further provider calls were made while diagnosing or fixing this case.

Run 8 (`PG026:71`, Muse Spark 1.3 Contributor, facsimile on) finished normally with 19,323 output tokens. Its three failed strict quotes were `άλλος` vs `ἄλλος`, `dixit :` vs `dixit:`, and `fetus` vs `fœtus`. Offline validation v4 locates all 30 quotations: 27 exact, 2 normalized and 1 fuzzy. Its original `invalid` execution status remains in history; the UI separately displays the latest validation. This does not adjudicate the extracted relationships or make the result gold.

Matching in comparisons uses normalized labels **and contextual descriptions/type/mode**, not raw local IDs. Shared quotations or overlapping labels with differing meanings are only uncertain candidates. The app reports unique objects/relations, evidence/grounding differences, warnings and run metrics. No synonym expansion, global IDs or automatic cross-page canonicalization occurs. Precision/recall is not shown: adjudicated reference annotation import/scoring is outside this initial version.

## Tests and demonstrations

The corpus-runner validation passed the 274-test KG suite, followed by focused process/discovery regressions after the per-volume query optimization. Python compilation, JavaScript syntax and Prettier checks also passed. Earlier output-budget validation covered 214 tests (126 playground tests plus 88 existing regressions).

Implementation tests made no live provider requests: adapters used mocked responses and synthetic loopback HTTP servers. Separately, user executions stored real responses from Ollama `gemma4:cloud` and Meta `muse-spark-1.3-contributor`; these pilot observations do not establish general model quality or successful live testing of other provider profiles. Synthetic fixtures cover the starter refutation/quoted adversary, equivalent spellings, identical labels with distinct senses, an editor's voice, and an inferred concept without its modern name. The Latin/Greek fixture prose is constructed test data, not an authenticated quotation.

The later source-drift policy change passed 103 focused tests via Native Lab. These overlap the earlier suites and should not be added as distinct test counts. Dated JUnit evidence and a read-only snapshot of user-run results are indexed in the [consolidated report](knowledge_graph/estado_e_resultados_2026-11-01.md).

```bash
python -m pytest -q \
  tests/test_kg_playground.py tests/test_kg_evidence_validation.py \
  tests/test_kg_provider_adapters.py tests/test_kg_playground_web.py \
  tests/test_kg_benchmark_discovery.py tests/test_ocr_xml_utils.py \
  tests/test_kg_corpus_runner.py \
  tests/test_resumo_v2.py tests/test_resumo_administrative_v2.py \
  tests/test_resumo_classification.py tests/test_resumo_parallel.py
python -m compileall -q tools/knowledge_graph/playground scripts/playgrounds/kg_extractor_playground.py
node --check tools/knowledge_graph/playground/static/playground.js
```

HTTP tests use localhost and can run under Native Lab. Native Lab Playwright was used for browser interaction, console/network checks and desktop/mobile screenshots. This is Python/JavaScript code; native ASan/UBSan compilation is not applicable. No public Astro site changes or public-site build are required.

Ignored evidence is under `.work/kg_playground_v1/`. It includes a synthetic SQLite demonstration, JSONL/review exports, comparison/report artifacts, a real-page dry-run snapshot, browser screenshots and a validation report. The source demo successfully loaded **PG026:71, generation 45218, summary run 268, shadow/valid**, with **1 current segment, 11 OCR blocks, a matching historical OCR record and an available archived facsimile**. Its OCR SHA256 is `6ecc0fc3b0f591cdedfc8c197b198ef0a8ac00aae8a431c88c4949326b13aad7`. This demonstrates source preparation only; it is not a KG result for that real page.

## Current limits

- Local research server for a trusted workstation, not a multiuser production service. It binds to localhost, checks Host/Origin and CSRF headers, uses CSP and renders documentary/model content through text nodes.
- Non-streaming provider calls; no model discovery, server-side semantic judge, global resolver or published graph. Persistent corpus extraction is available as an explicit experimental campaign. Live responses are recorded for the two user-tested model profiles above; other adapters/profiles have only mock/loopback validation in this delivery.
- Browser history is paginated (100 runs per page), with up to 1,000 experiments and 100 campaigns in the selectors. Bounded research plans remain limited to 10,000 runs and 100 repetitions per matrix cell; persistent corpus campaigns have no 10,000-page cap.
- Complete request bodies, OCR snapshots and image archives intentionally consume disk space. Review/export files can be large; none should be committed automatically.
- Character-count token estimates exclude images. Actual provider usage takes precedence. Price estimates omit unknown failed-request billing and cannot predict a heterogeneous full corpus precisely.
- Tolerant OCR boundaries, source metadata conflicts, parser-provided rectangles and semantic interpretations require review. A source-exact quotation can still support a wrong interpretation.
