# Prompt Contract

The driver should send one Codex call per volume.
All references for this skill live under `.codex/skills/alphabetical-index-extractor/` unless the file path below explicitly points to the repository root.

## Repository documents to read first

- `../../../docs/levantamento_indices_alfabeticos.md`
- `../../../docs/contrato_extrator_indices_alfabeticos.md`
- `../../../docs/normalizacao_indices_biblicos.md`

## Expected prompt shape

The runtime prompt should be machine-generated and contain these blocks in this order:

1. `TASK`
2. `VOLUME`
3. `NUMBERING GLOSSARY`
4. `FILTERED PAGES`
5. `PREVIOUS RESULT`
6. `HELPER PATHS`
7. `WORKSPACE PATHS`
8. `WORK INSTRUCTIONS`
9. `OUTPUT FILE`
10. `FINAL RESPONSE`

## Suggested template

```text
$alphabetical-index-extractor volume PG003 located at teste/PG003/text

TASK
You are building the alphabetical-index payload for one OCR volume.

VOLUME
- volume_id: PG003
- source_root: teste/PG003/text
- collection: PG

NUMBERING GLOSSARY
- OCR file / physical OCR file / file suffix: the numeric suffix in `...-NNN.txt`; this identifies the OCR text file only.
- Editorial page / printed page: the number printed in the source and cited by the index.
- Cited reference: the number, column, line, or range printed inside the index entry.
- Do not collapse these numbering systems.

FILTERED PAGES
- candidate_files:
  - teste/PG003/text/...
- candidate_sections:
  - heading: ...
    file: ...
    reason: ...

PREVIOUS RESULT
- path: data/alphabetical_index_payloads/PG003_alphabetical_indices.json
- status: absent
- source: default_output
```

If a previous result exists:

- read it
- treat it as a checkpoint only
- verify every reused section and entry against OCR

Continue the template:

```text
HELPER PATHS
- helper_request_json: /tmp/...
- helper_output_json: /tmp/...

WORKSPACE PATHS
- pipeline_scripts_dir: scripts/pipeline_index_extraction
- intermediate_dir: data/intermediate_payloads/PG003
- intermediate_files:
  - manifest.json
  - entries.json
  - refs.json

WORK INSTRUCTIONS
- Stay inside the current `source_root`.
- Use the filtered pages as hints, then inspect the OCR directly.
- You may write reusable helper scripts under `scripts/pipeline_index_extraction/` and execute them during the run.
- Each new helper script must start with a short top-of-file usage comment.
- You may persist partial work in `data/intermediate_payloads/<VOLUME>/` and reuse it on reruns.
- Prefer assembling the final payload from intermediate JSON fragments when the volume is large, instead of manually rewriting a very large JSON file.
- Use divide-and-conquer when helpful: split the volume into stable chunks such as sections, letter groups, entry ranges, or reference passes, then merge the results deliberately.
- Keep a short TODO list in the intermediate workspace when the run spans multiple chunks, retries, or unresolved ambiguities.
- Use the standard path `<intermediate_dir>/todo.json`.
- Use this shape for `todo.json`: `volume_id`, `updated_at`, `current_focus`, `completed`, `pending`, `blocked`, `notes`.
- Update intermediate artifacts as each chunk is completed so the next retry can resume from real saved state instead of memory.
- Keep `section_kind` inside the importer enum only; do not invent narrower ad hoc values such as `onomastic_veterum` or `onomastic_recentiorum`.
- Headings like `INDEX AUCTORUM VETERUM ...` and `INDEX AUCTORUM RECENTIORUM ...` should map to `author_index`; preserve the finer distinction in `heading_raw`, `section_key`, and `raw_json.section_kind_reason`.
- Build one helper request JSON for the current volume, following the shape used in `../../../docs/examples/index_target_locator/*.json`.
- The helper request should include entries for every alphabetical-index line whose target location needs material resolution.
- Include `expected_manual` only if the driver explicitly provides manual validation data. Otherwise omit it.
- Run: `python scripts/index_target_locator.py --input <helper_request_json> --output <helper_output_json> --pretty`
- Use helper output to support material anchors, not to replace editorial judgment.
- If the helper stays ambiguous or the printed page does not align cleanly with the OCR file suffix, inspect neighboring OCR files before leaving the case unresolved.
- Treat page drift and CER noise as expected. The printed page number, the cited reference, and the OCR file suffix may legitimately disagree.
- When the local page-number signal is weak, run local OCR searches in the current volume with `rg -n -S` and regexes derived from the actual pattern you see in the volume.
- Good target patterns include forms such as `450_3-5`, `55₁₀`, `464 n. 1`, `37 à 39`, `174, 175`, and inherited `—` continuation lines.
- Bare editorial remissions such as `vid.`, `vide`, `voir`, `v.`, `cf.`, or `id.` without a page, range, line, or target locator should not be emitted as `refs`.
- Those remissions belong in the entry semantics, usually as `entry_kind: cross_reference`, or should remain preserved in `entry_raw` / `raw_json`.
- Before declaring a section or entry partial, try direct OCR inspection, at least one neighboring-page check, and at least one volume-specific search pattern.
- If those attempts still fail, preserve the attempted searches or page-window checks in `raw_json`.
- Before writing the final payload, make sure every `entry_key`, `section_key`, `node_key`, and relationship field is internally consistent.
- Avoid empty `entries` whenever real line items can be recovered.
- If `sections` is non-empty and `entries` must remain empty, do not invent filler rows. Instead set `coverage.entries_status` to `unrecoverable_ocr` or `no_line_items`, add a non-empty `coverage.entries_status_reason`, and include non-empty `coverage.evidence_files`.

OUTPUT FILE
Write the full JSON payload to the output file named in the prompt.
The payload must follow `references/output-format.md`.
Return a tiny JSON acknowledgment only.

FINAL RESPONSE
{"status":"ok","volume_id":"PG003","written_file":"data/alphabetical_index_payloads/PG003_alphabetical_indices.json"}
```

## Helper request contract

The helper input is one JSON object per volume with:

- `volume_id`
- `source_root`
- `options`
- `entries`

Each helper entry should use the documented shape from `../../../docs/examples/index_target_locator/*.json`, especially:

- `entry_id`
- `lemma_raw`
- `query_names`
- `page_hints`
- `page_hint_ints`
- `context_raw`

Use the helper only for entries where material location matters. Do not create helper requests for headings that have no locator problem.

## Driver rules

- The driver should pre-filter candidate index pages before calling Codex.
- The driver should provide explicit runtime paths for helper input, helper output, and final payload output.
- The driver should provide explicit runtime paths for the reusable pipeline-scripts directory and the per-volume intermediate checkpoint directory.
- The driver should provide the previous result path when rerunning a volume, either from the default output location or from an explicit prior-results directory/file.
- On reruns, the driver should surface existing intermediate files for the same volume so the agent can reuse them instead of rebuilding the payload from scratch.
- The driver should not ask Codex to discover the entire volume from scratch when pre-filtered index pages already exist.
- If no external filtered-pages JSON exists for a volume and the driver falls back to an internal heuristic prefilter, the prompt should make that explicit so the agent treats it as weaker guidance.
