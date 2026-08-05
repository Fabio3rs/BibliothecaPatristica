# Output Format

This skill writes only the output set named by the current discovery, semantic, locator, repair,
or scripture-table micro-repair prompt.
It never assembles or writes the canonical per-volume payload. Python validates the phase
artifacts, performs exact-once assembly, and owns the importable payload described in
`../../../docs/contrato_extrator_indices_alfabeticos.md`.

Spatial vocabulary is normative in
`../../../docs/dicionario_campos_indices_alfabeticos.md`: an editorial page is a printed number;
an OCR file is a `.txt` storage unit and may contain more than one editorial page. Some phase
field names remain legacy-compatible, but their v8 database mappings below are unambiguous.

## Phase outputs

- Discovery: one segmented `discovery_manifest.json`.
- Semantic: `volume.json`, `coverage.json`, one section fragment per owned section, and
  `manifest.json` written last.
- Locator: one result object containing exactly the refs owned by its shard.
- Repair: replacement locator results for exactly the request items.
- Scripture-table micro-repair: one bounded parsing suggestion per owned `line_id`.

Paths come exclusively from the runtime prompt. Section fragments contain arrays named
`sections`, `nodes`, `entries`, `refs`, `scripture_refs`, and `notes`. They are checkpoints and
are not imported directly.

New fragments use `schema_version=2`, copy all four contract versions, and also contain
`task_id`, `input_fingerprint`,
`consumed_spans`, `residual_spans`, `unresolved`, and `decision_log`. Every entry has a
`source_span` covered by the fragment's `consumed_spans`. Version-1 artifacts remain readable only
as legacy checkpoints; new prompts must not create them.

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

For every owned section, `file_start` and `file_end` are required and `raw_json` must include:

- `page_start/page_end` mean the printed editorial pagination of the index section itself;
- `file_start/file_end` mean OCR file paths, never editorial page numbers;
- the v8 database names are `index_editorial_page_start/end` and
  `index_ocr_file_start/end`.

- `pipeline_owner`: `alphabetical`
- `alphabetical_role`: `owned_section`
- `material_reference_mode`: `remissive`, `parallel`, or `source_only`
- `scripture_mode`: `none`, `citation_index`, `pericope_index`,
  `concordance_component`, `textual_apparatus`, or `incidental_mention`

`pipeline_owner=general`, `alphabetical_role=stop_boundary`, and
`material_reference_mode=boundary_only` belong only to a manifest boundary decision. Never emit
an `ORDO RERUM`, `editorial_closure`, addenda/corrigenda, or errata section fragment in this
pipeline. `section_kind=ordo_rerum|editorial_closure` is legacy-only and prohibited in new
alphabetical phase artifacts.

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

- `inferred_printed_page` is an estimate of the editorial page on which the index entry itself
  appears; it is not a cited page. Its v8 database name is `index_editorial_page_estimate`.
- `section_start_file` and `editorial_anchor_file` are OCR file paths. The latter means the OCR
  file containing the source entry, not a body target. Their v8 database names are
  `index_section_start_ocr_file` and `index_entry_source_ocr_file`.
- `target_file_best` is a derived navigation cache whose v8 database name is
  `resolved_target_ocr_file`; evidence remains attached to individual refs.

- Keep OCR literals, including a literal line-final `-\n`, in `lemma_raw`, `entry_raw`,
  `context_raw`, and `ref_raw`. Derived normalized/search fields may join a proved alphabetic
  soft wrap; record the inference in `raw_json.soft_wrap_repairs`.
- Record an inclusive `source_span` with `file`, `line_start`, and `line_end`. Here `file` is
  specifically an OCR file path under `source_root`; the normalized database column is
  `ocr_file_path`.
- `lemma_*` fields may be null for editorial notes, cross-references, or unresolved fragments.
- The schema allows more than one entry for the same ambiguous OCR zone when the competing hypotheses are editorially distinct.
- One person, name, or subject entry may own many material refs.
- A `scripture_citation` or `scripture_pericope` entry represents one biblical passage. Split a
  printed line containing distinct passages into separate entries before attaching material refs.
- Remissive scripture entries require material refs. Only a non-remissive textual apparatus may
  omit them, and its section must declare
  `raw_json.material_reference_mode: "source_only"`.
- Entry `raw_json.material_reference_mode` and `raw_json.scripture_mode`, when present, override
  the section values. Other taxonomy dimensions are not entry-overridable.
- Effective `scripture_mode=incidental_mention` never emits a `scripture_ref`; preserve the
  literal mention only in the entry/context.

## `refs`

Each material reference should contain:

- `entry_key`
- `ref_order`
- `scripture_ref_order`
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

- `page_ref_raw/page_ref_int` are the printed editorial page cited by the index, not an OCR file
  number and not the editorial page containing the index entry.
- `target_file` is an OCR file path selected for the cited body material. It must never be
  inferred from the filename suffix alone.
- `line_ref_raw` is line notation within the cited editorial page. Never copy a line endpoint to
  `range_start_raw/range_end_raw` as if it were a page endpoint.
- The v8 database materializes typed endpoints as `cited_editorial_page_*` and
  `cited_editorial_line_*`, plus `cited_location_parse_status`.

- Do not collapse many references into one textual blob.
- Keep `ref_raw` exactly as printed when possible.
- Preserve helper evidence in `raw_json` when it influenced locator decisions.
- `ref_order` must be unique within each `entry_key`.
- Prefer sequential `ref_order` values starting at `1` within each `entry_key`.
- For a biblical entry, `scripture_ref_order` is required and identifies the matching
  `scripture_refs.ref_order` (normally `1`). For names and subjects it must be null.
