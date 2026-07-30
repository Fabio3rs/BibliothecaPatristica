# Versioned Prompt Contract

The driver uses fresh Codex contexts and durable JSON artifacts. Prompts carry paths and task
ownership; interpretation lives in the versioned contract and glossary.

## Shared envelope

Every new artifact uses `schema_version=2` and records:

- `prompt_contract_version`;
- `interpretation_contract_version`;
- `glossary_version`;
- `output_schema_version`;
- the phase input fingerprint;
- exactly one `volume_id` and `source_root`.

Large inputs are read by path. Agents may inspect any file inside the current `source_root` and
write checkpoints only below the runtime artifact directory. Python validates paths, versions,
fingerprints, exact ownership and output shapes before reuse.

## Discovery phase

Input: prefilter evidence. Output: one discovery manifest.

The agent starts from the physical end, expands beyond the prefilter when needed and writes:

- unique `inspected_files`;
- non-overlapping line/block `segments`;
- `expansion_requests`;
- `unresolved`;
- `status=complete|needs_expansion`.

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
  "task_id": "PL001:semantic:index-1",
  "input_fingerprint": "...",
  "consumed_spans": [
    {"file": "/.../page-900.txt", "line_start": 3, "line_end": 92}
  ],
  "residual_spans": [],
  "sections": [],
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

- `resolved` requires a physical target plus independent editorial-page evidence.
- `ambiguous` preserves readable competitors and attempted evidence.
- `unrecoverable_ocr` is reserved for evidence that cannot be recovered.

Candidates and regex hits are evidence, not automatic decisions. Index source intervals are
excluded. Filename suffixes are never treated as editorial pages.

## Repair phase

Input: only invalid or unresolved locator objects. Output: replacements for exactly those pairs.
The repair agent does not load or rewrite the semantic payload. Python overlays results and
validates exact ownership again.

## Final response

After writing the required artifact, the assistant returns only:

```json
{"status":"ok","volume_id":"<VOLUME>","written_file":"<PHASE_OUTPUT>"}
```
