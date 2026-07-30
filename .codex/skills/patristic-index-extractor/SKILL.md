---
name: patristic-index-extractor
description: Extract structured indices from a single patrística OCR volume (`teste/PG*/text/*`, `teste/PL*/text/*`, or `teste/PO*/text/*`), classify the editorial index structure for that collection, and persist it in SQLite under `data/`. Use when Codex is invoked one volume at a time to inspect OCR pages, tolerate imperfect numbering, and write the parsed index structure to a database.
---

# Patristic Index Extractor

## Pipeline boundary

This is the opening/general works-index pipeline. Start at the physical beginning of the volume and
move forward through tables of works, fascicles, books, parts, chapters, capitula, and work-level
contents. Closing alphabetical, analytical, onomastic, scripture, and citation indexes belong to
the separate alphabetical-index pipeline.

When the driver provides a workplan, process pending chunks with
`scripts/run_index_extraction_chunks.py --workplan <path>`. Each chunk runs in a fresh ephemeral
Codex process; validated fragment JSON is the continuation state. Read exact validator feedback
attached to an existing fragment before the first attempt of a rerun.

## Operating Contract

- Handle one volume per run. The driver already launches one Codex instance per volume.
- Work only inside `teste/<VOLUME>/text/*`.
- Read the opening pages of the volume and the opening/front matter of each work.
- Transcribe the actual index lines into `entries`; do not stop at section detection alone.
- Preserve OCR literals. Do not silently normalize uncertain digits, Roman numerals, or `Ibid.` references.
- Use the taxonomy in `references/volume-taxonomy.md`.
- Use `../../../docs/taxonomia_indices.md` when `collection` is `PG` or `PL`.
- Use `../../../docs/taxonomia_indices_po.md` when `collection` is `PO`.
- Accept a machine-generated prompt envelope with `TASK`, `VOLUME`, `PRESCAN`, `WORK INSTRUCTIONS`, `TODO`, `OUTPUT FILE`, and `FINAL RESPONSE` sections.
- Maintain an explicit TODO list during extraction: volume-level structures and one checkpoint per
  discovered work, fascicle, book, part, or chapter entrypoint.
- Do not finalize the volume until every TODO item has been verified against OCR files.
- Treat the `PRESCAN` section as the starting map, not as ground truth; inspect the files it names before finalizing the result.
- Write the full extraction payload to the output file named in the `OUTPUT FILE` section.
- Return only a tiny JSON acknowledgment in the final assistant message.
- If `collection` is `PO`, distinguish the tome table from work-level tables and from retrospective tables that list other tomes.
- The current helper scripts are still tuned for `PG/PL` headings. If the prompt came from those scripts, treat the `PRESCAN` as partial and correct it from direct inspection before finalizing a `PO` volume.

## Required Glossary

- `OCR file` / `physical OCR file` / `file suffix`: the physical text file in `teste/<VOLUME>/text/*.txt`, identified by the numeric suffix in `...-NNN.txt`.
- `internal page` / `editorial page` / `printed page`: the page, column, folio, or other numbering printed inside the historical source and cited by index entries.
- `scan` / `sheet reality`: the visual capture behind the OCR. One scan may contain two printed pages, facing pages, split columns, or other non-1:1 layouts.

Use these meanings consistently. Do not collapse them into one numbering system.

## Workflow

1. Initialize or reuse `data/patristic_indices.db` with `.codex/skills/patristic-index-extractor/scripts/init_index_db.py` or the compatibility wrapper `scripts/init_index_db.py`.
2. Run `.codex/skills/patristic-index-extractor/scripts/scan_volume.py` on the target volume to locate candidate index pages and headings, or use the compatibility wrapper `scripts/scan_volume.py`.
3. Use `.codex/skills/patristic-index-extractor/scripts/build_volume_prompt.py` to turn the scan output into the prompt envelope passed to `codex exec`, or use the compatibility wrapper `scripts/build_volume_prompt.py`.
4. Classify volume-level tables of works and work-level contents according to the collection-specific taxonomy.
5. Build or update a TODO list as you discover works; keep one checked item per work and separate
   items for its front matter, books, parts, or chapters.
