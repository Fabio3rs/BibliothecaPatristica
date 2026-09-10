# Deterministic structural-entry target localization

Use this procedure during semantic chunk extraction when an owned general-index entry describes a
chapter, book, part, homily, epistle, question, or comparable structural unit. The chunk may inspect
any OCR file inside the current volume's `source_root`; it still emits entries only from its owned
index files.

## Why localization belongs in the chunk

The chunk has the strongest local semantic context: the complete index literal, its work/author,
numbering restarts, surrounding titles, and section boundaries. A later page-number-only locator
cannot reliably recover page-less `INDEX CAPITUM` entries and may collapse every chapter onto the
work opening. Resolve strong targets now and leave weak ones explicitly unresolved.

## Search procedure

1. Preserve the literal index entry and extract its structural label and ordinal separately.
2. Split the index sequence when numbering restarts, normally at `I`, because each run may represent
   a new book, part, or embedded work.
3. Establish a plausible body window from direct work title/incipit/opening evidence and neighboring
   work or section anchors. Search beyond the local context files when needed, but never outside the
   current `source_root`.
4. Search with distinctive title phrases first. Also try shortened phrases, author plus title tokens,
   and an in-memory OCR-normalized form. Normalization may casefold, decompose Unicode, normalize
   ligatures, collapse whitespace/punctuation, repair obvious search-only OCR confusions, and strip
   labels such as `CAP.`, `CAPUT`, `CHAPTER`, or `CHAPITRE`. It must not replace the stored OCR
   literal.
5. Detect explicit body headings and compare both ordinal and title. Exclude matches in the source
   index, another contents table, `ORDO`, `ELENCHUS`, catalogue, retrospective table, and bare
   running headers.
6. Align the complete run monotonically in physical-file order. Multiple consecutive chapters may
   share one physical scan. Prefer a high-coverage, high-title-similarity sequence over isolated
   numeral matches.
7. Do not assume index ordinal equals body ordinal. A small offset is acceptable only when title
   similarity is strong and neighboring matches prove an insertion, omission, or editorial reorder.
   Record both ordinals. Never shift a sequence from numeric evidence alone.
8. A source index and the body may share a scan. Use that scan as a target only when direct OCR text
   proves that the body opens later on it; record `same_scan_body_opening: true`.
9. If readable candidates remain tied, the title evidence is weak, the sequence is non-monotonic, or
   the apparent match depends only on a page/column/ordinal/filename number, leave `target_file`
   null and record the ambiguity.

## Evidence contract

Every resolved `entry.target_file` emitted by a version-3 general chunk must have:

```json
{
  "raw_json": {
    "source_files": ["/volume/text/index-scan.txt"],
    "physical_target_evidence": {
      "status": "resolved",
      "method": "monotonic_chapter_heading_sequence",
      "query_raw": "CAP. XVIII. De festis a Resurrectione...",
      "matched_heading_raw": "CAPUT XIX. De festis a Resurrectione...",
      "target_file": "/volume/text/body-scan.txt",
      "inspected_files": [
        "/volume/text/body-scan-before.txt",
        "/volume/text/body-scan.txt",
        "/volume/text/body-scan-after.txt"
      ],
      "reason": "Title and neighboring headings establish an editorial +1 body offset.",
      "sequence": {
        "segment": 3,
        "index_ordinal": 18,
        "matched_body_ordinal": 19,
        "previous_match": 17,
        "next_match": 20
      }
    }
  }
}
```

`method` must describe textual or structural evidence. `numeric_equality`, `ordinal_only`, and
`physical_suffix_equality` are invalid methods. `inspected_files` must contain the resolved target.

For unresolved entries, prefer a compact record such as:

```json
{
  "physical_target_evidence": {
    "status": "unresolved",
    "method": "bounded_heading_search",
    "query_raw": "CAP. XX. ...",
    "inspected_files": ["..."],
    "reason": "No title-bearing body heading survived OCR; numeral-only candidates rejected."
  }
}
```

## Separation of concerns

- The chunk may read OCR, use `rg`, and perform in-memory normalization and comparison.
- Extra files are evidence only; they do not transfer semantic entry ownership.
- The chunk does not edit the workplan, helper artifacts, other fragments, canonical payload, or DB.
- Deterministic assembly preserves the resolved target and its evidence by stable `entry_key`.
- The final agent resolves only remaining ambiguities and must not silently discard or replace a
  validated chunk target.
