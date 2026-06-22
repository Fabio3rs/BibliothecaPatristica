# Output Format
This is the canonical schema. The payload must use the structured `volume`, `works`, `sections`, and `notes` layout defined below.

The agent writes one JSON file per volume.

## File name

- Pattern: `data/index_payloads/<VOLUME>_indices.json`
- Examples:
  - `data/index_payloads/PG001_indices.json`
  - `data/index_payloads/PL099_indices.json`

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

## `sections`

Each section is one object with:

- `section_key`
- `work_key`
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

## Required semantics

- `scope_kind` must be one of `volume_front`, `work_front`, or `volume_end`.
- `index_kind` must reflect the visible heading, for example `ELENCHUS`, `INDEX CAPITUM`, `ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, or `INDEX GRÆCITATIS`.
- `target_file` must point to the OCR text file for the referenced page when one can be identified.
- Keep OCR literals in `entry_raw`, `heading_raw`, and reference fields.
- Use `raw_json` to preserve any extra evidence that is useful for later review.
