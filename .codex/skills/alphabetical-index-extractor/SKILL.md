---
name: alphabetical-index-extractor
description: Extract or locate alphabetical, analytical, onomastic, remissive, and scripture-related index data from one OCR volume through compact semantic, locator, or repair artifacts supplied by the driver.
---

# Alphabetical Index Extractor

## Pipeline boundary

This is the closing alphabetical/citation-index pipeline. Start at the physical end of the volume
and move toward its beginning. Its primary objects are indexes of names, subjects, citations,
scripture, places, words, concordances, and editorial cross-references. Tables of works, subworks,
books, parts, chapters, and `ORDO RERUM` belong to the general works-index pipeline.
In this pipeline `ORDO RERUM`, addenda/corrigenda, errata, and other `editorial_closure` blocks
are non-owned stop/boundary material, including when they begin in the middle of a physical OCR
file; never emit their sections or entries here. The corresponding enum values exist only for
legacy database compatibility.

## Operating Contract

- Handle one volume per run.
- Treat the driver's semantic or locator artifact as the current ownership contract, not as a
  limit on OCR investigation inside the current volume.
- Treat the prompt's pre-filtered pages as the starting window, not as ground truth.
- Keep all OCR/corpus investigation inside the current `source_root`. The versioned project
  documentation named below remains readable as contract material.
- Use the alphabetical-index documentation at:
  - `docs/contrato_interpretacao_indices.md`
  - `docs/glossario_notacao_editorial_indices.md`
  - `docs/levantamento_indices_alfabeticos.md`
  - `docs/contrato_extrator_indices_alfabeticos.md`
  - `docs/normalizacao_indices_biblicos.md`
  - `docs/estrategia_pipeline_extracao_indices_alfabeticos.md`
  - `docs/duvidas_frequentes_extracao_indices_alfabeticos.md`
- Treat estimator/helper candidates supplied by the driver as evidence, not as volume content or final decisions.
- Preserve OCR literals. Do not silently normalize uncertain digits, abbreviations, `ibid.`, `seq.`, `seqq.`, `fin`, `*`, or inherited dashes.
- Treat a line-final hyphen as a possible OCR line wrap, not automatically as punctuation.
  Preserve the literal `-\n` in `lemma_raw`, `entry_raw`, `ref_raw`, and `context_raw`. A derived
  search/normalization layer may join alphabetic fragments such as `Corin-\nthios`, but must
  preserve numeric ranges such as `18-\n20` and lexical/editorial hyphens when continuation is not
  proved. Record an inferred join in `raw_json.soft_wrap_repairs`.
- Keep `OCR file`, `editorial page`, and `cited reference` as separate concepts.
- Use the spatial field dictionary in `docs/dicionario_campos_indices_alfabeticos.md`. An OCR file
  is a `.txt` storage unit; an editorial page is a printed number. A filename suffix is neither a
  page value nor independent page evidence.
- Editorial pagination commonly appears as `NUMBER  PAGE-TITLE  NUMBER+1` in a facing-page header,
  but OCR generators may split it across blocks, retain one side, corrupt digits through CER, or
  omit it. Infer it from several neighboring physical files and never from the filename suffix.
- Editorial section titles such as `INDEX SCRIPTURÆ SACRÆ`, `INDICES IN VITAS PATRUM`, `INDEX SCRIPTORUM`, `TABLE ...`, `ORDO ...`, or `CAP. ...` are not biblical book names and must never be written to `book_raw` or `book_norm`.
- For PG/PL Latin citations, use the Migne/Vulgate profile only when a biblical locator confirms
  the context: `I/II Regum` map to `1/2 Samuel`, `III/IV Regum` to `1/2 Reis`, and
  `I/II Esdrae` to `Esdras/Neemias`. Preserve `book_raw`. Do not apply that numbering globally,
  to PO Latin/Greek material by default, to Greek headings, or to bare prose tokens such as
  `Reg.`. In an explicit French PO scripture table, the historical French Vulgate headings
  `I/II/III/IV Rois` map contextually to `1/2 Samuel` and `1/2 Reis`; this exception requires
  a table heading or a confirming passage, not merely a prose occurrence of `Rois`.
