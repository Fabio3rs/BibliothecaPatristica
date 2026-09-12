# OCR Index Extraction Pipelines

## 1. Purpose

This document describes the two OCR index extraction pipelines.

The pipelines extract different editorial objects from one physical volume.

The general pipeline extracts work lists and tables of contents.

The alphabetical pipeline extracts names, subjects, citations, and cross-references.

Both pipelines use deterministic tools and bounded agent tasks.

The tools find evidence and validate output.

The agents read editorial structure and write semantic data.

## 2. Writing standard

This document uses the main writing rules of ASD-STE100, Issue 9.

It uses short sentences, active voice, consistent terms, and vertical lists.

Instructions use the imperative form.

Each instruction contains one action.

Technical nouns keep their repository spelling.

Examples and OCR quotations keep their source spelling.

This document does not claim formal ASD certification.

## 3. Technical terms

Use each term with the meaning in this table.

| Term | Meaning |
|---|---|
| physical OCR file | One text file in `teste/<VOLUME>/text/`. |
| file suffix | The final physical sequence number in an OCR filename. |
| physical sequence | The order of physical OCR files in one volume. |
| editorial page | A page, column, or folio number printed in the source. |
| cited reference | A number or label inside an index entry. |
| scan | The source image that produced one OCR file. |
| CER | Character errors in OCR text. |
| candidate page | A physical OCR file that has possible index evidence. |
| workplan | The JSON file that defines sections, chunks, and ownership. |
| chunk | A bounded extraction task for one agent process. |
| fragment | The validated JSON output from one chunk. |
| payload | The final JSON output for one volume. |
| helper | A deterministic tool that gives evidence to an agent. |
| validator feedback | The exact error message from a failed validation. |

Do not use `page` alone when its meaning is not clear.

Use `editorial page` or `physical OCR file`.

## 4. Pipeline limits

### 4.1 General pipeline

Use `scripts/run_index_extraction.py` for the general pipeline.

This pipeline starts near the physical front of the volume.

It extracts these objects:

- Volume work lists.
- Work titles.
- Fascicles.
- Books.
- Parts.
- Chapters.
- Capitula.
- Work tables of contents.

The pipeline uses the `$patristic-index-extractor` skill.

It writes payloads to `data/index_payloads/`.

It imports valid payloads into `data/patristic_indices.db`.

### 4.2 Alphabetical pipeline

Use `scripts/run_alphabetical_index_extraction.py` for the alphabetical pipeline.

This pipeline starts near the physical end of the volume.

It extracts these objects:

- Alphabetical indexes.
- Analytical indexes.
- Author indexes.
- Name indexes.
- Place indexes.
- Scripture indexes.
- Citation indexes.
- Concordances.
- Editorial cross-references.

The pipeline uses the `$alphabetical-index-extractor` skill.

It writes payloads to `data/alphabetical_index_payloads/`.

It imports valid payloads into the configured alphabetical index database.

### 4.3 Shared chunk runner

Use `scripts/run_index_extraction_chunks.py` for both pipelines.

This script is not a third editorial pipeline.

The workplan supplies one `pipeline_kind` value.

The permitted values are `general` and `alphabetical`.

The chunk runner rejects all other values.

The chunk runner selects the correct skill from `pipeline_kind`.

The first prompt line contains the exact skill name.

The general prompt starts with `$patristic-index-extractor`.

The alphabetical prompt starts with `$alphabetical-index-extractor`.

## 5. Number systems

Keep the three number systems separate.

### 5.1 Physical file number

The suffix in `{uuid}-NNN.txt` identifies the physical OCR sequence.

It does not identify an editorial page.

Use the suffix to sort physical OCR files.

Do not copy the suffix into an editorial page field.

### 5.2 Editorial page number

The historical source prints the editorial page number.

A facing-page scan frequently has this header form:

```text
NUMBER  PAGE-TITLE  NUMBER+1
```

This example has two editorial pages:

