#!/usr/bin/env python3
"""Usage: build the PL066 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl066_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL066/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL066_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL066_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL066 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL066_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL066"
COLLECTION = "PL"
MAIN_SECTION_START = 511
MAIN_SECTION_END = 526
ORDO_SECTION_START = 527
ORDO_SECTION_END = 527
MAIN_HEADING_RAW = "INDEX IN REGULAM S. P. BENEDICTI."
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
MAIN_PAGE_START = 1005
MAIN_PAGE_END = 1032
ORDO_PAGE_START = 1057
ORDO_PAGE_END = 1058

PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
BLOCK_RE = re.compile(r"<bloco[^>]*>(?P<content>.*?)</bloco>", re.IGNORECASE | re.DOTALL)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
NOISE_RE = re.compile(
    r"^(?:Digitized by Google|PATROL\.\s*LXVI\.?|_+|\d{1,4}\.?|)\s*$",
    re.IGNORECASE,
)
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
VIDE_RE = re.compile(r"\bVide\b|\bV\.\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    if "<pagina" not in raw_text:
        for raw in raw_text.splitlines():
            text = norm(raw)
            if text and not NOISE_RE.fullmatch(text):
                lines.append(text)
        return lines
    for match in BLOCK_RE.finditer(raw_text):
        content = match.group("content") or ""
        for raw in content.splitlines():
            text = norm(raw)
            if not text or NOISE_RE.fullmatch(text):
                continue
            lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = extract_lines(path)[:5]
        blob = " ".join(lines)
        for match in PAGE_HEADER_RE.finditer(blob):
            page = int(match.group(1))
            if 1 <= page <= 9999:
                page_map.setdefault(page, str(path))
    return page_map


def section_for_file_num(num: int) -> dict[str, Any]:
    if MAIN_SECTION_START <= num <= MAIN_SECTION_END:
        return {
            "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
            "section_kind": "alphabetical_general",
            "heading_raw": MAIN_HEADING_RAW,
            "heading_norm": "index in regulam s. p. benedicti",
            "page_start": MAIN_PAGE_START,
            "page_end": MAIN_PAGE_END,
        }
    if ORDO_SECTION_START <= num <= ORDO_SECTION_END:
        return {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "page_start": ORDO_PAGE_START,
            "page_end": ORDO_PAGE_END,
        }
    raise ValueError(f"file outside expected sections: {num}")


def is_letter_line(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def should_continue(buffer: str, line: str) -> bool:
    if not buffer:
        return False
    if line.startswith("—") or line.startswith("-"):
        return True
    if line.startswith("ibid.") or line.startswith("id."):
        return True
    if line[:1].islower():
        return True
    return False


def parse_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1
    for match in PAGE_TOKEN_RE.finditer(text):
        raw = match.group(0).strip()
        start = int(match.group(1))
        end = match.group(2)
        prefix = text[: match.start()]
        suffix = text[match.end() : match.end() + 10]
        if prefix.rstrip().endswith(("Act", "Apost", "Matth", "Luc", "Rom", "Joan", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")):
            continue
        if prefix.rstrip().endswith("(") or "(" in prefix[-8:]:
            # Likely a biblical or parenthetical citation.
            if not re.search(r",\s*$", prefix):
                continue
        if end is not None:
            ref_raw = raw
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
        ref_raw = raw
        ref_kind = "editorial_page"
        if "et seq" in suffix.lower():
            ref_raw = f"{raw} et seq."
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": ref_kind,
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
    if not refs and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
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
    return refs, page_hints, last_page


def extract_entries(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    current_section_key: str | None = None
    current_section_kind: str | None = None
    entry_order = 0
    node_order = 0
    buffer = ""
    buffer_file: str | None = None
    last_page: int | None = None
    section_started = False
    main_index_open = False
    pending_index_word = False

    def flush() -> None:
        nonlocal buffer, buffer_file, entry_order, last_page
        text = norm(buffer)
        source_file = buffer_file
        buffer = ""
        buffer_file = None
        if not text or current_section_key is None or source_file is None:
            return
        if is_letter_line(text) or NOISE_RE.fullmatch(text):
            return
        refs_local, page_hints, last_page_local = parse_refs(text, last_page)
        if last_page_local is not None:
            last_page = last_page_local
        lemma_raw = text
        cut = None
        for ref in refs_local:
            idx = text.find(ref["ref_raw"])
            if idx >= 0 and (cut is None or idx < cut):
                cut = idx
        if cut is not None and cut > 0:
            lemma_raw = norm(text[:cut])
        lemma_display = lemma_raw
        entry_kind = "lemma"
        if VIDE_RE.search(text) and not refs_local:
            entry_kind = "cross_reference"
            if "Vide" in text:
                lemma_display = norm(text.split("Vide", 1)[0])
        elif current_section_kind == "ordo_rerum" and not refs_local:
            entry_kind = "heading_group"
        elif is_letter_line(text):
            entry_kind = "heading_group"
        inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else source_file
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": current_section_key,
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
            "section_start_file": next((str(p) for p in files if file_num(p) == MAIN_SECTION_START), str(files[0])),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.9 if refs_local else 0.74,
            "raw_json": {
                "source_file": source_file,
                "section_kind": current_section_kind,
                "page_hints": page_hints,
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
                    "section_start_file": next((str(p) for p in files if file_num(p) == MAIN_SECTION_START), str(files[0])),
                    "editorial_anchor_file": source_file,
                    "confidence": 0.86 if target_file else 0.68,
                    "raw_json": {},
                }
            )
        helper_entries.append(
            {
                "entry_id": entry_key,
                "lemma_raw": lemma_raw or text,
                "query_names": [q for q in [lemma_raw, lemma_display, text] if q],
                "page_hints": [str(p) for p in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": text,
            }
        )

    for path in files:
        num = file_num(path)
        if not (MAIN_SECTION_START <= num <= ORDO_SECTION_END):
            continue
        lines = extract_lines(path)
        for line in lines:
            if num == MAIN_SECTION_START and not main_index_open:
                if line == "INDEX":
                    pending_index_word = True
                    continue
                if pending_index_word and "IN REGULAM" in line and "BENEDICTI" in line:
                    main_index_open = True
                    current_section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
                    current_section_kind = "alphabetical_general"
                    pending_index_word = False
                    continue
                pending_index_word = False
                if line.startswith(MAIN_HEADING_RAW):
                    main_index_open = True
                    current_section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
                    current_section_kind = "alphabetical_general"
                    continue
                # Ignore the pre-index content that precedes the real alphabetical block.
                continue
            if num == ORDO_SECTION_START:
                if line.startswith(ORDO_HEADING_RAW) or ("ORDO RERUM" in line and "HOC TOMO" in line):
                    current_section_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"
                    current_section_kind = "ordo_rerum"
                    continue
            if line.startswith(MAIN_HEADING_RAW) or line.startswith(ORDO_HEADING_RAW):
                flush()
                continue
            if is_letter_line(line) and main_index_open and not section_started:
                flush()
                current_letter = line.strip(".")
                node_order += 1
                if current_section_key is None:
                    current_section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
                    current_section_kind = "alphabetical_general"
                section_started = True
                nodes.append(
                    {
                        "node_key": f"{VOLUME_ID}:node:{node_order:03d}",
                        "section_key": current_section_key,
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
            if not section_started and current_section_kind == "alphabetical_general":
                # Skip stray material before the first letter group.
                continue
            if buffer and should_continue(buffer, line):
                buffer = f"{buffer} {line}"
                continue
            if buffer:
                flush()
            if current_section_key is None:
                continue
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
    ap = argparse.ArgumentParser(description="Build PL066 alphabetical index payload.")
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
        "volume_id": VOLUME_ID,
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

    section_1_files = [str(path) for path in files if MAIN_SECTION_START <= file_num(path) <= MAIN_SECTION_END]
    section_2_files = [str(path) for path in files if ORDO_SECTION_START <= file_num(path) <= ORDO_SECTION_END]
    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
            "volume_id": VOLUME_ID,
            "work_key": "regulam_s_benedicti",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": MAIN_HEADING_RAW,
            "heading_norm": "index in regulam s. p. benedicti",
            "heading_letter": None,
            "page_start": MAIN_PAGE_START,
            "page_end": MAIN_PAGE_END,
            "file_start": section_1_files[0],
            "file_end": section_1_files[-1],
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical index of topics and names for the Rule of Saint Benedict.",
                "evidence_files": [section_1_files[0], section_1_files[-1]],
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "volume_id": VOLUME_ID,
            "work_key": "regulam_s_benedicti",
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": ORDO_PAGE_START,
            "page_end": ORDO_PAGE_END,
            "file_start": section_2_files[0],
            "file_end": section_2_files[-1],
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial contents/closure block after the alphabetical index.",
                "evidence_files": [section_2_files[0]],
            },
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": "Patrologia Latina 66",
        "notes": [
            "Main alphabetical index recovered from the tail of the volume.",
            "The final ORDO RERUM block is preserved separately as editorial contents.",
            "OCR literals and abbreviations were preserved; helper output only supports locator choice.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical index and the final ORDO RERUM block from the OCR tail, with conservative line-based entry grouping and page-locator resolution on the volume-local target pages.",
        "evidence_files": [section_1_files[0], section_1_files[-1], section_2_files[0]],
    }
    notes = [
        "Section 1 spans the alphabetical index from 1005 to 1032.",
        "Section 2 spans the closing ORDO RERUM block from 1057 to 1058.",
        "Helper output is preserved in raw_json for locator support, not as editorial authority.",
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
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "helper_entry_count": len(helper_entries),
            "entry_count": len(entries),
            "ref_count": len(refs),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate PL066 alphabetical payload and preserve locator evidence",
            "completed": [
                "OCR tail section boundaries identified",
                "helper request generated and resolved",
                "entries and refs serialized",
            ],
            "pending": [
                "review final payload shape",
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