- PO is internally multilingual. Historical English `I/II/III/IV Kings` maps to
  `1/2 Samuel` and `1/2 Reis` only when the local section declares that profile, contains the
  decisive `III/IV Kings` sequence, or the cited passage confirms it. Modern English `I Kings`
  means `1 Reis`, so collection membership alone is insufficient.
- In confirmed Latin PG/PL scripture context, `Jo.` means João while explicit `Job` means Jó.
  Preserve the literal and never promote the `Jo.` override to a global alias.
- Normalize `æ→ae` and `œ→oe` only in the derived lookup layer. Preserve ligatures in
  `book_raw`, `ref_raw`, and context.
- Recognize `III/IV Esdras` or `III/IV Esdrae` as historical noncanonical works when explicit.
  Preserve the literal and record `raw_json.canonical_status=historical_noncanonical` plus
  `historical_book_key`; never coerce them to Esdras/Neemias.
- Write only the phase output set and optional checkpoints explicitly authorized by the runtime
  prompt. Python, not an agent, assembles the canonical final payload.
- Return only a tiny JSON acknowledgment in the final assistant message.

## Required Inputs

Expect the driver to provide one of five bounded phase contracts:

- discovery phase: `volume_id`, `source_root`, prefilter evidence, and a segmented manifest output;
- semantic phase: `volume_id`, `source_root`, filtered-pages artifact, semantic directory, and
  manifest output;
- locator phase: `volume_id`, `source_root`, one citation-shard input, and its result output;
- repair phase: `volume_id`, `source_root`, a compact request containing only pending citations,
  and its result output.
- scripture-table micro-repair: one section-local format profile, only its unresolved lines, and
  one bounded suggestion result; it never owns semantic entries or target files.

The driver fills these paths at runtime. Read large artifacts from disk only when the current
phase names them. Never load a complete assembled payload during locator or repair work.

## Workflow

1. Read the exact runtime phase contract and the versioned interpretation/glossary references first.
2. In discovery, classify non-overlapping line/block segments as `owned`, `boundary`, `context`,
   or `uncertain`. Record every inspected file and any required expansion. Expansion requests
   must be bounded and actionable; the driver redispatches at most three expansion rounds. Do not
   extract entries.
3. In the semantic phase, inspect the pre-filtered OCR files and any neighbors required to follow
   complete sections and category hierarchies. Write one fragment per semantic section and write
   the manifest last.
   - Every owned section must record all four taxonomy dimensions in `raw_json`:
     `pipeline_owner`, `alphabetical_role`, `material_reference_mode`, and `scripture_mode`.
   - An entry may override only `material_reference_mode` and `scripture_mode`; entry values take
     precedence over section values. Do not use an omitted value as an implicit default.
   - Owned section fragments must record `file_start` and `file_end`; both are OCR file paths and
     map to `index_ocr_file_start/end` in database v8. A stop boundary is recorded
     in `manifest.boundary_decisions`, not emitted as a section.
   - Every entry records a `source_span` covered by its fragment's `consumed_spans`.
   - Every fragment records `task_id`, `input_fingerprint`, `consumed_spans`, `residual_spans`,
     `unresolved`, and `decision_log`.
4. In semantic refs, serialize every printed material citation separately with stable
   `(entry_key, ref_order)`. Keep target fields null.
   - one person/name/subject entry may have many material refs
   - one `scripture_citation` or `scripture_pericope` entry represents one biblical passage
   - if a printed line contains different biblical passages, split them into separate entries
   - attach every material page citing the same passage as a separate ref on that passage entry
   - preserve a printed page range as one `editorial_range`; do not invent member pages
   - set each biblical material ref's `scripture_ref_order` to its matching
     `scripture_refs.ref_order`; leave it null for names and subjects
   - remissive scripture entries must have material refs; a non-remissive textual apparatus may
     omit them only when its section explicitly records
     `raw_json.material_reference_mode: "source_only"`
