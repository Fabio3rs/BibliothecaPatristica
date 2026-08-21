# Versioned Prompt Contract

The driver uses fresh Codex contexts and durable JSON artifacts. Prompts carry paths and task
ownership; interpretation lives in the versioned contract and glossary.

Spatial field meanings live in `../../../docs/dicionario_campos_indices_alfabeticos.md`.
In phase artifacts, legacy `file` means an OCR `.txt` path; it never means a printed editorial
page.

## Shared envelope

Every phase control envelope (discovery manifest, semantic section fragment and manifest,
locator/repair result, or micro-repair result) uses `schema_version=2` and records:

- `prompt_contract_version`;
- `interpretation_contract_version`;
- `glossary_version`;
- `output_schema_version`;
- the phase input fingerprint;
- exactly one `volume_id`;
- `source_root` on inputs and discovery manifests. Section fragments and bounded result envelopes
  inherit it through their exact input fingerprint instead of repeating it.

Large inputs are read by path. Agents may inspect any file inside the current `source_root` and
write checkpoints only below the runtime artifact directory. Python validates paths, versions,
fingerprints, exact ownership and output shapes before reuse.

Semantic `volume.json` and `coverage.json` are component objects named by the versioned manifest;
they do not repeat the control envelope. Their integrity is inherited through the manifest and
semantic input fingerprint.

## Discovery phase

Input: prefilter evidence. Output: one discovery manifest.

The agent starts from the physical end, expands beyond the prefilter when needed and writes:

- unique `inspected_files`;
- non-overlapping line/block `segments`;
- `expansion_requests`;
- `unresolved`;
- `status=complete|needs_expansion`.

`needs_expansion` is a durable, non-terminal checkpoint: the driver may redispatch discovery for
up to three bounded expansion rounds after the initial run. Every expansion request contains an
identifier, reason, one or more anchor files, a direction (`before`, `after`, `both`, or
`specific`), and a finite `max_files`. A completed request is removed; an unchanged request must
not be repeated after its evidence was inspected. Irreducible uncertainty belongs in `unresolved`.

Segment roles are `owned`, `boundary`, `context`, and `uncertain`. A physical file may contain
both an owned segment and a later boundary segment. Discovery never emits semantic entries.

## Semantic phase

Input: discovery/prefilter evidence. Output:

- `volume.json`;
- `coverage.json`;
- one section fragment per owned section;
- optional `glossary_suggestions.json`;
- `manifest.json` written last.

Each version-2 fragment contains:

```json
{
  "schema_version": 2,
  "prompt_contract_version": 4,
  "interpretation_contract_version": 1,
  "glossary_version": 1,
  "output_schema_version": 2,
  "task_id": "PL001:semantic:index-1",
  "input_fingerprint": "...",
  "consumed_spans": [
    {"file": "/.../page-900.txt", "line_start": 3, "line_end": 92}
  ],
  "residual_spans": [],
  "sections": [{"section_key": "PL001:index-1", "file_start": "/.../page-900.txt", "file_end": "/.../page-900.txt", "raw_json": {"pipeline_owner": "alphabetical", "alphabetical_role": "owned_section", "material_reference_mode": "remissive", "scripture_mode": "none"}}],
  "nodes": [],
  "entries": [],
  "refs": [],
  "scripture_refs": [],
  "unresolved": [],
  "decision_log": [],
  "notes": []
}
```

Every entry has a `source_span` covered by `consumed_spans`. A ref may inherit its entry span; it
uses a separate span when printed elsewhere. Printed lists become independent material refs.
Printed ranges remain one `editorial_range`. Distinct biblical passages are distinct entries;
their material occurrences link by `scripture_ref_order`.

The manifest owns each section exactly once, records precise boundary decisions and copies all
contract versions plus the input fingerprint.

## Locator phase

Input: one compact shard. Output: exactly one result per owned `(entry_key, ref_order)`.

- `resolved` requires a physical target plus independent editorial-page evidence when a printed
  page exists. A page-less `target_locator` uses the strict work-title + locator-number +
  work-family bundle instead.
- `ambiguous` preserves readable competitors and attempted evidence.
- `unrecoverable_ocr` is reserved for evidence that cannot be recovered.

Candidates and regex hits are evidence, not automatic decisions. Index source intervals are
excluded. Filename suffixes are never treated as editorial pages.

A candidate may contain `facsimile_hint` with an unambiguous sibling `image_path`, pairing basis,
and `inspection_status=not_inspected`. This is an intermediate work hint, not visual evidence.
Open the image when glyphs, headers, columns, punctuation, or layout matter. Do not copy the PNG
path into the locator result or canonical payload; report the resulting observation against the
OCR target file.

## Repair phase

Input: only invalid or unresolved locator objects. Output: replacements for exactly those pairs.
The repair agent does not load or rewrite the semantic payload. Python overlays results and
validates exact ownership again.

## Scripture-table micro-repair

Input: one section-local citation-format profile and only the unresolved table rows owned by that
task. Output: exactly one bounded suggestion per `line_id`. Both use `schema_version=2` and the
shared contract-version envelope. This phase proposes parsing evidence only; it cannot rewrite
semantic entries, decide body target files, or inspect unrelated complete pages.

## Final response

After writing the required artifact, the assistant returns only:

```json
{"status":"ok","volume_id":"<VOLUME>","written_file":"<PHASE_OUTPUT>"}
```
