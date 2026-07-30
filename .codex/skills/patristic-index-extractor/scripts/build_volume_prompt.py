#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def optional_artifact(path: Path | None) -> str:
    if path is None:
        return "(not provided)"
    if not path.is_file():
        return f"path={path} (missing)"
    payload = load_json(path)
    counts = {
        key: len(value)
        for key, value in payload.items()
        if isinstance(value, list)
    }
    return f"path={path}\nsummary={json.dumps(counts, ensure_ascii=False)}"


def build_todo_lines(prescan: dict, collection: str) -> str:
    hits = prescan.get("all_hits", [])
    if collection == "PO":
        lines = [
            "- [ ] Verify the volume title and tome-level table.",
            "- [ ] Verify which `FASC.` entries belong to the current tome.",
            "- [ ] For each discovered structure, locate the real OCR files using string evidence, not only page numbers.",
        ]
        heading_markers = (
            "TOMUS",
            "TABLE DES MATIÈRES",
            "FASC.",
            "AVERTISSEMENT",
            "INTRODUCTION",
            "PRÉFACE",
            "PREFACE",
            "PROLOGUE",
        )
    else:
        lines = [
            "- [ ] Verify the front index and record its scope.",
            "- [ ] For each discovered structure, locate the real OCR files using string evidence, not only page numbers.",
        ]
        heading_markers = (
            "ELENCHUS",
            "AUCTORUM ET OPERUM",
            "INDEX CAPITUM",
            "PROLEGOMENA",
        )
    seen: set[tuple[str, int | None]] = set()
    for hit in sorted(
        (h for h in hits if str(h.get("text", "")).strip()),
        key=lambda h: (h.get("file_seq") if isinstance(h.get("file_seq"), int) else h.get("page") if isinstance(h.get("page"), int) else 10**9, str(h.get("text", ""))),
    ):
        text = str(hit.get("text", "")).strip()
        file_seq = hit.get("file_seq")
        if file_seq is None:
            continue
        if not text[0].isalpha():
            continue
        upper = text.upper()
        if len(text) > 80:
            continue
        if collection != "PO":
            letters = [c for c in text if c.isalpha()]
            upper_ratio = (sum(1 for c in letters if c.isupper()) / len(letters)) if letters else 0.0
            starts_like_heading = text.startswith(
                (
                    "Index capitum",
                    "Elenchus",
                )
            )
            if not starts_like_heading and upper_ratio < 0.7:
                continue
        key = (upper, file_seq)
        if key in seen:
            continue
        if any(marker in upper for marker in heading_markers):
            seen.add(key)
            lines.append(f"- [ ] Verify candidate index in OCR file suffix {file_seq}: {text}")
    if collection == "PO":
        lines.extend([
            "- [ ] Build one TODO item per discovered fascicle or work entrypoint.",
            "- [ ] For each work TODO item, verify front matter and internal tables of books, parts, or chapters.",
            "- [ ] Identify retrospective tables that list other tomes and keep them separate from the current tome.",
            "- [ ] Leave closing names, subjects, scripture, citation, concordance, and alphabetical indexes to the alphabetical-index pipeline.",
            "- [ ] Recheck uncertain page numbers, bracket pagination, and OCR digits before finalizing.",
        ])
    else:
        lines.extend([
            "- [ ] Build one TODO item per discovered work.",
            "- [ ] For each work TODO item, verify the opening pages and the work-level index.",
            "- [ ] Leave closing alphabetical, analytical, onomastic, scripture, citation, and concordance indexes to the alphabetical-index pipeline.",
            "- [ ] Recheck uncertain page numbers, columns, and OCR digits before finalizing.",
        ])
    return "\n".join(lines)