5. Stop at `ORDO RERUM`; preserve its boundary evidence but emit none of its content.
6. In a locator phase, process exactly the citations owned by the shard. Validate estimator/helper
   guesses against OCR headers, neighboring files, and targeted searches. Do not alter semantic
   entries or categories.
   Resolve every `(entry_key, ref_order)` independently; never propagate one entry-level target
   to all refs without page-specific evidence.
   - The driver first restricts candidates by the printed editorial page and then checks
     book/chapter/verse or name evidence inside those physical files. When a `target_locator` has
     no printed page, use the page-independent work-locator bundle instead: exact work title or
     abbreviation, locator-number cooccurrence, and section/work-family confirmation.
   - A unique scripture candidate may be resolved before sharding only when it has independent
     page evidence and material citation evidence. Numeric-only and text-only matches remain
     pending for an agent.
   - In PO, repeated or parallel pagination must be delimited by work/fascicle; the same printed
     number in another fascicle is a competing candidate, not confirmation.
   - For biblical refs, inspect the shard's deterministic `scripture_ref`,
     `table_reference_suggestions`, regex candidates, and section-local
     `citation_format_profile`.
   - Prefer exact book/chapter/verse hits inside an explicit critical-apparatus block over
     uncontextualized mentions, but still verify the cited editorial page.
   - Short book aliases such as `Gen.`, `Jo.`, or `Is.` count only inside a complete citation
     pattern, never as arbitrary substrings of body words.
   - Never select a regex hit inside the source index section.
   - A scripture-table repair prompt owns only its listed unresolved lines; it must not reopen or
     reproduce complete OCR pages.
7. In a repair phase, process exactly the pending citations in the compact repair request. Do not
   open or rewrite the full semantic payload. A genuinely ambiguous result may remain
   `ambiguous`; do not relabel readable competing candidates as `unrecoverable_ocr`.
8. Reuse artifacts on disk only when the driver has verified their input/contract fingerprint.
   Never use conversational memory or session resume as continuation state.
9. Analyze candidate evidence conservatively:
   - use it to separate the legacy fields `section_start_file`, `editorial_anchor_file`, and
     `target_file_best`, whose v8 meanings are respectively `index_section_start_ocr_file`,
     `index_entry_source_ocr_file`, and `resolved_target_ocr_file`
   - preserve candidate evidence in `raw_json`
   - do not let helper probabilities override direct editorial evidence without explanation
   - if the helper leaves the case ambiguous or the target file does not clearly confirm the printed page, inspect neighboring OCR files before settling on a partial result
   - when CER or pagination drift weakens the page-number signal, search the current volume with `rg -n -S` and regexes derived from the local editorial pattern
   - treat page drift between printed numbering and OCR file suffixes as normal; do not reject a candidate only because the numbers do not align literally
10. Keep `section_kind` inside the schema enum only. Do not invent narrower ad hoc values.
11. Map headings like `INDEX AUCTORUM VETERUM ...` and `INDEX AUCTORUM RECENTIORUM ...` to `author_index`. Preserve the finer editorial distinction in `heading_raw`, `section_key`, and `raw_json.section_kind_reason`.
12. For scripture-like sections, distinguish editorial headings from inherited biblical book headings:
   - inherit the book only from explicit biblical headings such as `GENESIS.`, `EXODUS.`, `Psal. ...`, `EX PSALMO I.`, or equivalent real book/salmo labels
   - never inherit the book from a section title like `INDEX SCRIPTURÆ SACRÆ.` or `TABLE DES CITATIONS DE LA BIBLE`
   - when a row has only chapter/verse or verse-level data, inherit from the nearest valid biblical heading until the next explicit biblical heading
   - record a section-local `raw_json.scripture_numbering_profile` when a historical numbering
     system is proved, especially `po_old_english` for `I-IV Kings`; do not assign one profile to
     the entire PO collection
13. Treat `LOCA EX PSALMIS`, `VARIANTIA IN PSALTERIIS`, and similar verse-apparatus sections as a dedicated scripture-apparatus class:
   - they are not ordinary alphabetical subject indexes
   - they may still produce `scripture_refs`, but the current psalm/book must come from local heading context such as `EX PSALMO I.`
   - preserve the variant/apparatus nature in `raw_json`
14. Titles such as `CAP. LIII. — Index scriptorum ...` are structural section titles, not ordinary lemma lines and not scripture refs by themselves.
15. Use `references/output-format.md` for semantic object fields; Python owns final assembly.
16. If ambiguity is only material, keep one entry and let the locator result preserve competing candidates.
17. If ambiguity changes the editorial structure, emit competing semantic entries and mark the ambiguity explicitly.

## Helper Use Rules