```text
915  INDEX RERUM  916
```

An OCR generator can put this header in one text block.

Another generator can put each part in a different block.

One side can be absent.

CER can change a digit into a letter.

The complete header can also be absent.

Inspect several physical OCR files before and after the target.

Accept an editorial number only when the local sequence supports it.

Do not calculate an editorial page from the filename suffix.

### 5.3 Cited reference

An index entry can contain several types of numbers.

The number can identify one of these objects:

- An editorial page.
- A column.
- A folio.
- A note.
- A scripture chapter.
- A scripture verse.
- A book or chapter.
- A manuscript.
- A year.
- A different editorial system.

Use the entry context to identify the number type.

Do not treat all entry numbers as editorial pages.

## 6. File-to-page resolution

`tools/indexing/editorial_page_estimator.py` estimates editorial pages for physical files.

It reads headers, footers, body-top numbers, and neighboring sequences.

Its default neighbor window contains four files on each side.

It accepts digit-like CER characters in number tokens.

It reads paired-page scans and single-page scans.

It can read a header that OCR split into different blocks.

The estimator stores observations in `data/editorial_page_estimator.db`.

The estimator supports automatic runs for PG and PL.

The general driver does not run this estimator for PO.

PO volumes can contain parallel number systems.

`tools/indexing/index_target_locator.py` resolves references in both directions.

It finds candidate physical files for editorial references.

It also reports editorial page candidates for each physical file.

The locator combines these evidence types:

- Exact text.
- Normalized text.
- Local context.
- Editorial number.
- Neighbor sequence.
- Estimator result.
- Physical position.

Text evidence has more authority than weak numeric evidence.

The locator keeps multiple editorial candidates when parallel sequences exist.

The locator returns `ambiguous` when numeric evidence is not sufficient.

An agent must inspect the OCR when the result is ambiguous.

## 7. CER control

The candidate-page builder reads one-line and multiline headings.

It joins as many as three visible heading lines.

It normalizes ligatures and diacritics for comparison.

It accepts common OCR substitutions in plausible heading forms.

It gives exact heading matches more authority than fuzzy matches.

It limits ambiguous headings by physical position.

The workplan adds four context files before and after each section.

It can follow a continuing section beyond the first candidate window.

It stops before the next strong heading or closing boundary.

Use `scripts/read_ocr_page_text.py` to inspect OCR and XML evidence.

Use this command:

```bash
python scripts/read_ocr_page_text.py --view xml --show-source <file>
```

Join a line-break hyphen only when the next text part proves continuation.

Keep a real lexical or editorial hyphen.

## 8. Divide-and-conquer process

The workplan divides a large index into small and stable chunks.

Each chunk owns a set of physical OCR files.

Adjacent files give context but do not change ownership.

Each chunk runs in a new ephemeral Codex process.

The process writes one JSON fragment.

The fragment is the continuation state for a later run.

Conversation memory is not continuation state.

The coordinator validates each fragment before it changes the workplan status.

The assembler merges all valid fragments in a deterministic order.

The final agent reads the assembled data and writes the volume payload.

This method reduces the cognitive load for each agent.

It also prevents one long context from hiding local errors.

## 9. Rerun process

Use the files from the first run as checkpoints.

Do not trust a checkpoint only because the file exists.

### 9.1 Existing chunk fragment

The chunk runner validates each existing fragment before an agent call.

It compares the fragment fingerprint with the current input fingerprint.

The fingerprint covers the contract and the owned OCR files.

An OCR change invalidates the old fingerprint.

A contract change also invalidates the old fingerprint.

The runner reuses a fragment only when validation succeeds.

The runner stores an exact failure in `last_validation_failure`.

The next prompt includes this exact failure.

Thus, the first new agent attempt receives the current validator feedback.

### 9.2 Existing general payload

The general driver validates the existing payload before agent work.

It creates a current failure record when validation fails.

The final agent prompt includes the exact current failure.

