# Prompt Contract

The driver must send one Codex call per volume.
All reference files live under `.codex/skills/patristic-index-extractor/references/`; do not look for them at the repository root.

## Required shape

The prompt must be machine-generated and contain these blocks in this order:

1. `TASK`
2. `VOLUME`
3. `NUMBERING GLOSSARY`
4. `PRESCAN`
5. `WORK INSTRUCTIONS`
6. `TODO`
7. `OUTPUT FILE`
8. `FINAL RESPONSE`

## Suggested template

```text
$patristic-index-extractor volume PG001 localizado em teste/PG001/text

TASK
You are extracting the index structure for one OCR volume.

VOLUME
- volume_id: PG001
- source_root: teste/PG001/text
- collection: PG

NUMBERING GLOSSARY
- OCR file / file suffix / physical file: the numeric suffix in `...-NNN.txt`; this identifies the OCR text file only.
- Internal/editorial/printed page: the page, folio, or column number printed inside the historical source.
- Scan/sheet reality: one scan may contain two printed pages or facing pages.
- Do not assume these numbering systems are equivalent.

PRESCAN
- file_count: 612
- sample_files:
  - ...
- tail_files:
  - ...
- heading_hits:
  - file_seq: ...
    file: ...
    line: ...
    text: ...

WORK INSTRUCTIONS
- This run is only for the current `volume_id`; every index section, work anchor, `target_file`, grep, and OCR search must stay inside the current `source_root`.
- Never search another volume to resolve an index from the current volume.
- Start from the PRESCAN hints.
- Verify the hinted files directly before deciding anything.
- Search the whole volume if needed, but do not skip the hinted pages.
- Treat printed numbers inside OCR as weak clues only; CER and page wear often corrupt digits.
- Prefer text anchors such as titles, repeated headers, author names, and distinctive opening phrases over numeric literals.
- Scope local searches to the current volume path only. Example: `rg -n -S "STRING" teste/PG001/text`.
- When ligatures may have been flattened by OCR, search both forms inside the same volume. Example: `rg -n -S "GRÆCITATIS|GRAECITATIS" teste/PG001/text`.
- Keep OCR literals.
- Before saving the final JSON, validate referential integrity: every non-null `sections[].work_key` must exactly match one `works[].work_key` from the same payload.
- Apply the collection-specific taxonomy from `references/volume-taxonomy.md`.
- For each work, inspect the opening pages and the work index before naming the work.
- Inspect the closing pages for final indexes.
- If a number is uncertain, keep the raw literal and lower confidence.

TODO
- [ ] Verify the front index and record its scope.
- [ ] Build one TODO item per discovered work.
- [ ] For each work TODO item, verify the opening pages and the work-level index.
- [ ] Verify all closing indexes at the end of the volume.
- [ ] Recheck uncertain page numbers, columns, and OCR digits before finalizing.

OUTPUT FILE
Write the full JSON payload to the output file named in the prompt.
The payload must follow `references/output-format.md`.
Return a tiny JSON acknowledgment only.

FINAL RESPONSE
{"status":"ok","volume_id":"PG001","written_file":"data/index_payloads/PG001_indices.json"}
```

## Driver rule

- The driver should pre-scan the volume directory before calling Codex.
- The driver should pass the pre-scan result into `PRESCAN`.
- The driver should describe filename-derived locators in `PRESCAN` as OCR files or file suffixes, not as editorial pages.
- The driver should not ask the model to discover the obvious file structure from scratch.
- The driver should not send a generic prompt without the pre-scan block.
- The driver should read the output file from disk and ingest it with `.codex/skills/patristic-index-extractor/scripts/import_index_json.py` or the compatibility wrapper `scripts/import_index_json.py`.
- For `PO`, the prompt should make clear whether the volume uses tome-level tables, fascicle inventories, or retrospective tables if the prescan already discovered them.
- Until the helper scripts are updated, `PO` prompts may contain incomplete heading detection; the model should treat `PRESCAN` as hints, not as complete coverage.