- The helper does not decide the final payload.
- The helper exists to expose material candidates and evidence for the agent.
- Preserve at least these helper fields in `raw_json` when they are relevant:
  - `status`
  - `candidate_role`
  - `reason_summary`
  - top candidate list with `file`, `probability`, and main evidence kinds
- When the helper says `ambiguous`, keep the ambiguity explicit unless OCR inspection resolves it.
- If the helper and direct OCR reading disagree, prefer the reading that is best supported by the printed structure and explain the override in `raw_json`.

## What to Record

- every detected alphabetical-index section in `sections`
- heading hierarchy in `nodes` when it exists
- one logical entry per extracted line or grouped fragment in `entries`
- one or more material references per entry in `refs`
- parsed biblical references in `scripture_refs` when present
- `scripture_refs` only when the biblical book context is explicit or safely inherited from a real biblical heading
- no `scripture_ref` for an incidental biblical mention whose effective `scripture_mode` is
  `incidental_mention`; preserve it only in the semantic entry/context
- coverage notes and residual uncertainty in `coverage` and `notes`
- when `entries` must stay empty, explicit recovery status in `coverage`
- editorial remissions such as `vid.`, `vide`, `voir`, `v.`, `cf.`, or `id.` without a real locator should stay at entry level and should not be emitted as material `refs`
- if a scripture-like line lacks a safe biblical book context, omit the `scripture_ref` rather than inventing `book_raw` / `book_norm`
- reusable and volume-local partial state in the runtime intermediate directory when that materially reduces rework on reruns
- `todo.json` progress notes inside the runtime intermediate directory when they help the agent resume chunked work safely

## Recommended `todo.json` format

When needed, store progress notes at:

- `<intermediate_dir>/todo.json`

Recommended shape:

```json
{
  "volume_id": "PL018",
  "updated_at": "2026-07-19T12:00:00Z",
  "current_focus": "Resolve invalid cross-reference refs and rebuild final payload",
  "completed": [
    "sections recovered",
    "entries chunk A-C validated"
  ],
  "pending": [
    "fix refs[2] cross-reference modeling",
    "rebuild refs.json",
    "assemble final payload and validate"
  ],
  "blocked": [
    "helper remains ambiguous for page drift in one entry"
  ],
  "notes": [
    "Use neighboring OCR files around the candidate target.",
    "Recheck previous failure before rewriting payload."
  ]
}
```

Rules:

- Keep it short and operational.
- Update `updated_at` whenever the file changes.
- Prefer short strings over nested structures.
- Use it as a checkpoint aid only; the canonical extraction data still belongs in the intermediate JSON fragments and final payload.

## Completeness Rule

- Do not stop after section detection.
- The semantic artifacts are incomplete unless the actual recoverable entries and references have
  been serialized. The agent does not assemble the canonical payload.
- Empty `entries` are exceptional. Use them only when the OCR is too damaged to recover line items or the section truly has none.
- Do not invent placeholder entries just to satisfy the schema.
- If no owned section exists after the inspected closing window, use `sections=[]` and
  `coverage.entries_status=no_index_section`, with a reason and inspected evidence files.
- If `sections` is non-empty and `entries` is empty, set `coverage.entries_status` to either
  `unrecoverable_ocr` or `no_line_items`, explain why in non-empty
  `coverage.entries_status_reason`, and list the main evidence files in non-empty
  `coverage.evidence_files`.
- `unrecoverable_ocr` means the printed content cannot be recovered from available OCR evidence;
  it is not a synonym for ambiguity, a failed search, or a section that does not exist.
- When leaving a section or entry fragment unresolved, lower confidence and explain why in `raw_json`.
- `coverage.entries_status` does not use `partial_*`. A final
  `coverage.locator_status=partial` is permitted only after attempted neighboring-page checks and
  OCR searches have been recorded.

## Ambiguity Rule

- Use one semantic `entry` when the logical entry is stable and only the material locator is disputed.
- Use multiple `entries` when the ambiguity changes segmentation, inherited lemma scope, entry kind, or the set of references.
- The schema allows repeated `lemma_raw`, `lemma_norm`, and `entry_raw`; do not collapse competing hypotheses prematurely.

## References

- Use `references/prompt-contract.md` for the five bounded runtime phase contracts.
- Use `references/output-format.md` for the phase artifacts and semantic object shapes. Python,
  not this skill, owns canonical payload assembly.
