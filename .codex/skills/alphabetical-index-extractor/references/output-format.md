# Output Format

This skill writes only the artifact named by the current semantic, locator, or repair prompt.
It never assembles or writes the canonical per-volume payload. Python validates the phase
artifacts, performs exact-once assembly, and owns the importable payload described in
`../../../docs/contrato_extrator_indices_alfabeticos.md`.

## Phase outputs

- Discovery: one segmented `discovery_manifest.json`.
- Semantic: `volume.json`, `coverage.json`, one section fragment per owned section, and
  `manifest.json` written last.
- Locator: one result object containing exactly the refs owned by its shard.
- Repair: replacement locator results for exactly the request items.

Paths come exclusively from the runtime prompt. Section fragments contain arrays named
`sections`, `nodes`, `entries`, `refs`, `scripture_refs`, and `notes`. They are checkpoints and
are not imported directly.

New fragments use `schema_version=2` and also contain `task_id`, `input_fingerprint`,
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

- Keep OCR literals, including a literal line-final `-\n`, in `lemma_raw`, `entry_raw`,
  `context_raw`, and `ref_raw`. Derived normalized/search fields may join a proved alphabetic
  soft wrap; record the inference in `raw_json.soft_wrap_repairs`.
- Record an inclusive `source_span` with `file`, `line_start`, and `line_end`.
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

Both phases write:

```json
{
  "schema_version": 1,
  "volume_id": "PL001",
  "input_fingerprint": "<copied from input>",
  "results": []
}
```

There is exactly one result per owned `(entry_key, ref_order)`.

- `resolved`: non-null `target_file`, confidence, and non-empty `evidence`. Each evidence object
  has `kind`, `file`, `editorial_page`, and `detail`; it must connect the cited editorial page to
  the chosen physical file.
- `ambiguous`: `target_file:null`, non-empty `competing_candidates`, and at least one non-empty
  attempted list.
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
