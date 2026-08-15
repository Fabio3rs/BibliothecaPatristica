# Prompt Contract

The driver must send one Codex call per volume.
All reference files live under `.codex/skills/patristic-index-extractor/references/`; do not look for them at the repository root.

## Phase ownership

The full pipeline has distinct write boundaries:

1. Prescan, filtered-page localization, workplan construction, editorial-page estimation, and
   helper localization are deterministic driver phases. They write only their named artifacts.
2. Each chunk agent writes exactly one named fragment. It must not edit the workplan, assemble the
   volume, write the canonical payload, reconcile anchors, or access the database for writes.
3. The driver validates and deterministically assembles complete fragments.
4. The final agent reads that assembly, writes the one named canonical payload, and may run only
   non-mutating validation.
5. The driver reconciles anchors, validates payload/fragment/OCR evidence, and imports only after
   every gate succeeds.

An acknowledgment from a chunk or final agent confirms only that its named JSON artifact was
written. It never means that a volume was validated by the driver or imported.

## Required shape

The prompt must be machine-generated and contain these blocks in this order:

1. `TASK`
2. `PHASE OWNERSHIP`
3. `VOLUME`
4. `NUMBERING GLOSSARY`
5. `PRESCAN`
6. `LOCALIZATION ARTIFACTS`
7. `WORK INSTRUCTIONS`
8. `OCR READING`
9. `TODO`
10. `OUTPUT FILE`
11. `FINAL RESPONSE`

## Suggested template

```text
$patristic-index-extractor volume PG001 localizado em teste/PG001/text

TASK
You are extracting the index structure for one OCR volume.

PHASE
You are the final-payload agent. Deterministic discovery, localization, chunk extraction, and
assembly have already produced their named artifacts. Write and validate only the canonical output
JSON. Do not mutate chunk fragments, the workplan, or any database.

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
- Treat an exact or Levenshtein/fuzzy phrase occurrence as additive evidence; a hit in an `ORDO`,
  `ELENCHUS`, catalogue, prefatory inventory, or closing index is not the work target.
- Compare one logical header assembled from all header blocks on a page; numeric tokens may be
  attached to the title text or emitted in separate OCR/XML blocks.
- A similar logical header on at least four files, with up to three missing/corrupt intervening
  headers, is evidence for a probable body range. Inspect the range edges and neighboring files;
  do not assume its first file is the title page.
- Preserve a declared page that belongs to an estimator facing-page pair unless layout or direct
  text evidence identifies the side.
- Adjacent works may share a scan or editorial page. Do not set `end_page = next_start - 1`, infer
  `end_file` from editorial numbers alone, or accept `start_page > end_page`.
- Do not force local anchors for composite containers or external `Vide ... tom.` remissions.
- Scope local searches to the current volume path only. Example: `rg -n -S "STRING" teste/PG001/text`.
- When ligatures may have been flattened by OCR, search both forms inside the same volume. Example: `rg -n -S "GRÆCITATIS|GRAECITATIS" teste/PG001/text`.
- Keep OCR literals.
- Before saving the final JSON, validate referential integrity: every non-null `sections[].work_key` must exactly match one `works[].work_key` from the same payload.
- Run non-mutating validation checks on the written payload and correct reported problems before
  returning the acknowledgment. Validation may read files and the primary database but must not
  modify the database.
- Never initialize, import into, replace, rebuild, or otherwise write to
  `data/patristic_indices.db`. In particular, do not invoke `init_index_db.py`,
  `import_index_json.py`, or `rebuild_index_db_from_payloads.py`; the driver owns those actions.
- Apply the collection-specific taxonomy from `references/volume-taxonomy.md`.
- For each work, inspect the opening pages and the work index before naming the work.
- Do not extract closing alphabetical, analytical, onomastic, scripture, citation, concordance, or
  cross-reference indexes; they belong to the alphabetical-index pipeline.
- Process every `work_anchor_rerun` or nested `anchor_locator_review`; remove it only after direct
  OCR evidence resolves the anchor, otherwise preserve it as ambiguous/unresolved.
- If a number is uncertain, keep the raw literal and lower confidence.

TODO
- [ ] Verify the front index and record its scope.
- [ ] Build one TODO item per discovered work.
- [ ] For each work TODO item, verify the opening pages and the work-level index.
- [ ] Leave closing alphabetical and citation-related indexes to the alphabetical-index pipeline.
- [ ] Resolve or preserve every work-anchor rerun marker.
- [ ] Recheck uncertain page numbers, columns, and OCR digits before finalizing.

OUTPUT FILE
Write the full JSON payload to the output file named in the prompt.
The payload must follow `references/output-format.md`.
Validate the file without importing it. Database import belongs exclusively to the driver after
all driver-side checks pass.
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
- When an existing payload contains `work_anchor_rerun` or `anchor_locator_review`, the driver
  should append a `WORK ANCHOR RERUN` block and require every listed work to be inspected.
- The driver should read and validate the output file from disk, including payload, fragment
  consumption, and OCR-evidence checks, before ingesting it with
  `.codex/skills/patristic-index-extractor/scripts/import_index_json.py` or the compatibility
  wrapper `scripts/import_index_json.py`.
- Only the driver may perform that import. A failed validation must leave the primary database
  unchanged and must not produce an imported completion state.
- For `PO`, the prompt should make clear whether the volume uses tome-level tables, fascicle inventories, or retrospective tables if the prescan already discovered them.
- Until the helper scripts are updated, `PO` prompts may contain incomplete heading detection; the model should treat `PRESCAN` as hints, not as complete coverage.
