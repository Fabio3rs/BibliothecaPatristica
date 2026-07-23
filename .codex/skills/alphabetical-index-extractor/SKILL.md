---
name: alphabetical-index-extractor
description: Extract alphabetical, analytical, onomastic, remissive, and scripture-related index payloads from one OCR volume when the driver already provides pre-filtered candidate pages and runtime paths. Use when Codex must inspect `teste/PG*/text/*`, `teste/PL*/text/*`, or `teste/PO*/text/*`, build helper input for `scripts/index_target_locator.py`, analyze helper evidence, and write the final JSON payload to `data/alphabetical_index_payloads/`.
---

# Alphabetical Index Extractor

## Operating Contract

- Handle one volume per run.
- Treat the prompt's pre-filtered pages as the starting window, not as ground truth.
- Work only inside the current `source_root`.
- Use the alphabetical-index documentation at:
  - `docs/levantamento_indices_alfabeticos.md`
  - `docs/contrato_extrator_indices_alfabeticos.md`
  - `docs/normalizacao_indices_biblicos.md`
  - `docs/estrategia_pipeline_extracao_indices_alfabeticos.md`
  - `docs/duvidas_frequentes_extracao_indices_alfabeticos.md`
- Use the helper examples at `docs/examples/index_target_locator/*.json` as procedural models for helper input, not as volume content to copy.
- Preserve OCR literals. Do not silently normalize uncertain digits, abbreviations, `ibid.`, `seq.`, `seqq.`, `fin`, `*`, or inherited dashes.
- Keep `OCR file`, `editorial page`, and `cited reference` as separate concepts.
- Editorial section titles such as `INDEX SCRIPTURÆ SACRÆ`, `INDICES IN VITAS PATRUM`, `INDEX SCRIPTORUM`, `TABLE ...`, `ORDO ...`, or `CAP. ...` are not biblical book names and must never be written to `book_raw` or `book_norm`.
- Write the final payload to the output file named in the prompt.
- Return only a tiny JSON acknowledgment in the final assistant message.

## Required Inputs

Expect the driver to provide:

- one `volume_id`
- one `source_root`
- one list of pre-filtered OCR pages or files relevant to alphabetical/final indexes
- one previous alphabetical payload or partial result when it exists, either from the default output path or from an explicit runtime path
- one helper request path and one helper output path
- one reusable pipeline-scripts directory
- one per-volume intermediate checkpoint directory
- one final payload output path

The driver fills these paths at runtime. Do not invent alternate paths if the prompt already specifies them.
If the driver had to fall back to an internal heuristic prefilter, treat that filtered-pages block as weaker evidence than an explicitly provided external prefilter.

## Workflow

1. Read the runtime prompt contract in `references/prompt-contract.md`.
2. Read the three repository docs listed above before deciding the payload shape.
3. Inspect the pre-filtered OCR files directly. Confirm which sections are real alphabetical/analytical/onomastic/scripture/concordance sections and which are not.
4. If a previous result exists, reuse it only as a checkpoint. Verify it against OCR before carrying anything forward.
5. Assemble the helper request JSON in the runtime helper-input path using the helper shape documented in `references/prompt-contract.md` and modeled in `docs/examples/index_target_locator/*.json`.
6. Run `scripts/index_target_locator.py` on that helper request JSON.
7. Reuse any valid intermediate JSON fragments already present for the same volume before rebuilding work from scratch.
8. If the volume is large or the payload is complex, you may write reusable helper scripts under `scripts/pipeline_index_extraction/`. Put a short usage comment at the top of each new script.
9. Use divide-and-conquer when the volume is large: break the work into stable chunks such as sections, letter groups, entry slices, or refs/scripture_refs passes, then merge them deliberately.
10. Keep a short local TODO list for the current volume whenever the extraction spans multiple chunks or retries. Use `todo.json` inside the runtime intermediate directory.
11. You may persist partial extraction state under the runtime intermediate directory for the current volume. Prefer assembling the final payload from these intermediates instead of hand-editing a huge JSON blob.
12. Keep intermediate files and TODO-style notes aligned: if you finish one chunk, update the corresponding intermediate artifact before moving on.
13. Analyze helper output conservatively:
   - use it to separate `section_start_file`, `editorial_anchor_file`, and `target_file_best`
   - preserve candidate evidence in `raw_json`
   - do not let helper probabilities override direct editorial evidence without explanation
   - if the helper leaves the case ambiguous or the target file does not clearly confirm the printed page, inspect neighboring OCR files before settling on a partial result
   - when CER or pagination drift weakens the page-number signal, search the current volume with `rg -n -S` and regexes derived from the local editorial pattern
   - treat page drift between printed numbering and OCR file suffixes as normal; do not reject a candidate only because the numbers do not align literally
