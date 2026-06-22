---
name: patristic-index-extractor
description: Extract structured indices from a single PG/PL OCR volume (`teste/PG*/text/*` or `teste/PL*/text/*`), locate the volume-level index, work-level indexes, and final-volume indexes, and persist them in SQLite under `data/`. Use when Codex is invoked one volume at a time to inspect OCR pages, tolerate imperfect numbering, and write the parsed index structure to a database.
---

# Patristic Index Extractor

## Operating Contract

- Handle one volume per run. The driver already launches one Codex instance per volume.
- Work only inside `teste/<VOLUME>/text/*`.
- Read the opening pages, the opening of each work, and the closing pages of the volume.
- Preserve OCR literals. Do not silently normalize uncertain digits, Roman numerals, or `Ibid.` references.
- Use the taxonomy in `references/volume-taxonomy.md` and the repository note `../../../docs/taxonomia_indices.md` to classify what you found.
- Accept a machine-generated prompt envelope with `TASK`, `VOLUME`, `PRESCAN`, `WORK INSTRUCTIONS`, `TODO`, `OUTPUT FILE`, and `FINAL RESPONSE` sections.
- Maintain an explicit TODO list during extraction: front index, one checkpoint per discovered work, and closing indexes.
- Do not finalize the volume until every TODO item has been verified against OCR files.
- Treat the `PRESCAN` section as the starting map, not as ground truth; inspect the files it names before finalizing the result.
- Write the full extraction payload to the output file named in the `OUTPUT FILE` section.
- Return only a tiny JSON acknowledgment in the final assistant message.

## Workflow

1. Initialize or reuse `data/patristic_indices.db` with `scripts/init_index_db.py`.
2. Run `scripts/scan_volume.py` on the target volume to locate candidate index pages and headings.
3. Use `scripts/build_volume_prompt.py` to turn the scan output into the prompt envelope passed to `codex exec`.
4. Classify the volume-level index, work-level indexes, and closing indexes.
5. Build or update a TODO list as you discover works; keep one checked item per work and separate items for front and end matter.
6. For each work, read its opening pages to capture the work index and its pagination, then mark that TODO item complete.
7. Read the end of the volume to capture any final analytic or editorial indexes, then complete the closing TODO items.
8. Assemble a JSON payload, write it to the requested output file, and import it with `scripts/import_index_json.py`.

## What to Record

- `volume_index`: the front-matter index for the whole volume.
- `works`: one record per work identified in the volume.
- `sections`: every index section, including work-level and end-of-volume sections.
- `entries`: the lines inside each index section.
- `confidence`: low when OCR digits or headings are uncertain.

## Number Handling

- Treat the OCR file suffix as technical metadata only.
- Distinguish file suffix, printed page number, and in-entry references.
- Keep `col.`, page numbers, Roman numerals, and suspicious digits verbatim.
- When OCR is ambiguous, store the raw text and mark the record uncertain instead of correcting it.

## Output Contract

- Write to `data/patristic_indices.db`.
- Keep the raw JSON for each section and entry.
- Prefer stable keys (`work_key`, `section_key`) so reruns can replace rows cleanly.
- If the volume is rerun, replace the previous rows for that same `volume_id`.
- The final payload file should be JSON only, with top-level keys `volume`, `works`, `sections`, and `notes`.
- The final assistant message should be JSON only, with keys `status`, `volume_id`, and `written_file`.
