#!/usr/bin/env python3
"""Usage: build the PL065 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl065_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL065/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL065_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL065_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL065 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL065_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


SECTION_1_START = 496
SECTION_1_END = 517
SECTION_2_START = 518
SECTION_2_END = 518

HEADING_RE = re.compile(r"^(INDEX OPERUM S\.? FULGENTII\.?|ORDO RERUM QU[ÆAE] IN HOC TOMO CONTINENTUR\.?)$", re.IGNORECASE)
HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$|^[A-ZÆŒ]\.$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
LOWER_CONT_RE = re.compile(r"^[a-zà-ÿ(,.;:\-]")
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().strip(" ,;:")


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = extract_lines(path)[:4]
        header_blob = " ".join(lines)
        for match in HEADER_PAGE_RE.finditer(header_blob):
            page = int(match.group(1))
            if page >= 10:
                page_map.setdefault(page, str(path))
    return page_map


def section_for_file(path: Path) -> dict[str, Any]:
    num = file_num(path)
    if SECTION_1_START <= num <= SECTION_1_END:
        return {
            "section_key": "PL065:alpha:alphabetical_general:001",
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX OPERUM S. FULGENTII.",
            "heading_norm": "index operum s. fulgentii",
            "heading_letter": None,
            "page_start": 975,
            "page_end": 1018,
            "file_start": str(path.parent / f"{path.stem.split('-')[-2]}-{SECTION_1_START}.txt") if False else None,
        }
    if SECTION_2_START <= num <= SECTION_2_END:
        return {
            "section_key": "PL065:alpha:ordo_rerum:002",
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 4019,
            "page_end": 4020,
            "file_start": None,
        }
    raise ValueError(f"file outside expected sections: {path}")


def is_section_heading(line: str) -> bool:
    return bool(HEADING_RE.fullmatch(line))


def is_letter_line(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def should_continue(buffer: str, line: str) -> bool:
    if not buffer:
        return False
    if LOWER_CONT_RE.match(line):
        return True
    if line.startswith("—") or line.startswith("-"):
        return True
    if line.startswith("ibid") or line.startswith("id."):
        return True
    return False


def split_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1
    for clause in re.split(r"\.\s+", entry_raw):
        clause = clause.strip()
        if not clause:
            continue
        if IBID_RE.search(clause) and last_page is not None:
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": "ibid.",
                    "page_ref_raw": "ibid.",
                    "page_ref_int": last_page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            page_hints.append(last_page)
            ref_order += 1
            continue
        for match in PAGE_TOKEN_RE.finditer(clause):
            start = int(match.group(1))
            end = match.group(2)
            if end is not None:
                ref_raw = match.group(0).strip()
                refs.append(
                    {
                        "ref_order": ref_order,
                        "ref_kind": "editorial_range",
                        "ref_raw": ref_raw,
                        "page_ref_raw": ref_raw,
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(start),
                        "range_end_raw": str(int(end)),
                    }
                )
                page_hints.append(start)
                ref_order += 1
                last_page = start
                continue
            if clause[: match.start()].strip().endswith("§"):
                continue
            ref_raw = match.group(0).strip()
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            page_hints.append(start)
            ref_order += 1
            last_page = start
    return refs, page_hints, last_page


def extract_entries(
    files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    entry_order = 0
    node_order = 0
    buffer = ""
    buffer_file: str | None = None
    last_page: int | None = None
    section_start_file = str(next(path for path in files if file_num(path) == SECTION_1_START))
    ordo_start_file = str(next(path for path in files if file_num(path) == SECTION_2_START))
    section1_started = False

    def flush() -> None:
        nonlocal buffer, buffer_file, entry_order, last_page
        text = norm(buffer)
        buffer = ""
        if not text:
            buffer_file = None
            return
        source_file = buffer_file or section_start_file
        if is_section_heading(text) or is_letter_line(text):
            buffer_file = None
            return
        entry_order += 1
        entry_key = f"PL065:entry:{entry_order:04d}"
        refs_local, page_hints, last_page_local = split_refs(text, last_page)
        if last_page_local is not None:
            last_page = last_page_local

        cut = None
        for ref in refs_local:
            idx = text.find(ref["ref_raw"])
            if idx >= 0 and (cut is None or idx < cut):
                cut = idx
        if cut is not None and cut > 0:
            lemma_raw = norm(text[:cut].rstrip(" ,;:"))
        else:
            lemma_raw = text
        entry_kind = "lemma"
        if is_letter_line(text) or (not refs_local and re.fullmatch(r"[A-ZÆŒ][A-ZÆŒ\s\.]+", text or "")):
            entry_kind = "heading_group"
        elif not refs_local and IBID_RE.search(text):
            entry_kind = "cross_reference"
        lemma_display = lemma_raw
        inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else buffer_file
        if target_file_best is None and inferred_page is not None:
            target_file_best = page_map.get(inferred_page)

        entry = {
            "entry_key": entry_key,
            "section_key": "PL065:alpha:alphabetical_general:001" if file_num(Path(source_file)) <= SECTION_1_END else "PL065:alpha:ordo_rerum:002",
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": current_letter,
            "inferred_printed_page": inferred_page,
            "section_start_file": section_start_file if file_num(Path(source_file)) <= SECTION_1_END else ordo_start_file,
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.91 if refs_local else 0.74,
            "raw_json": {
                "source_file": source_file,
                "ref_count": len(refs_local),
            },
        }
        entries.append(entry)
        for ref in refs_local:
            target_file = page_map.get(ref["page_ref_int"])
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref["ref_order"],
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": target_file,
                    "target_file_probability": 0.9 if target_file else None,
                    "section_start_file": section_start_file if file_num(Path(source_file)) <= SECTION_1_END else ordo_start_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.88 if target_file else 0.72,
                    "raw_json": {},
                }
            )
        if refs_local:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": [q for q in [lemma_raw, text, lemma_raw.split(",", 1)[0] if lemma_raw else None] if q],
                    "page_hints": [str(p) for p in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": text,
                }
            )
        buffer_file = None

    for path in files:
        num = file_num(path)
        if not (SECTION_1_START <= num <= SECTION_2_END):
            continue
        lines = extract_lines(path)
        for line in lines:
            if is_section_heading(line):
                flush()
                continue
            if num <= SECTION_1_END and not section1_started:
                if not is_letter_line(line):
                    continue
                section1_started = True
            if is_letter_line(line):
                flush()
                current_letter = line.strip(".")
                node_order += 1
                nodes.append(
                    {
                        "node_key": f"PL065:node:{node_order:03d}",
                        "section_key": "PL065:alpha:alphabetical_general:001" if num <= SECTION_1_END else "PL065:alpha:ordo_rerum:002",
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter,
                        "label_sort": current_letter,
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": str(path)},
                    }
                )
                continue
            if buffer and should_continue(buffer, line):
                buffer = f"{buffer} {line}"
                continue
            if buffer:
                flush()
            buffer = line
            buffer_file = str(path)
    flush()
    return entries, refs, nodes, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL065 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    page_map = build_page_map(files)
    entries, refs, nodes, helper_entries = extract_entries(files, page_map)

    helper_request = {
        "volume_id": "PL065",
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_entry = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}

    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        entry.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": helper.get("top_candidates") or helper.get("candidates") or [],
        }

    section_1_files = [str(path) for path in files if SECTION_1_START <= file_num(path) <= SECTION_1_END]
    section_2_files = [str(path) for path in files if SECTION_2_START <= file_num(path) <= SECTION_2_END]
    sections = [
        {
            "section_key": "PL065:alpha:alphabetical_general:001",
            "volume_id": "PL065",
            "work_key": "fulgentii_opera",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX OPERUM S. FULGENTII.",
            "heading_norm": "index operum s. fulgentii",
            "heading_letter": None,
            "page_start": 975,
            "page_end": 1018,
            "file_start": section_1_files[0],
            "file_end": section_1_files[-1],
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical index of works and subject entries for S. Fulgentius.",
                "evidence_files": [section_1_files[0], section_1_files[-1]],
            },
        },
        {
            "section_key": "PL065:alpha:ordo_rerum:002",
            "volume_id": "PL065",
            "work_key": "fulgentii_opera",
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 4019,
            "page_end": 4020,
            "file_start": section_2_files[0],
            "file_end": section_2_files[-1],
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial closure / contents block after the alphabetical index.",
                "evidence_files": [section_2_files[0], section_2_files[-1]],
            },
        },
    ]

    volume = {
        "volume_id": "PL065",
        "collection": "PL",
        "source_root": str(args.source_root),
        "volume_label": "Patrologia Latina 65",
        "notes": [
            "Alphabetical index for S. Fulgentius recovered from the OCR tail.",
            "The final ORDO RERUM block is preserved as a separate editorial-closure section.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical index block and the final ORDO RERUM block from the OCR tail, preserving OCR literals and the visible page references while tolerating line wraps and local corruption.",
        "evidence_files": [section_1_files[0], section_1_files[-1], section_2_files[0]],
    }
    notes = [
        "Section 1 covers INDEX OPERUM S. FULGENTII.",
        "Section 2 covers ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "Helper output was used only as locator support and not allowed to override OCR reading.",
    ]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": "PL065", "updated_at": now_iso(), "helper_entry_count": len(helper_entries)})
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": "PL065",
            "updated_at": now_iso(),
            "current_focus": "Finalize PL065 alphabetical payload from the OCR tail and helper calibration",
            "completed": [
                "OCR tail section boundaries identified",
                "helper request generated and resolved",
                "entries and refs serialized",
            ],
            "pending": [
                "validate final payload shape",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not collapse distinct page references into a single blob.",
            ],
        },
    )
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