6. For each work, read its opening pages to capture the work index and its pagination, then mark that TODO item complete.
7. For `PO`, identify fascicle inventory pages, internal work tables, and retrospective tables before recording final sections.
8. Do not extract closing alphabetical, analytical, onomastic, scripture, citation, concordance,
   names, subjects, or cross-reference indexes; the alphabetical-index pipeline owns them.
9. Assemble a JSON payload, write it to the requested output file, and import it with `.codex/skills/patristic-index-extractor/scripts/import_index_json.py` or the compatibility wrapper `scripts/import_index_json.py`.

## What to Record

- `volume_index`: the front-matter index or equivalent volume-level structure for the whole volume.
- `works`: one record per work identified in the volume.
- `sections`: opening volume tables and work-level tables of books, parts, chapters, or contents.
- `entries`: the lines inside each index section.
- `confidence`: low when OCR digits or headings are uncertain.
- For `PO`, record fascicle-level structures and retrospective tables as sections when they are editorially meaningful.

## Entry Completeness Rule

- Every verified index/table section must include its line-by-line `entries`.
- Do not leave `entries` empty just because the section is long, dense, repetitive, or time-consuming.
- An empty `entries` array is allowed only when the section truly has no line items, or when the OCR page is unreadable enough that individual entries cannot be recovered.
- When `entries` is empty, explain the reason in `raw_json` and lower `confidence`.
- Do not summarize a long table with `entries_summary` in place of actual `entries`.

## Number Handling

- Treat the OCR file suffix as technical metadata only.
- Distinguish file suffix, printed page number, in-entry references, and scan layout reality.
- Never infer an internal/editorial page number from the OCR file suffix alone.
- Never infer the OCR file suffix from an internal/editorial page number alone.
- `target_file`, `start_file`, `end_file`, `file_start`, and `file_end` are physical OCR locators.
- `start_page`, `end_page`, `page_start`, `page_end`, `page_ref_raw`, `page_ref_int`, and `page_ref_col` are editorial/internal references, not physical file ids.
- If one scan contains two printed pages or facing pages, preserve the printed references as editorial data and use string evidence to locate the physical OCR file.
- Printed/internal numbers may suffer OCR CER because of faded ink, page wear, bleed-through, cropping, or scan defects; treat them as weak clues, not as primary anchors.
- Editorial pagination commonly appears as `NUMBER  PAGE-TITLE  NUMBER+1` in a facing-page
  header. Depending on the OCR generator, it may occupy one block, be split across multiple blocks,
  retain only one number, contain CER-corrupted digits, or be absent.
- When the header is partial, corrupt, or absent, inspect several physical OCR files before and
  after and infer only from a consistent local editorial sequence. Never fill the gap from the
  physical filename suffix.
- Keep `col.`, page numbers, Roman numerals, and suspicious digits verbatim.
- When OCR is ambiguous, store the raw text and mark the record uncertain instead of correcting it.
- Do not trust printed page numbers or OCR page numbers as the primary locator for the target file.
- Use page numbers only as weak hints to narrow the search.

## Deep Local Search Rule

- After finding an index heading, continue with local search until you locate the real OCR files for the indexed work/section.
- Search in multiple steps across the whole volume, not only around the prescan pages.
- Prefer title strings, author names, distinctive chapter headings, and repeated phrases over numeric references.
- Use numeric clues only as secondary evidence because CER, page wear, and scan defects can corrupt printed digits inside OCR.
- When a target is not obvious, try several queries: full title, shortened title, normalized title, author plus title token, and nearby heading phrases.
- Record the evidence of the located files in `raw_json`, including the strings that matched and any uncertainty about numbering.

## Output Contract

- Write to `data/patristic_indices.db`.
- Keep the raw JSON for each section and entry.
- Prefer stable keys (`work_key`, `section_key`) so reruns can replace rows cleanly.
- `work_key` and `section_key` must be unique within the whole database, not only within one payload; prefix them with the current `volume_id` whenever the natural label is generic.
- If the volume is rerun, replace the previous rows for that same `volume_id`.
- The final payload file should be JSON only, with top-level keys `volume`, `works`, `sections`, and `notes`.
- The final assistant message should be JSON only, with keys `status`, `volume_id`, and `written_file`.
