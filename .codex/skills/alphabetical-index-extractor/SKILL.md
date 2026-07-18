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
- Use the helper examples at `docs/examples/index_target_locator/*.json` as procedural models for helper input, not as volume content to copy.
- Preserve OCR literals. Do not silently normalize uncertain digits, abbreviations, `ibid.`, `seq.`, `seqq.`, `fin`, `*`, or inherited dashes.
- Keep `OCR file`, `editorial page`, and `cited reference` as separate concepts.
- Write the final payload to the output file named in the prompt.
- Return only a tiny JSON acknowledgment in the final assistant message.

## Required Inputs

Expect the driver to provide:

- one `volume_id`
- one `source_root`
- one list of pre-filtered OCR pages or files relevant to alphabetical/final indexes
- one previous alphabetical payload or partial result when it exists, either from the default output path or from an explicit runtime path
- one helper request path and one helper output path
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
7. Analyze helper output conservatively:
   - use it to separate `section_start_file`, `editorial_anchor_file`, and `target_file_best`
   - preserve candidate evidence in `raw_json`
   - do not let helper probabilities override direct editorial evidence without explanation
8. Build the final payload using `references/output-format.md`.
9. If ambiguity is only material, keep one entry and store competing candidates in `raw_json`.
10. If ambiguity changes the editorial structure, emit competing entries and mark the ambiguity explicitly in `raw_json`.
11. Write the complete JSON payload to the runtime output file.

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
- coverage notes and residual uncertainty in `coverage` and `notes`
- when `entries` must stay empty, explicit recovery status in `coverage`

## Completeness Rule

- Do not stop after section detection.
- The payload is incomplete unless the actual entries and references have been serialized.
- Empty `entries` are exceptional. Use them only when the OCR is too damaged to recover line items or the section truly has none.
- Do not invent placeholder entries just to satisfy the schema.
- If `sections` is non-empty and `entries` is empty, set `coverage.entries_status` to either `unrecoverable_ocr` or `no_line_items`, explain why in non-empty `coverage.entries_status_reason`, and list the main evidence files in non-empty `coverage.evidence_files`.
- When leaving a section or entry fragment unresolved, lower confidence and explain why in `raw_json`.

## Ambiguity Rule

- Use one `entry` when the logical entry is stable and only the material locator is disputed.
- Use multiple `entries` when the ambiguity changes segmentation, inherited lemma scope, entry kind, or the set of references.
- The schema allows repeated `lemma_raw`, `lemma_norm`, and `entry_raw`; do not collapse competing hypotheses prematurely.

## References

- Use `references/prompt-contract.md` for the runtime prompt shape and helper invocation contract.
- Use `references/output-format.md` for the canonical output shape.