14. Keep `section_kind` inside the schema enum only. Do not invent narrower ad hoc values.
15. Map headings like `INDEX AUCTORUM VETERUM ...` and `INDEX AUCTORUM RECENTIORUM ...` to `author_index`. Preserve the finer editorial distinction in `heading_raw`, `section_key`, and `raw_json.section_kind_reason`.
16. For scripture-like sections, distinguish editorial headings from inherited biblical book headings:
   - inherit the book only from explicit biblical headings such as `GENESIS.`, `EXODUS.`, `Psal. ...`, `EX PSALMO I.`, or equivalent real book/salmo labels
   - never inherit the book from a section title like `INDEX SCRIPTURÆ SACRÆ.` or `TABLE DES CITATIONS DE LA BIBLE`
   - when a row has only chapter/verse or verse-level data, inherit from the nearest valid biblical heading until the next explicit biblical heading
17. Treat `LOCA EX PSALMIS`, `VARIANTIA IN PSALTERIIS`, and similar verse-apparatus sections as a dedicated scripture-apparatus class:
   - they are not ordinary alphabetical subject indexes
   - they may still produce `scripture_refs`, but the current psalm/book must come from local heading context such as `EX PSALMO I.`
   - preserve the variant/apparatus nature in `raw_json`
18. Titles such as `CAP. LIII. — Index scriptorum ...` are structural section titles, not ordinary lemma lines and not scripture refs by themselves.
19. Build the final payload using `references/output-format.md`.
20. If ambiguity is only material, keep one entry and store competing candidates in `raw_json`.
21. If ambiguity changes the editorial structure, emit competing entries and mark the ambiguity explicitly in `raw_json`.
22. Write the complete JSON payload to the runtime output file.

## Helper Use Rules

- The helper does not decide the final payload.
- The helper exists to expose material candidates and evidence for the agent.
- Preserve at least these helper fields in `raw_json` when they are relevant:
  - `status`
  - `candidate_role`
  - `reason_summary`
  - top candidate list with `file`, `probability`, and main evidence kinds
- When the helper says `ambiguous`, keep the ambiguity explicit unless OCR inspection resolves it.
- If the helper and direct OCR reading disagree, prefer the reading that is best supported by the printed structure and explain the override in `raw_json`.

## What to Record

- every detected alphabetical-index section in `sections`
- heading hierarchy in `nodes` when it exists
- one logical entry per extracted line or grouped fragment in `entries`
- one or more material references per entry in `refs`
- parsed biblical references in `scripture_refs` when present
- `scripture_refs` only when the biblical book context is explicit or safely inherited from a real biblical heading
- coverage notes and residual uncertainty in `coverage` and `notes`
- when `entries` must stay empty, explicit recovery status in `coverage`
- editorial remissions such as `vid.`, `vide`, `voir`, `v.`, `cf.`, or `id.` without a real locator should stay at entry level and should not be emitted as material `refs`
- if a scripture-like line lacks a safe biblical book context, omit the `scripture_ref` rather than inventing `book_raw` / `book_norm`
- reusable and volume-local partial state in the runtime intermediate directory when that materially reduces rework on reruns
- `todo.json` progress notes inside the runtime intermediate directory when they help the agent resume chunked work safely

## Recommended `todo.json` format

When needed, store progress notes at:

- `<intermediate_dir>/todo.json`

Recommended shape:

```json
{
  "volume_id": "PL018",
  "updated_at": "2026-07-19T12:00:00Z",
  "current_focus": "Resolve invalid cross-reference refs and rebuild final payload",
  "completed": [
    "sections recovered",
    "entries chunk A-C validated"
  ],
  "pending": [
    "fix refs[2] cross-reference modeling",
    "rebuild refs.json",
    "assemble final payload and validate"
  ],
  "blocked": [
    "helper remains ambiguous for page drift in one entry"
  ],
  "notes": [
    "Use neighboring OCR files around the candidate target.",
    "Recheck previous failure before rewriting payload."
  ]
}
```

Rules:

- Keep it short and operational.
- Update `updated_at` whenever the file changes.
- Prefer short strings over nested structures.
- Use it as a checkpoint aid only; the canonical extraction data still belongs in the intermediate JSON fragments and final payload.

## Completeness Rule

- Do not stop after section detection.
- The payload is incomplete unless the actual entries and references have been serialized.
- Empty `entries` are exceptional. Use them only when the OCR is too damaged to recover line items or the section truly has none.
- Do not invent placeholder entries just to satisfy the schema.
- If `sections` is non-empty and `entries` is empty, set `coverage.entries_status` to either `unrecoverable_ocr` or `no_line_items`, explain why in non-empty `coverage.entries_status_reason`, and list the main evidence files in non-empty `coverage.evidence_files`.
- When leaving a section or entry fragment unresolved, lower confidence and explain why in `raw_json`.
- Do not use `partial_*` coverage as a shortcut when the current volume still allows neighboring-page checks or regex-based OCR searches that have not been attempted yet.

## Ambiguity Rule

- Use one `entry` when the logical entry is stable and only the material locator is disputed.
- Use multiple `entries` when the ambiguity changes segmentation, inherited lemma scope, entry kind, or the set of references.
- The schema allows repeated `lemma_raw`, `lemma_norm`, and `entry_raw`; do not collapse competing hypotheses prematurely.

## References

- Use `references/prompt-contract.md` for the runtime prompt shape and helper invocation contract.
- Use `references/output-format.md` for the canonical output shape.
