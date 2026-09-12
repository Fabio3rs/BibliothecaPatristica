#!/usr/bin/env python3
"""Usage: build the PL063 alphabetical-index payload from OCR, helper resolution, and local checkpoints.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl063_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL063/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL063_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL063_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL063 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL063_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


SECTION_1_HEADING = "INDEX RERUM PRÆCIPUARUM"
SECTION_2_HEADING = "INDEX VOCABULORUM OMNIUM"
SECTION_3_HEADING = "ORDO RERUM"

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ROMAN_ONLY_RE = re.compile(r"^[IVXLCDM]+\.*$", re.IGNORECASE)
SECTION_HEADING_RE = re.compile(
    r"(INDEX RERUM PRÆCIPUARUM|INDEX VOCABULORUM OMNIUM|ORDO RERUM QU[ÆAE] IN HOC TOMO CONTINENTUR|ORDO RERUM)",
    re.IGNORECASE,
)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ENTRY_START_RE = re.compile(r"^[A-ZÆŒ0-9\-]|^—|^-", re.UNICODE)
LOWER_CONT_RE = re.compile(r"^[a-zà-ÿ]|^[,.;:)]")
IBID_RE = re.compile(r"\bibid\.?\b", re.IGNORECASE)
BARE_REMISSION_RE = re.compile(r"\b(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", re.IGNORECASE)
LOCIS_COUNT_RE = re.compile(r"(?:alii|alli|aliq|aliis|aliisque|allisque)\s+locis\s+\d+", re.IGNORECASE)
ENTRY_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ENTRY_PAIR_RE = re.compile(r"(?<!\d)(\d{1,4})\s*,\s*([0-9IVXLCDM]+)(?!\d)", re.IGNORECASE)


@dataclass
class SectionSpec:
    section_key: str
    section_kind: str
    heading_raw: str
    heading_norm: str
    file_start: str
    file_end: str
    page_start: int | None
    page_end: int | None
    anchor_file: str
    start_marker: str
    end_marker: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.strip(" \t\r\n,;:.")
    return text


def sort_key(text: str | None) -> str | None:
    if text is None:
        return None
    return normalize_text(text).lower()


def lemma_norm(text: str | None) -> str | None:
    if text is None:
        return None
    return normalize_text(text).lower()


def strip_ocr_markup(text: str) -> list[str]:
    parsed = parse_ocr_page_xml(text)
    lines = [normalize_text(line) for line in parsed["all_text"].splitlines()]
    return [line for line in lines if line and line not in {"Digitized by Google", "||"}]


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def extract_header_numbers(lines: list[str]) -> list[int]:
    header_lines = lines[:3]
    numbers: list[int] = []
    for line in header_lines:
        for match in HEADER_PAGE_RE.finditer(line):
            value = int(match.group(1))
            if value >= 10:
                numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = strip_ocr_markup(path.read_text(encoding="utf-8", errors="replace"))
        for number in extract_header_numbers(lines):
            page_map.setdefault(number, str(path))
    return page_map


def find_line_index(lines: list[str], marker: str) -> int | None:
    for idx, line in enumerate(lines):
        if marker.lower() in line.lower():
            return idx
    return None


def determine_sections(files: list[Path]) -> tuple[SectionSpec, SectionSpec, SectionSpec, dict[str, list[str]]]:
    file_lines = {str(path): strip_ocr_markup(path.read_text(encoding="utf-8", errors="replace")) for path in files}
    # Section 1 is the subject index on 685-686.
    file_685 = next(path for path in files if path.name.endswith("-685.txt"))
    file_686 = next(path for path in files if path.name.endswith("-686.txt"))
    file_721 = next(path for path in files if path.name.endswith("-721.txt"))
    file_728 = next(path for path in files if path.name.endswith("-728.txt"))
    section1 = SectionSpec(
        section_key="PL063:alpha:analytic_subject:001",
        section_kind="analytic_subject",
        heading_raw="INDEX RERUM PRÆCIPUARUM.",
        heading_norm="index rerum præcipuarum",
        file_start=str(file_685),
        file_end=str(file_686),
        page_start=1365,
        page_end=1368,
        anchor_file=str(file_685),
        start_marker=SECTION_1_HEADING,
        end_marker=SECTION_2_HEADING,
    )
    section2 = SectionSpec(
        section_key="PL063:alpha:alphabetical_general:002",
        section_kind="alphabetical_general",
        heading_raw="INDEX VOCABULORUM OMNIUM QUÆ IN QUINQUE LIBRIS BOETII DE CONSOLATIONE PHILOSOPHIÆ LEGUNTUR.",
        heading_norm="index vocabulorum omnium quae in quinque libris boetii de consolatione philosophiae leguntur",
        file_start=str(file_686),
        file_end=str(file_721),
        page_start=1368,
        page_end=1438,
        anchor_file=str(file_686),
        start_marker=SECTION_2_HEADING,
        end_marker=SECTION_3_HEADING,
    )
    section3 = SectionSpec(
        section_key="PL063:alpha:ordo_rerum:003",
        section_kind="ordo_rerum",
        heading_raw="ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        heading_norm="ordo rerum quae in hoc tomo continentur",
        file_start=str(file_721),
        file_end=str(file_728),
        page_start=1438,
        page_end=1448,
        anchor_file=str(file_721),
        start_marker=SECTION_3_HEADING,
        end_marker=None,
    )
    return section1, section2, section3, file_lines


def file_suffix_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def is_heading_line(line: str) -> bool:
    if not line:
        return False
    if SECTION_HEADING_RE.search(line):
        return True
    if LETTER_RE.match(line):
        return True
    if line.upper() in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Z"}:
        return True
    if line.startswith("LIBER ") or line.startswith("EPISTOLA ") or line.startswith("OPUSCUL.") or line.startswith("CARMEN ") or line == "ENNODIUS.":
        return True
    return False


def looks_like_entry_start(line: str, section_kind: str) -> bool:
    if not line:
        return False
    if line.startswith("-") or line.startswith("—"):
        return True
    if IBID_RE.search(line):
        return True
    if section_kind == "ordo_rerum":
        return bool(re.match(r"^(LIBER|EPISTOLA|OPUSCUL|CARMEN|APPENDIX|INDICULUS|HORMISDA|DICTIONES|DE CONSOLATIONE|TRANSLATIO)", line))
    return bool(ENTRY_START_RE.match(line))


def buffer_has_ref(text: str, section_kind: str) -> bool:
    if IBID_RE.search(text):
        return True
    if section_kind == "alphabetical_general":
        return bool(ENTRY_PAIR_RE.search(text) or ENTRY_NUM_RE.search(text))
    return bool(ENTRY_NUM_RE.search(text))


def should_continue(buffer: str, line: str, section_kind: str) -> bool:
    if not buffer:
        return False
    if line.startswith("||"):
        return False
    if LOWER_CONT_RE.match(line):
        return True
    if not buffer_has_ref(buffer, section_kind) and not looks_like_entry_start(line, section_kind):
        return True
    if section_kind == "alphabetical_general" and not line[:1].isupper() and not line.startswith("-") and not line.startswith("—"):
        return True
    return False


def extract_entry_refs(entry_raw: str, section_kind: str) -> tuple[list[dict[str, Any]], list[int]]:
    working = LOCIS_COUNT_RE.sub("", entry_raw)
    working = re.sub(r"\b(?:aliisque|allisque|aliis que|aliisque)\s+locis\s+\d+", "", working, flags=re.IGNORECASE)
    working = re.sub(r"\b(?:bis|ter)\b", "", working, flags=re.IGNORECASE)
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    if section_kind == "alphabetical_general":
        clauses = [clause.strip() for clause in re.split(r"\.(?:\s+|$)", working) if clause.strip()]
        ref_order = 1
        for clause in clauses:
            for match in ENTRY_PAIR_RE.finditer(clause):
                page = int(match.group(1))
                line = match.group(2)
                refs.append(
                    {
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page_line",
                        "page_ref_raw": match.group(0).strip(),
                        "page_ref_int": page,
                        "page_ref_col": None,
                        "line_ref_raw": line,
                        "range_start_raw": None,
                        "range_end_raw": None,
                    }
                )
                page_hints.append(page)
                ref_order += 1
            # Page-only fallback if no pair could be found in the clause.
            if not ENTRY_PAIR_RE.search(clause):
                for num in ENTRY_NUM_RE.finditer(clause):
                    value = int(num.group(1))
                    if value < 10 and clause.strip().startswith("V"):
                        continue
                    if value >= 1:
                        refs.append(
                            {
                                "ref_order": ref_order,
                                "ref_kind": "editorial_page",
                                "page_ref_raw": num.group(1),
                                "page_ref_int": value,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                            }
                        )
                        page_hints.append(value)
                        ref_order += 1
    else:
        ref_order = 1
        for num in ENTRY_NUM_RE.finditer(working):
            value = int(num.group(1))
            if value < 1:
                continue
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "page_ref_raw": num.group(1),
                    "page_ref_int": value,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            page_hints.append(value)
            ref_order += 1
    return refs, page_hints


def extract_section_entries(
    *,
    section: SectionSpec,
    file_lines: dict[str, list[str]],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    start_num = file_suffix_num(Path(section.file_start))
    end_num = file_suffix_num(Path(section.file_end))
    ordered_files = [Path(p) for p in sorted(file_lines, key=lambda p: int(Path(p).stem.rsplit("-", 1)[-1]))]
    relevant_files = [p for p in ordered_files if start_num <= file_suffix_num(p) <= end_num]

    current_letter: str | None = None
    entry_counter = 0
    node_counter = 0
    buffer = ""
    buffer_file: str | None = None
    buffer_letter: str | None = None
    buffer_line_raw: str = ""

    def flush_buffer() -> None:
        nonlocal buffer, buffer_file, buffer_letter, buffer_line_raw, entry_counter
        text = normalize_text(buffer)
        if not text:
            buffer = ""
            buffer_file = None
            buffer_letter = None
            buffer_line_raw = ""
            return
        if is_heading_line(text) and not buffer_has_ref(text, section.section_kind) and not IBID_RE.search(text):
            buffer = ""
            buffer_file = None
            buffer_letter = None
            buffer_line_raw = ""
            return
        refs_local, page_hints = extract_entry_refs(text, section.section_kind)
        entry_counter += 1
        entry_key = f"{section.section_key}:entry:{entry_counter:04d}"
        entry_kind = "lemma"
        if not refs_local and IBID_RE.search(text):
            entry_kind = "cross_reference"
        elif not refs_local and not page_hints and (text.startswith("LIBER ") or text.startswith("EPISTOLA ") or text.startswith("CARMEN ") or text.startswith("OPUSCUL.") or text.startswith("DICTIONES.") or text.startswith("APPENDIX.") or text.startswith("ORDO RERUM")):
            entry_kind = "heading_group"
        lemma = text
        if refs_local:
            first_raw = refs_local[0]["page_ref_raw"]
            cut = text.find(first_raw)
            if cut > 0:
                lemma = normalize_text(text[:cut].rstrip(" ,;:."))
        elif IBID_RE.search(text):
            lemma = normalize_text(re.split(r"\bibid\.?\b", text, maxsplit=1, flags=re.IGNORECASE)[0].rstrip(" ,;:."))
        elif section.section_kind == "ordo_rerum" and text.startswith("-"):
            lemma = normalize_text(re.sub(r"^[-—\s]+", "", text))
        lemma_display = lemma if lemma else None
        inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = None
        if refs_local:
            target_file_best = page_map.get(refs_local[0]["page_ref_int"])
        if target_file_best is None:
            target_file_best = buffer_file
        if inferred_page is None and entries:
            inferred_page = entries[-1].get("inferred_printed_page")
            if target_file_best is None:
                target_file_best = entries[-1].get("target_file_best")
        confidence = 0.92 if refs_local else 0.68
        if section.section_kind == "ordo_rerum":
            confidence = 0.9 if refs_local else 0.76
        if not refs_local and not IBID_RE.search(text):
            confidence = min(confidence, 0.7)
        entry = {
            "entry_key": entry_key,
            "section_key": section.section_key,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": entry_kind,
            "lemma_raw": lemma if lemma else None,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm(lemma),
            "lemma_sort": sort_key(lemma),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": buffer_letter if section.section_kind != "ordo_rerum" else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": section.file_start,
            "editorial_anchor_file": buffer_file or section.anchor_file,
            "target_file_best": target_file_best,
            "confidence": confidence,
            "raw_json": {
                "source_file": buffer_file or section.anchor_file,
                "section_kind": section.section_kind,
            },
        }
        if not refs_local and IBID_RE.search(text):
            entry["raw_json"]["ibid"] = True
        entries.append(entry)
        if refs_local:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma if lemma else text,
                    "query_names": [q for q in _unique_preserve_order([lemma if lemma else text, text, normalize_text(lemma if lemma else text).split(",", 1)[0]]) if q],
                    "page_hints": [str(x) for x in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": text,
                }
            )
            for ref in refs_local:
                ref_entry = {
                    "entry_key": entry_key,
                    "ref_order": ref["ref_order"],
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["page_ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": page_map.get(ref["page_ref_int"]),
                    "target_file_probability": 0.9 if page_map.get(ref["page_ref_int"]) else None,
                    "section_start_file": section.file_start,
                    "editorial_anchor_file": buffer_file or section.anchor_file,
                    "confidence": confidence if ref["ref_kind"] == "editorial_page" else min(confidence, 0.86),
                    "raw_json": {},
                }
                refs.append(ref_entry)
        buffer = ""
        buffer_file = None
        buffer_letter = None
        buffer_line_raw = ""

    for path in relevant_files:
        lines = file_lines[str(path)]
        current_idx = 0
        while current_idx < len(lines):
            line = lines[current_idx]
            current_idx += 1
            if not line:
                continue
            if SECTION_HEADING_RE.search(line):
                flush_buffer()
                continue
            if section.section_kind != "ordo_rerum" and LETTER_RE.match(line):
                flush_buffer()
                current_letter = line
                node_counter += 1
                node_key = f"{section.section_key}:node:{node_counter:03d}"
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section.section_key,
                        "parent_node_key": None,
                        "node_order": node_counter,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": line,
                        "label_sort": line,
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path), "section_kind": section.section_kind},
                    }
                )
                continue
            if is_heading_line(line) and not buffer and not buffer_has_ref(line, section.section_kind):
                # Keep major headings out of entries; they are section-level structure.
                continue
            if buffer and should_continue(buffer, line, section.section_kind):
                buffer = f"{buffer} {line}"
                continue
            if buffer:
                flush_buffer()
            buffer = line
            buffer_file = str(path)
            buffer_letter = current_letter
            buffer_line_raw = line
        # page boundary flush only for section 1/2 if buffer accumulated a line that clearly ends there.
    flush_buffer()
    # Deduplicate helper entries by entry_id.
    seen_helper: set[str] = set()
    unique_helper_entries: list[dict[str, Any]] = []
    for item in helper_entries:
        if item["entry_id"] in seen_helper:
            continue
        seen_helper.add(item["entry_id"])
        unique_helper_entries.append(item)
    return entries, refs, nodes, unique_helper_entries


def build_helper_request(
    *,
    volume_id: str,
    source_root: Path,
    helper_entries: list[dict[str, Any]],
    helper_request_json: Path,
) -> dict[str, Any]:
    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    return request


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
    proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def merge_helper_into_entries(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    if not helper_output:
        return
    by_entry: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        by_entry[item.get("entry_id")] = item
    # The helper output naming is not identical to the final payload, so preserve only
    # useful evidence for entries that have a direct correspondence.
    for entry in entries:
        helper = by_entry.get(entry["entry_key"])
        if not helper:
            continue
        raw = entry.setdefault("raw_json", {})
        raw["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": helper.get("best_candidate"),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL063 alphabetical index payload.")
    ap.add_argument("--volume-id", default="PL063")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": args.volume_id,
        "updated_at": now_iso(),
        "current_focus": "Build PL063 alphabetical payload from OCR tail indexes and ordo rerum",
        "completed": [
            "ocr files discovered",
            "section boundaries identified",
            "helper request generated",
        ],
        "pending": [
            "review helper output for ambiguous locator lines",
            "write canonical payload and validate",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "Do not collapse page refs, lines, and cited references.",
        ],
    }
    write_json(todo_path, todo)

    files = discover_text_files(args.source_root)
    page_map = build_page_map(files)
    section1, section2, section3, file_lines = determine_sections(files)

    entries1, refs1, nodes1, helper1 = extract_section_entries(section=section1, file_lines=file_lines, page_map=page_map)
    entries2, refs2, nodes2, helper2 = extract_section_entries(section=section2, file_lines=file_lines, page_map=page_map)
    entries3, refs3, nodes3, helper3 = extract_section_entries(section=section3, file_lines=file_lines, page_map=page_map)

    entries = entries1 + entries2 + entries3
    refs = refs1 + refs2 + refs3
    nodes = nodes1 + nodes2 + nodes3
    helper_entries = helper1 + helper2 + helper3

    helper_request = build_helper_request(
        volume_id=args.volume_id,
        source_root=args.source_root,
        helper_entries=helper_entries,
        helper_request_json=args.helper_request_json,
    )
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    merge_helper_into_entries(entries, refs, helper_output)

    volume = {
        "volume_id": args.volume_id,
        "collection": "PL",
        "source_root": str(args.source_root),
        "volume_label": "Patrologia Latina 63",
        "notes": [
            "Alphabetical and analytical indices are serialized from the OCR tail of the volume.",
            "The final ORDO RERUM block is preserved as a separate editorial-closure section.",
            "Helper resolution was generated for material locator lines, but the payload remains OCR-led.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the visible index blocks and the editorial closure block conservatively from the OCR tail. Some noisy lines remain partially parsed because the OCR has dense split lines and corrupted running titles.",
        "evidence_files": [
            str(args.source_root / "865f3843-e11a-45cb-b593-aa35b4defd46-685.txt"),
            str(args.source_root / "865f3843-e11a-45cb-b593-aa35b4defd46-686.txt"),
            str(args.source_root / "d4b4a3ee-99e3-4ec0-b498-8b2315b17dd2-721.txt"),
            str(args.source_root / "d4b4a3ee-99e3-4ec0-b498-8b2315b17dd2-728.txt"),
        ],
    }
    notes = [
        "Section 1 is the subject index (analytic_subject).",
        "Section 2 is the vocabulary index (alphabetical_general).",
        "Section 3 is the ordo rerum closure block (ordo_rerum).",
        "Some OCR headers are corrupted; printed-page fields are therefore conservative.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": [
            {
                "section_key": section1.section_key,
                "volume_id": args.volume_id,
                "work_key": None,
                "section_order": 1,
                "section_kind": section1.section_kind,
                "heading_raw": section1.heading_raw,
                "heading_norm": section1.heading_norm,
                "heading_letter": None,
                "page_start": section1.page_start,
                "page_end": section1.page_end,
                "file_start": section1.file_start,
                "file_end": section1.file_end,
                "confidence": 0.94,
                "raw_json": {
                    "section_kind_reason": "Analytical subject index block with letter headings and topical entries.",
                    "evidence_files": [section1.file_start, section1.file_end],
                },
            },
            {
                "section_key": section2.section_key,
                "volume_id": args.volume_id,
                "work_key": None,
                "section_order": 2,
                "section_kind": section2.section_kind,
                "heading_raw": section2.heading_raw,
                "heading_norm": section2.heading_norm,
                "heading_letter": None,
                "page_start": section2.page_start,
                "page_end": section2.page_end,
                "file_start": section2.file_start,
                "file_end": section2.file_end,
                "confidence": 0.93,
                "raw_json": {
                    "section_kind_reason": "Alphabetical vocabulary index with the printed note that the first number is the page and the second is the line.",
                    "evidence_files": [section2.file_start, section2.file_end],
                },
            },
            {
                "section_key": section3.section_key,
                "volume_id": args.volume_id,
                "work_key": None,
                "section_order": 3,
                "section_kind": section3.section_kind,
                "heading_raw": section3.heading_raw,
                "heading_norm": section3.heading_norm,
                "heading_letter": None,
                "page_start": section3.page_start,
                "page_end": section3.page_end,
                "file_start": section3.file_start,
                "file_end": section3.file_end,
                "confidence": 0.92,
                "raw_json": {
                    "section_kind_reason": "Editorial closure / contents block after the vocabulary index.",
                    "evidence_files": [section3.file_start, section3.file_end],
                },
            },
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    # Persist intermediate fragments for reruns.
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": args.volume_id, "updated_at": now_iso(), "helper_entry_count": len(helper_entries)})

    write_json(args.output_file, payload)


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


if __name__ == "__main__":
    main()
