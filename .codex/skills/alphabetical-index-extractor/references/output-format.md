# Output Format

This skill writes one JSON payload per volume.
It may also use per-volume intermediate JSON fragments during assembly, but only the final payload is canonical for validation/import.

## File name

- Directory: `data/alphabetical_index_payloads/`
- Recommended pattern: `<VOLUME>_alphabetical_indices.json`

The runtime prompt may pass a different exact file path. Use the runtime output path if it is provided.

## Intermediate fragments

- Recommended directory: `data/intermediate_payloads/<VOLUME>/`
- Recommended fragments: `volume.json`, `sections.json`, `nodes.json`, `entries.json`, `refs.json`, `scripture_refs.json`, `coverage.json`, `notes.json`, `manifest.json`
- Recommended progress note file: `todo.json`
- These files are checkpoints only. They do not replace the canonical final payload and are not imported directly into SQLite.

## Canonical payload shape

The file must contain exactly one JSON object with these top-level keys:

- `schema_version`
- `generated_at`
- `volume`
- `sections`
- `nodes`
- `entries`
- `refs`
- `scripture_refs`
- `coverage`
- `notes`

This shape follows `../../../docs/contrato_extrator_indices_alfabeticos.md`.

## `volume`

Required fields:

- `volume_id`
- `collection`
- `source_root`
- `volume_label`

Optional fields:

- `notes`

## `sections`

Each section should contain:

- `section_key`
- `volume_id`
- `work_key`
- `section_order`
- `section_kind`
- `heading_raw`
- `heading_norm`
- `heading_letter`
- `page_start`
- `page_end`
- `file_start`
- `file_end`
- `confidence`
- `raw_json`

## `nodes`

Use `nodes` for hierarchy above the entry level, such as:

- grouping letters
- macro-headings
- subgroup headings
- editorial rubrics

Each node should contain:

- `node_key`
- `section_key`
- `parent_node_key`
- `node_order`
- `node_kind`
- `label_raw`
- `label_norm`
- `label_sort`
- `node_level`
- `confidence`
- `raw_json`

## `entries`

Each entry should contain:

- `entry_key`
- `section_key`
- `parent_node_key`
- `entry_order`
- `entry_kind`
- `lemma_raw`
- `lemma_display`
- `lemma_norm`
- `lemma_sort`
- `entry_raw`
- `context_raw`
- `heading_letter`
- `inferred_printed_page`
- `section_start_file`
- `editorial_anchor_file`
- `target_file_best`
- `confidence`
- `raw_json`

Rules:

- Keep OCR literals in `lemma_raw` and `entry_raw`.
- `lemma_*` fields may be null for editorial notes, cross-references, or unresolved fragments.
- The schema allows more than one entry for the same ambiguous OCR zone when the competing hypotheses are editorially distinct.

## `refs`

Each material reference should contain:

- `entry_key`
- `ref_order`
- `ref_kind`
- `ref_raw`
- `page_ref_raw`
- `page_ref_int`
- `page_ref_col`
- `line_ref_raw`
- `range_start_raw`
- `range_end_raw`
- `target_file`
- `target_file_probability`
- `section_start_file`
- `editorial_anchor_file`
- `confidence`
- `raw_json`

Rules:

- Do not collapse many references into one textual blob.
- Keep `ref_raw` exactly as printed when possible.
- Preserve helper evidence in `raw_json` when it influenced locator decisions.
- `ref_order` must be unique within each `entry_key`.
- Prefer sequential `ref_order` values starting at `1` within each `entry_key`.
- Do not emit the same material reference twice for the same `entry_key`.

## `scripture_refs`

Use this array only when the index contains biblical references that can be parsed separately from material/editorial references.

Each scripture ref should contain:

- `entry_key`
- `ref_order`
- `ref_role`
- `ref_raw`
- `book_raw`
- `book_norm`
- `chapter_start`
- `verse_start`
- `chapter_end`
- `verse_end`
- `is_range`
- `confidence`
- `raw_json`

Rules:

- Keep `ref_raw` even when `book_norm` is present.
- Use conservative normalization.
- If the parse is ambiguous, record the ambiguity in `raw_json` instead of inventing certainty.
- `ref_order` must be unique within each `entry_key`.
- Prefer sequential `ref_order` values starting at `1` within each `entry_key`.

## `coverage`

`coverage` is always an object.

Recommended fields:

- `entries_status`
- `entries_status_reason`
- `evidence_files`

Rules:

- Prefer normal extracted `entries`; do not use `coverage` as an excuse to leave the payload skeletal.
- If `sections` is non-empty and `entries` is empty, `coverage.entries_status` must be `unrecoverable_ocr` or `no_line_items`.
- In that same exceptional case, `coverage.entries_status_reason` must be a non-empty string and `coverage.evidence_files` must be a non-empty list of OCR file paths that justify the empty extraction.

## Ambiguity semantics

- Material ambiguity only: keep one `entry`, preserve candidate evidence in `raw_json`, and use `section_start_file`, `editorial_anchor_file`, and `target_file_best` carefully.
- Editorial ambiguity: emit more than one `entry` when competing readings change the structure of the payload.

## Final response

After writing the payload file, return only:

```json
{"status":"ok","volume_id":"<VOLUME>","written_file":"data/alphabetical_index_payloads/<VOLUME>_alphabetical_indices.json"}
```
