# Output Format
This is the canonical schema. The payload must use the structured `volume`, `works`, `sections`, and `notes` layout defined below.

The agent writes one JSON file per volume.

## File name

- Pattern: `data/index_payloads/<VOLUME>_indices.json`
- Examples:
  - `data/index_payloads/PG001_indices.json`
  - `data/index_payloads/PL099_indices.json`
  - `data/index_payloads/PO025_indices.json`

## Payload shape

The file must contain exactly one JSON object with these top-level keys:

- `volume`
- `works`
- `sections`
- `notes`

## `volume`

Required fields:

- `volume_id`
- `collection`
- `source_root`
- `volume_label`

Optional fields:

- `notes`
- `started_at`
- `finished_at`
- `scan_summary`

Notes:

- `notes` may be a list of short strings or a single string; the importer will serialize it for SQLite storage.

## `works`

Each work is one object with:

- `work_key`
- Keys must be globally unique in SQLite; if the semantic label is generic, prefix with `volume_id`.
- `work_order`
- `author_raw`
- `title_raw`
- `title_norm`
- `start_page`
- `end_page`
- `start_file`
- `end_file`
- `source_section_key`
- `confidence`
- `raw_json`

Semantics:

- `start_page` and `end_page` are editorial/internal references from the printed source.
- `start_file` and `end_file` are physical OCR file locators.
- Do not treat `start_page`/`end_page` as OCR file suffixes.

## `sections`

Each section is one object with:

- `section_key`
- `work_key`
- `section_key` must be globally unique in SQLite; if the semantic label is generic, prefix with `volume_id`.
- `scope_kind`
- `index_kind`
- `heading_raw`
- `heading_norm`
- `page_start`
- `page_end`
- `file_start`
- `file_end`
- `confidence`
- `raw_json`
- `entries`

Semantics:

- `page_start` and `page_end` are editorial/internal references for the section span.
- `file_start` and `file_end` are physical OCR file locators for the files where the section is attested.
- The same printed section may span multiple printed pages inside one scan or OCR file; keep the editorial span and the physical file span conceptually separate.
- `page_start`/`page_end` may be `null` when the section page itself has no trustworthy printed/editorial number in OCR, as long as `file_start`/`file_end` anchor the section physically.
- If `work_key` is not `null`, it must exactly match one `works[].work_key` from the same payload.
- Do not use a heading label, thematic alias, or shortened title as `sections[].work_key` unless that exact key is also the canonical `work_key` of a work in `works[]`.

## `entries`

Each entry is one object with:

- `entry_order`
- `entry_raw`
- `target_raw`
- `target_file`
- `page_ref_raw`
- `page_ref_int`
- `page_ref_col`
- `note_raw`
- `normalized_target`
- `confidence`
- `raw_json`

Semantics:

- `target_file` is the physical OCR file judged to contain the indexed target when it can be located.
- `page_ref_raw`, `page_ref_int`, and `page_ref_col` are printed/editorial references copied from the source index line.
- `page_ref_int` is not the OCR file suffix.

## Required semantics

- `scope_kind` must reflect the structural role of the section in its collection.
- For `PG` and `PL`, use:
  - `volume_front`
  - `work_front`
  - `volume_end`
- For `PO`, use the collection-specific classes when needed:
  - `volume_title`
  - `volume_table`
  - `fascicle_inventory`
  - `work_front_matter`
  - `work_internal_table`
  - `work_index_nominal`
  - `work_index_scripture`
  - `work_index_alphabetical`
  - `work_index_analytic`
  - `editorial_closure`
  - `retrospective_table`
- `index_kind` must reflect the visible heading, for example `ELENCHUS`, `INDEX CAPITUM`, `ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, `INDEX GRÆCITATIS`, `TABLE DES MATIÈRES`, `TABLE DES NOMS PROPRES`, `TABLE ANALYTIQUE DES MATIÈRES`, or `INDEX DES CITATIONS DES ÉCRITURES`.
- `target_file` must point to the OCR text file for the referenced page when one can be identified.
- `target_file` should be chosen from direct local OCR evidence, not from page numbers alone.
- The numeric suffix in `...-NNN.txt` is a physical OCR locator, not a printed page number.
- Printed/internal numbers may be corrupted by OCR CER or scan wear; keep them as references, but prefer title/header/body-text evidence when anchoring a physical OCR file.
- One scan may represent two printed pages, facing pages, or other non-1:1 layouts; preserve editorial references separately from physical file mapping.
- Keep OCR literals in `entry_raw`, `heading_raw`, and reference fields.
- Use `raw_json` to preserve any extra evidence that is useful for later review.
- For `PO`, use `raw_json` to preserve extra structural evidence such as `FASC.` labels, cross-tome mentions, bracket pagination, and parallel page numbering.
- `entries` is required for every section and must contain the section's actual line items whenever they can be read from OCR.
- Do not replace line items with high-level summaries such as `entries_summary`.
- Leave `entries` empty only when the section truly contains no list items, or when OCR quality prevents reliable line extraction; in that case explain the reason in `raw_json`.
- For uncertain file mapping, keep the original numeric literal, lower `confidence`, and store the string-matching evidence in `raw_json`.