- Do not emit the same material reference twice for the same `entry_key`.
- Each ref is one material occurrence and must be located independently. Do not copy
  `target_file_best` or one ref's target across sibling refs without page-specific evidence.
- Two refs may point to the same OCR file when that physical file really contains both facing
  editorial pages.
- `ref_kind=scripture` is legacy and prohibited in new phase artifacts. A material occurrence is
  an `editorial_page`, `editorial_column`, `editorial_page_column`, `editorial_range`,
  `editorial_page_line`, `target_locator`, `parallel_locator`, or `unresolved`; the biblical
  passage itself belongs in `scripture_refs`.
- Preserve a printed page range such as `12-15` as one `editorial_range` ref with
  `range_start_raw` and `range_end_raw`; do not invent member-page occurrences. Open forms such
  as `seq.`, `seqq.`, `fin`, uncertain digits, and non-page ranges also remain one conservative
  ref.
- Put interpretations of `ibid.`, `seq.`, `seqq.`, `fin`, `passim` and analogous forms in an
  optional `notation` array using keys from the versioned glossary. Unknown forms belong in the
  fragment's `unresolved` array.
- For `10,2-3`, the page endpoints are `10` and `10`, while the line endpoints are `2` and `3`.
  For `69_13-70_1`, the endpoints are page 69 line 13 and page 70 line 1.

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
- When `III/IV Esdras` or `III/IV Esdrae` is an explicit historical work, keep canonical
  `book_key` ownership with Python and record
  `raw_json.canonical_status=historical_noncanonical` and a stable
  `raw_json.historical_book_key`.
- When a PO section proves historical English numbering, record
  `section.raw_json.scripture_numbering_profile=po_old_english`. French `I-IV Rois` is inferred
  from its explicit spelling and scripture-table context.
- `ref_order` must be unique within each `entry_key`.
- Prefer sequential `ref_order` values starting at `1` within each `entry_key`.
- Do not emit a scripture ref for effective `scripture_mode=incidental_mention`.

## `coverage`

`coverage` is always an object.

Recommended fields:

- `entries_status`
- `entries_status_reason`
- `evidence_files`

Rules:

- Prefer normal extracted `entries`; do not use `coverage` as an excuse to leave the payload skeletal.
- If no owned section exists, use `sections=[]` and `entries_status=no_index_section`.
- If `sections` is non-empty and `entries` is empty, `coverage.entries_status` must be
  `unrecoverable_ocr` or `no_line_items`.
- In that same exceptional case, `coverage.entries_status_reason` must be a non-empty string and `coverage.evidence_files` must be a non-empty list of OCR file paths that justify the empty extraction.
- `unrecoverable_ocr` means the line items cannot be recovered from the available OCR. It does not
  mean ambiguous, not yet searched, or absent.

## Locator and repair result

New locator inputs use `locator_format_version=2` and group coordinates under
`locator_contract.ownership`, `locator_contract.index_source`,
`locator_contract.cited_location`, `locator_contract.search_hints`, and
`locator_contract.target_resolution`. Read those groups first. Flat fields remain compatibility
aliases. A shard-level `excluded_index_intervals` applies to every item and must be excluded from
body targets.

Both phases write:

```json
{
  "schema_version": 2,
  "prompt_contract_version": 4,
  "interpretation_contract_version": 1,
  "glossary_version": 1,
  "output_schema_version": 2,
  "locator_contract_version": 3,
  "volume_id": "PL001",
  "input_fingerprint": "<copied from input>",
  "results": []
}
```

There is exactly one result per owned `(entry_key, ref_order)`.

- `resolved`: non-null `target_file`, confidence, and non-empty `evidence`. For a printed-page
  locator, each decisive evidence object has `kind`, OCR file, observed editorial page, and
  detail. Prefer the canonical names `ocr_file_path` and `observed_editorial_page_number`;
  `file` and `editorial_page` are accepted result aliases. Together they connect the cited
  editorial page to the chosen OCR file.
- A page-less `target_locator` may be `resolved` without editorial-page evidence only when the
  evidence contains all three independent signals: an exact work-title/abbreviation match,
  locator-number cooccurrence, and section/work-family confirmation. A text match or number alone
  is not enough.
- `ambiguous`: `target_file:null`, non-empty `reason`, at least two `competing_candidates`, and at
  least one non-empty attempted list.
- `unrecoverable_ocr`: `target_file:null`, non-empty `reason`, and attempted evidence proving the
  relevant OCR cannot be recovered.
- `attempted_files` is a list of path strings.
- `attempted_searches` is a list of objects with `query` and `result`.
- `competing_candidates` is a list of objects with at least `file` and `reason`.

The repair phase may return `ambiguous`; it must not rename material ambiguity
`unrecoverable_ocr`.

## Ambiguity semantics

- Material ambiguity only: keep one semantic `entry`; the locator preserves competing candidates,
  leaves its `target_file` null, and Python leaves `target_file_best` null unless another resolved
  ref on the entry legitimately supplies it.
- Editorial ambiguity: emit more than one `entry` when competing readings change the structure of the payload.

## Final response

After writing the exact phase output, return only:

```json
{"status":"ok","volume_id":"<VOLUME>","written_file":"<PHASE_OUTPUT>"}
```