The prompt can also include the previous run failure file.

### 9.3 Existing alphabetical payload

The alphabetical driver validates the output checkpoint before extraction.

It writes validation text to the checkpoint error file.

The prompt identifies the checkpoint as `done`, `invalid`, or `missing`.

The prompt gives the checkpoint error file to the agent.

The driver also stores a structured previous failure file.

The next run gives this failure to the agent.

## 10. Validation sequence

Use this sequence for each chunk:

1. Read the existing fragment.
2. Validate its JSON structure.
3. Validate its pipeline type.
4. Validate its section and chunk identifiers.
5. Validate its input fingerprint.
6. Give the exact error to the agent.
7. Run the agent when correction is necessary.
8. Validate the new fragment.
9. Update the workplan.

Use this sequence for the final payload:

1. Assemble all valid fragments.
2. Run the final agent.
3. Validate the acknowledgment.
4. Validate the payload schema.
5. Compare the payload with the assembled fragments.
6. Compare sampled entries with OCR evidence.
7. Reject unjustified empty list sections.
8. Import the valid payload.

The evidence check accepts exact, cross-file, and fuzzy matches.

The configured threshold limits unverified sampled entries.

## 11. Main artifacts

The general pipeline creates these principal artifacts:

- `<VOLUME>_prescan.json`.
- `<VOLUME>_filtered_pages.json`.
- `<VOLUME>_editorial_pages.json`.
- `<VOLUME>_helper_request.json`.
- `<VOLUME>_helper_output.json`.
- `<VOLUME>_indices.json`.
- `<VOLUME>_failure.json`.
- `workplan.json`.
- `assembled_fragments.json`.
- `fragment_consumption_report.json`.
- `payload_evidence_report.json`.

The alphabetical pipeline creates equivalent filtered-page and workplan artifacts.

It also creates an output checkpoint error file when validation fails.

## 12. Helper limits

The helper gives evidence to the agent.

The helper does not make the final editorial decision.

The helper rejects common OCR boilerplate and date-like material.

It reads candidates near workplan sections.

It can join a wrapped index line with its reference line.

It sends useful entry text to the target locator.

Keep the helper result in `raw_json` when it affects a decision.

Record candidate files, probabilities, evidence kinds, and ambiguity.

## 13. Review result

The review found correct separation between the two editorial pipelines.

The shared chunk runner also keeps the pipeline type explicit.

The prompt builder writes the required skill name on the first line.

The rerun path validates existing fragments before the first new agent call.

The rerun prompt includes the latest fragment validation error.

Both final drivers preserve payload validation errors for a later run.

The file-to-page tools do not use numeric equality as proof.

The locator supports parallel editorial numbering in PO evidence.

The standalone estimator remains limited to PG and PL in the general driver.

This limit is explicit in its output artifact.

The current design keeps deterministic evidence separate from agent judgment.

## 14. Focused test command

Run the focused tests after a pipeline change.

```bash
PYTHONPYCACHEPREFIX=/tmp/pdfocr_pycache pytest -q \
  tests/test_build_filtered_pages_profiles.py \
  tests/test_editorial_page_estimator.py \
  tests/test_index_fragment_assembly.py \
  tests/test_index_localization_helpers.py \
  tests/test_index_payload_evidence.py \
  tests/test_index_target_locator.py \
  tests/test_index_workplan.py \
  tests/test_index_workplan_resume.py \
  tests/test_run_alphabetical_index_extraction.py \
  tests/test_run_index_extraction.py \
  tests/test_run_index_extraction_chunks.py
```

## 15. Maintenance rules

Keep the two skill contracts separate.

Keep `pipeline_kind` explicit in all workplans.

Keep the skill name on the first prompt line.

Keep physical files and editorial pages in different fields.

Preserve raw OCR when a reading is uncertain.

Add a small regression test for each parser correction.

Use a small volume sample for development checks.

Do not scan the complete corpus during a focused test.
