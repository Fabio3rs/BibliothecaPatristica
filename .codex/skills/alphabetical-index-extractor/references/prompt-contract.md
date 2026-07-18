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
7. `WORK INSTRUCTIONS`
8. `OUTPUT FILE`
9. `FINAL RESPONSE`

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

WORK INSTRUCTIONS
- Stay inside the current `source_root`.
- Use the filtered pages as hints, then inspect the OCR directly.
- Build one helper request JSON for the current volume, following the shape used in `../../../docs/examples/index_target_locator/*.json`.
- The helper request should include entries for every alphabetical-index line whose target location needs material resolution.
- Include `expected_manual` only if the driver explicitly provides manual validation data. Otherwise omit it.
- Run: `python scripts/index_target_locator.py --input <helper_request_json> --output <helper_output_json> --pretty`
- Use helper output to support material anchors, not to replace editorial judgment.
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
- The driver should provide the previous result path when rerunning a volume, either from the default output location or from an explicit prior-results directory/file.
- The driver should not ask Codex to discover the entire volume from scratch when pre-filtered index pages already exist.
- If no external filtered-pages JSON exists for a volume and the driver falls back to an internal heuristic prefilter, the prompt should make that explicit so the agent treats it as weaker guidance.
