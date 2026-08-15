---
name: patristic-index-extractor
description: Extract structured indices from a single patrística OCR volume (`teste/PG*/text/*`, `teste/PL*/text/*`, or `teste/PO*/text/*`), classify the editorial index structure for that collection, and write a driver-consumable JSON payload. Use when Codex is invoked one volume at a time to inspect OCR pages, tolerate imperfect numbering, and validate the parsed index structure without importing it into SQLite.
---

# Patristic Index Extractor

## Pipeline boundary

This is the opening/general works-index pipeline. Start at the physical beginning of the volume and
move forward through tables of works, fascicles, books, parts, chapters, capitula, and work-level
contents. Closing alphabetical, analytical, onomastic, scripture, and citation indexes belong to
the separate alphabetical-index pipeline.

## Phase Ownership

Respect the runtime phase boundary:

- **Discovery and localization:** the driver runs prescan, filtered-page detection, editorial-page
  estimation, helper localization, and workplan construction. Treat their artifacts as evidence;
  do not turn them into a final payload or database state.
- **Semantic chunk extraction:** when `RUNTIME` names a `chunk_id` and fragment `output_file`, write
  only that fragment. Do not assemble the volume, edit the workplan, write the canonical payload,
  run reconciliation, or touch any database.
- **Deterministic assembly:** the driver validates chunk fingerprints and merges complete fragments
  into `assembled_fragments.json`. Agents must not replace or bypass this artifact.
- **Final payload:** consume every stable object from the validated assembly, resolve only the
  remaining volume-level work, boundary, and anchor questions, write the named canonical JSON, and
  run non-mutating checks on it.
- **Reconciliation and quality gates:** the driver owns work-anchor reconciliation, fragment
  consumption checks, payload validation, and OCR-evidence thresholds.
- **Import:** the driver alone imports after every preceding gate succeeds. No extraction agent,
  chunk agent, helper, or final agent may create an imported database state.

The driver processes pending workplan chunks with
`scripts/run_index_extraction_chunks.py --workplan <path>`. Each chunk runs in a fresh ephemeral
Codex process; validated fragment JSON is the continuation state. A chunk agent reads only its
runtime assignment and exact validator feedback, then stops after writing its fragment.

## Operating Contract

- Handle one volume per run. The driver already launches one Codex instance per volume.
- Work only inside `teste/<VOLUME>/text/*`.
- Read the opening pages of the volume and the opening/front matter of each work.
- Transcribe the actual index lines into `entries`; do not stop at section detection alone.
- Preserve OCR literals. Do not silently normalize uncertain digits, Roman numerals, or `Ibid.` references.
- Use the taxonomy in `references/volume-taxonomy.md`.
- Use `../../../docs/taxonomia_indices.md` when `collection` is `PG` or `PL`.
- Use `../../../docs/taxonomia_indices_po.md` when `collection` is `PO`.
- Accept a machine-generated prompt envelope with `TASK`, `PHASE OWNERSHIP`, `VOLUME`,
  `NUMBERING GLOSSARY`, `PRESCAN`, `LOCALIZATION ARTIFACTS`, `WORK INSTRUCTIONS`, `OCR READING`,
  `TODO`, `OUTPUT FILE`, and `FINAL RESPONSE` sections.
- Maintain an explicit TODO list during extraction: volume-level structures and one checkpoint per
  discovered work, fascicle, book, part, or chapter entrypoint.
- Do not finalize the volume until every TODO item has been verified against OCR files.
- Treat the `PRESCAN` section as the starting map, not as ground truth; inspect the files it names before finalizing the result.
- In the final-payload phase, write the full extraction payload to the file named in the
  `OUTPUT FILE` section. In a chunk phase, write only the named fragment.
- Validate the written payload with non-mutating checks before acknowledging completion. Use
  validators named by the runtime prompt and `scripts/verify_index_payload_evidence.py` when
  applicable; validation may read the primary database but must not change it.
- The driver exclusively owns database initialization, replacement, and import. Never run
  `init_index_db.py`, `import_index_json.py`, `rebuild_index_db_from_payloads.py`, SQLite write
  statements, or any other command that changes `data/patristic_indices.db`.
- Return only a tiny JSON acknowledgment in the final assistant message.
- If `collection` is `PO`, distinguish the tome table from work-level tables and from retrospective tables that list other tomes.
- The current helper scripts are still tuned for `PG/PL` headings. If the prompt came from those scripts, treat the `PRESCAN` as partial and correct it from direct inspection before finalizing a `PO` volume.

## Required Glossary

- `OCR file` / `physical OCR file` / `file suffix`: the physical text file in `teste/<VOLUME>/text/*.txt`, identified by the numeric suffix in `...-NNN.txt`.
- `internal page` / `editorial page` / `printed page`: the page, column, folio, or other numbering printed inside the historical source and cited by index entries.
- `scan` / `sheet reality`: the visual capture behind the OCR. One scan may contain two printed pages, facing pages, split columns, or other non-1:1 layouts.

Use these meanings consistently. Do not collapse them into one numbering system.

## Final-Payload Workflow

Use this workflow only in the final-payload phase. A chunk agent follows `Phase Ownership` and its
bounded runtime prompt instead, then stops after its fragment passes acknowledgment handoff.

