# Prompt Contract

The driver must send one Codex call per volume.
All reference files live under `.codex/skills/patristic-index-extractor/references/`; do not look for them at the repository root.

## Required shape

The prompt must be machine-generated and contain these blocks in this order:

1. `TASK`
2. `VOLUME`
3. `PRESCAN`
4. `WORK INSTRUCTIONS`
5. `TODO`
6. `OUTPUT FILE`
7. `FINAL RESPONSE`

## Suggested template

```text
$patristic-index-extractor volume PG001 localizado em teste/PG001/text

TASK
You are extracting the index structure for one OCR volume.

VOLUME
- volume_id: PG001
- source_root: teste/PG001/text
- collection: PG

PRESCAN
- file_count: 612
- sample_files:
  - ...
- tail_files:
  - ...
- heading_hits:
  - file: ...
    line: ...
    text: ...

WORK INSTRUCTIONS
- Start from the PRESCAN hints.
- Verify the hinted files directly before deciding anything.
- Search the whole volume if needed, but do not skip the hinted pages.
- Keep OCR literals.
- Separate volume-front, work-front, and volume-end indexes.
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
- The driver should not ask the model to discover the obvious file structure from scratch.
- The driver should not send a generic prompt without the pre-scan block.
- The driver should read the output file from disk and ingest it with `scripts/import_index_json.py`.