def work_instructions(collection: str) -> str:
    base = [
        "- This run is only for the current `volume_id`; every index section, work anchor, `target_file`, grep, and OCR search must stay inside the current `source_root`.",
        "- Never search another volume to resolve an index from the current volume. A volume index belongs only to its own volume.",
        "- Numbering glossary: `...-NNN.txt` is an OCR file suffix / physical file locator, not an editorial page number.",
        "- Numbering glossary: `start_page`, `page_start`, and `page_ref_*` refer to printed/internal/editorial pagination or column references inside the historical volume.",
        "- Numbering glossary: one scan may contain two printed pages, facing pages, or split-column layouts; do not assume a 1:1 relation between scan layout and printed pagination.",
        "- Start from the PRESCAN hints.",
        "- Verify the hinted files directly before deciding anything.",
        "- Search the whole volume if needed, but do not skip the hinted pages.",
        "- After finding an index heading, keep searching until you locate the real OCR files for the indexed work or section.",
        "- Use multi-step local search with title strings, author names, distinctive phrases, and nearby headings.",
        "- For ligatures and CER variants, search both forms inside the same volume: `Æ/AE`, `æ/ae`, `Œ/OE`, `œ/oe`.",
        "- When a title or heading contains ligatures, run paired local searches inside the same `source_root`, for example `GRÆCITATIS` and `GRAECITATIS`, or `quæ` and `quae`.",
        "- Treat page numbers inside OCR as weak hints only; digit CER, page wear, bleed-through, cropping, and scan defects can make printed numbers unreliable.",
        "- Never infer an editorial/internal page number from the OCR file suffix alone.",
        "- Never infer the OCR file suffix from an editorial/internal page number alone.",
        "- Prefer text evidence over printed numbers: repeated titles, headers, author strings, opening incipits, explicit work names, and distinctive section phrases are stronger anchors than numeric literals.",
        "- Do not map `target_file` from numbers alone when stronger string evidence is available elsewhere in the volume.",
        "- Keep OCR literals.",
        "- When using local text search, scope it to the current volume path only. Good pattern: `rg -n -S \"STRING\" {source_root}`.",
        "- When narrowing by filename suffix, still keep the current volume root explicit. Good pattern: `rg -n -S \"STRING\" {source_root}/*.txt`.",
        "- Prefer paired grep variants when ligatures may have been flattened by OCR, for example: `rg -n -S \"GRÆCITATIS|GRAECITATIS\" {source_root}`.",
        "- Do not run repository-wide `rg`/`grep` for index resolution unless the task explicitly asks for a cross-volume audit.",
        "- Transcribe the actual line-by-line entries for every verified index/table section.",
        "- Do not stop after identifying headings, spans, or section structure.",
        "- Do not use summaries like `entries_summary` in place of real `entries`.",
        "- Leave `entries` empty only if the section truly has no line items, or OCR quality makes line extraction unreliable; explain that explicitly in `raw_json`.",
        "- Before writing the final JSON, validate referential integrity inside the payload: every non-null `sections[].work_key` must exactly match one `works[].work_key` from the same volume payload.",
        "- If an index heading uses a thematic label, alternate Latin form, ligature variant, or shortened title, still point `sections[].work_key` to the real canonical work record already present in `works[]`.",
        "- Never invent a new `sections[].work_key` label unless that same key also exists in `works[]` for the current payload.",
        "- Apply the collection-specific taxonomy from `references/volume-taxonomy.md`.",
        "- If a number is uncertain, keep the raw literal and lower confidence.",
    ]
    if collection == "PO":
        base.extend(
            [
                "- Separate tome-level structures, fascicle inventory, work front matter, work contents, and retrospective tables.",
                "- Distinguish `TABLE DES MATIÈRES` of the current tome from internal work tables and cumulative tables of other tomes.",
                "- Treat `FASC.` lines as candidate fascicle inventory, but verify whether they belong to the current tome.",
                "- Inspect the opening pages of each discovered fascicle before naming the work.",
                "- Do not extract closing names, subjects, scripture, citation, concordance, alphabetical, or analytical indexes; the alphabetical-index pipeline owns them.",
            ]
        )
    else:
        base.extend(
            [
                "- Separate the volume-front inventory from each work's front matter and internal contents.",
                "- For each work, inspect the opening pages and the work index before naming the work.",
                "- Do not extract closing alphabetical, analytical, onomastic, scripture, citation, concordance, names, subjects, or cross-reference indexes.",
                "- For PG/PL, confirm work boundaries with repeated title/author strings across multiple files when numeric references are noisy.",
            ]
        )
    return "\n".join(base)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Codex prompt envelope for one volume.")
    ap.add_argument("--volume", required=True, help="Volume id, e.g. PG001, PL099, or PO025")
    ap.add_argument("--source-root", required=True, help="Text root, e.g. teste/PG001/text")
    ap.add_argument("--collection", required=True, help="Collection code, e.g. PG, PL, or PO")
    ap.add_argument("--prescan-json", type=Path, required=True, help="JSON produced by scan_volume.py --json")
    ap.add_argument("--filtered-pages-json", type=Path)
    ap.add_argument("--editorial-pages-json", type=Path)
    ap.add_argument("--helper-request-json", type=Path)
    ap.add_argument("--helper-output-json", type=Path)
    ap.add_argument("--workplan-json", type=Path)
    ap.add_argument("--output-dir", type=Path, default=Path("data/index_payloads"), help="Directory for the JSON payload artifact")
    args = ap.parse_args()

    prescan = load_json(args.prescan_json)
    output_file = args.output_dir / f"{args.volume}_indices.json"
    prompt = f"""$patristic-index-extractor

TASK
You are extracting the index structure for one OCR volume.

VOLUME
- volume_id: {args.volume}
- source_root: {args.source_root}
- collection: {args.collection}

NUMBERING GLOSSARY
- OCR file / file suffix / physical file: the numeric suffix in `...-NNN.txt`; this identifies the OCR text file only.
- Internal/editorial/printed page: the page, folio, or column number printed inside the historical source.
- Editorial page headers commonly have the form `NUMBER  PAGE-TITLE  NUMBER+1`, for example
  `915  INDEX RERUM  916`.
- OCR generators may keep that header in one block, split it across multiple blocks, preserve only
  one of the numbers, corrupt digits through CER, or omit the header entirely.
- Infer missing or corrupt editorial numbers from several physical files before and after. Never
  fill them from the physical filename suffix.
- Scan/sheet reality: one scan may contain two printed pages, facing pages, or a non-1:1 layout.
- Do not assume these numbering systems are equivalent.

PRESCAN
{json.dumps(prescan, ensure_ascii=False, indent=2)}

LOCALIZATION ARTIFACTS
Use FILTERED PAGES and HELPER EVIDENCE only as localization aids, not as restrictions or semantic
ground truth. These artifacts suggest where to begin, not which pages you may investigate.

Filtered-page localization artifact:
{optional_artifact(args.filtered_pages_json)}

Editorial page-to-file estimator artifact:
{optional_artifact(args.editorial_pages_json)}

Target-locator helper request:
{optional_artifact(args.helper_request_json)}

Target-locator helper output:
{optional_artifact(args.helper_output_json)}

Opening-matter workplan (works/parts/books/chapters; never the closing citations pipeline):
{optional_artifact(args.workplan_json)}

WORK INSTRUCTIONS
{work_instructions(args.collection).format(source_root=args.source_root)}

OCR READING
- Prefer `python scripts/read_ocr_page_text.py --view xml --show-source <file>`.
- The reader automatically joins likely within-block word wraps conservatively.
- Use `scripts/pipeline_index_extraction/fix_linebreak_hyphens.py` only as a recovery helper after
  checking the OCR; it is not evidence.

TODO
{build_todo_lines(prescan, args.collection)}

OUTPUT FILE
Write the full JSON payload to:
{output_file}

The payload must follow `.codex/skills/patristic-index-extractor/references/output-format.md`.
Return a tiny JSON acknowledgment only.

FINAL RESPONSE
{{"status":"ok","volume_id":"{args.volume}","written_file":"{output_file}"}}
"""
    print(prompt.rstrip())


if __name__ == "__main__":
    main()