1. Read the driver-supplied prescan, workplan, localization artifacts, and exact output path.
2. Classify volume-level tables of works and work-level contents according to the collection-specific taxonomy.
3. Build or update a TODO list as you discover works; keep one checked item per work and separate
   items for its front matter, books, parts, or chapters.
4. For each work, read its opening pages and enough body pages to distinguish its title/front matter
   from a recurring running-header range, then mark that TODO item complete.
5. For `PO`, identify fascicle inventory pages, internal work tables, and retrospective tables before recording final sections.
6. Do not extract closing alphabetical, analytical, onomastic, scripture, citation, concordance,
   names, subjects, or cross-reference indexes; the alphabetical-index pipeline owns them.
7. Construct the canonical JSON from the validated assembly and verified volume-level evidence,
   then write it to the requested output file.
8. Run non-mutating payload checks, correct every reported problem, and leave final database
   validation and import to the driver.

For a corpus-wide deterministic anchor audit, use
`python scripts/reconcile_work_index_anchors.py --all-volumes --workers 12`. The workers are
separate processes, and the editorial-page cache uses SQLite WAL with a busy timeout. Parallel
`--apply` must use `--no-import`; applying or importing results into the primary index database is
the driver/operator's responsibility, not the extraction agent's.

The main driver runs the CPU-bound target locator with
`--helper-workers 12` by default. The locator keeps parsed volume pages once per worker, resolves
entries in separate processes, preserves input order in the output, and uses RapidFuzz for
Levenshtein scoring with a deterministic Python fallback. Worker arguments have no fixed upper
limit; the locator only reduces the effective count when the volume has fewer entries than the
requested number of workers.

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
- Treat all header blocks on the same OCR file as one logical header for comparison, while
  preserving whether numbers and title text came from the same block or separate blocks.
- If the editorial-page estimator returns a facing-page pair and the declared page is one member of
  that pair, preserve it unless bounding-box, column, or direct text evidence identifies the side.
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

## Deterministic Work-Anchor Evidence

Use this evidence order when locating `works[].start_file` and `works[].end_file`:

1. Direct title-page, author, incipit, explicit opening, or explicit closing evidence.
2. A multi-file recurring-header sequence confirmed by body text.
3. Editorial-page estimator pairs and locally consistent printed pagination.
4. Raw numeric coincidence or physical filename proximity.

Apply these rules:

- Exact and Levenshtein/fuzzy phrase matches are additive evidence, not proof by themselves.
  Short, generic, or OCR-damaged titles require an independent author, incipit, structure, or
  neighborhood signal.
- An occurrence in `ORDO`, `ELENCHUS`, a catalogue, a prefatory inventory, or another list is
  source/index evidence, not the work target.
- A similar logical header recurring on at least four physical OCR files is strong evidence of a
  probable body range. The sequence may bridge gaps of up to three files whose header is absent or
  OCR-damaged. Inspect both edges and neighboring files; recurrence alone does not prove that the
  first file is the title page or that the last file is the work ending.
- A running header can identify a work's body range but must not replace direct title-page/opening
  evidence when selecting the start boundary.
- One physical scan may contain two editorial pages, columns, or the end of one work and the start
  of the next. Adjacent works may therefore share a physical file or an editorial page.
- Never set a work's end to `next_start - 1` without direct boundary evidence. Never synthesize
  `end_file` from an editorial number alone.
- Reject any proposed anchor whose start editorial page is greater than its end editorial page.
- Do not collapse slash-separated or otherwise composite inventories into one locally anchored work
  unless the OCR proves that the container itself is a work.
- A pure external remission such as `Vide ... tom.` or `Voir ... tome ...` has no local target.
  Preserve the literal and referenced tome in `raw_json`; do not invent a local `start_file`.

## Ambiguity and Agent Reruns

The deterministic reconciler may add `works[].raw_json.work_anchor_rerun` or a nested
`anchor_locator_review`. Treat either marker as a mandatory TODO item.

- Read every candidate file and its neighbors inside the current volume.
- Preserve candidate rankings, raw literals, estimator pairs, matching strings, header sequences,
  and the reason for ambiguity.
- Remove the marker only when direct OCR evidence resolves the boundary.
- If readable candidates remain tied, or the work is composite, external/remissive, numerically
  contradictory, or dependent on unresolved scan layout, keep the marker with
  `status: "ambiguous"` or `status: "unresolved"`.

## Payload and Validation Contract

- Write only the output JSON and explicitly authorized intermediate checkpoints or validation
  reports. Never write to `data/patristic_indices.db`.
- Non-mutating checks are encouraged. At minimum, verify JSON syntax, required top-level fields,
  work/section referential integrity, page-range consistency, entry completeness, and suspicious
  OCR line-break hyphens. When applicable, run
  `python scripts/verify_index_payload_evidence.py --input <output_file> --sample-size 200`.
- Do not use an import into the primary or a temporary SQLite database as a validation shortcut.
- A successful local validation does not authorize import; the driver repeats its own validation
  and is the only component allowed to import.
- Keep the raw JSON for each section and entry.
- Prefer stable keys (`work_key`, `section_key`) so reruns can replace rows cleanly.
- `work_key` and `section_key` must be unique within the whole database, not only within one payload; prefix them with the current `volume_id` whenever the natural label is generic.
- The final payload file should be JSON only, with top-level keys `volume`, `works`, `sections`, and `notes`.
- The final assistant message should be JSON only, with keys `status`, `volume_id`, and `written_file`.
