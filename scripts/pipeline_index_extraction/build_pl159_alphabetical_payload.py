#!/usr/bin/env python3
"""Usage: build the PL159 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl159_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL159/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL159_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL159_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL159 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL159_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL159"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 159"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_START_SEQ = 550
INDEX_END_SEQ = 577
ORDO_START_SEQ = 578
ORDO_END_SEQ = 586

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX IN S. ANSELMUM.",
        "section_kind_reason": "Alphabetical analytical index of subjects, biblical themes, and named persons in the Anselm volume, organized by letter groups and remissions.",
        "file_start_seq": INDEX_START_SEQ,
        "file_end_seq": INDEX_END_SEQ,
        "start_marker": "INDEX IN S. ANSELMUM",
        "stop_marker": "ORDO RERUM",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "section_kind_reason": "Closing editorial contents table for the tome, distinct from the alphabetical index that precedes it.",
        "file_start_seq": ORDO_START_SEQ,
        "file_end_seq": ORDO_END_SEQ,
        "start_marker": "ORDO RERUM",
        "stop_marker": "FINIS TOMI",
    },
]

BLOCK_NOISE_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LETTER_DOT_RE = re.compile(r"^([A-ZÆŒ])\.\s*(.+)$")
SPACE_RE = re.compile(r"\s+")
PAGE_RE = re.compile(
    r"(?<!\d)(?P<num>\d{1,4})(?:\s*[-–]\s*(?P<end>\d{1,4}))?(?:\s*(?P<tail>et\s+seqq?\.?|et\s+seq\.|seqq\.|seq\.|etc\.))?",
    re.IGNORECASE,
)
REMISSION_RE = re.compile(r"^(?:V\.|Vid\.?|Vide|Voir|Cf\.?|Id\.?)\b", re.IGNORECASE)
SECTION_HEADING_RE = re.compile(r"^(?:INDEX\s+IN\s+S\.\s+ANSELMUM\.?|ORDO\s+RERUM(?:\s+QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?)$", re.IGNORECASE)


@dataclass(slots=True)
class Segment:
    text: str
    source_file: str
    current_letter: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = SPACE_RE.sub(" ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def parse_page(path: Path) -> dict[str, Any]:
    return parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))


def clean_lines(path: Path) -> list[str]:
    parsed = parse_page(path)
    lines: list[str] = []
    for raw in (parsed.get("body_text") or "").splitlines():
        line = normalize(raw)
        if not line or BLOCK_NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_page(path)
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in PAGE_RE.finditer(header):
            page_map.setdefault(int(match.group("num")), str(path))
    return page_map


def locate_text_window(files: list[Path], start_seq: int, end_seq: int) -> list[Path]:
    return [path for path in files if start_seq <= file_seq(path) <= end_seq]


def is_heading_group(text: str, refs: list[dict[str, Any]]) -> bool:
    if refs:
        return False
    cleaned = text.strip(" ,;:.")
    if not cleaned:
        return False
    return cleaned.isupper() and len(cleaned.split()) <= 4


def is_cross_reference(text: str, refs: list[dict[str, Any]]) -> bool:
    if refs:
        return False
    stripped = text.strip()
    return bool(REMISSION_RE.match(stripped) or re.search(r"\bVide\b", stripped, re.IGNORECASE))


def extract_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in PAGE_RE.finditer(text):
        num = int(match.group("num"))
        end = match.group("end")
        tail = match.group("tail")
        raw = match.group(0).strip().rstrip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_range" if end else "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(num) if end else None,
                "range_end_raw": str(end) if end else None,
                "tail": tail,
            }
        )
    return refs


def infer_lemma(text: str, refs: list[dict[str, Any]]) -> str | None:
    stripped = text.strip(" ,;:.")
    if not stripped:
        return None
    if not refs:
        if REMISSION_RE.match(stripped):
            return None
        if "." in stripped:
            head = stripped.split(".", 1)[0].strip(" ,;:.")
            return head or None
        if ":" in stripped:
            head = stripped.split(":", 1)[0].strip(" ,;:.")
            return head or None
        return stripped
    first = refs[0]["ref_raw"]
    idx = text.find(first)
    head = text[:idx].strip(" ,;:.") if idx > 0 else stripped
    if "." in head:
        head = head.split(".", 1)[0].strip(" ,;:.")
    if ":" in head and len(head.split()) > 1:
        head = head.split(":", 1)[0].strip(" ,;:.")
    return head or None


def make_query_names(entry_raw: str, lemma_raw: str | None) -> list[str]:
    values = [lemma_raw, entry_raw]
    if lemma_raw and "." in lemma_raw:
        values.append(lemma_raw.replace(".", ""))
    if entry_raw and "." in entry_raw:
        values.append(entry_raw.replace(".", ""))
    if lemma_raw:
        values.append(normalize(lemma_raw))
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out[:5]


def collect_segments(section: dict[str, Any], files: list[Path]) -> list[Segment]:
    collected: list[Segment] = []
    buffer = ""
    buffer_source = ""
    current_letter: str | None = None

    def flush_buffer() -> None:
        nonlocal buffer, buffer_source
        if not buffer:
            return
        chunk = normalize(buffer)
        if chunk and not SECTION_HEADING_RE.fullmatch(chunk):
            collected.append(Segment(text=chunk, source_file=buffer_source, current_letter=current_letter))
        buffer = ""
        buffer_source = ""

    for path in files:
        if not (section["file_start_seq"] <= file_seq(path) <= section["file_end_seq"]):
            continue
        for line in clean_lines(path):
            upper = line.upper()
            if re.fullmatch(r"\d{1,4}", line):
                continue
            letter_dot = LETTER_DOT_RE.fullmatch(line)
            if letter_dot:
                flush_buffer()
                current_letter = letter_dot.group(1)
                line = letter_dot.group(2).strip()
                upper = line.upper()
                if not line:
                    continue
            if re.match(r"^\d{1,4}\.\s+", line):
                if buffer:
                    if buffer.endswith("-"):
                        buffer = f"{buffer[:-1]}{line.lstrip()}"
                    else:
                        buffer = f"{buffer} {line}"
                    buffer_source = str(path)
                continue
            if section["section_kind"] == "analytic_subject":
                if "ORDO RERUM" in upper:
                    flush_buffer()
                    return collected
            else:
                if "FINIS TOMI" in upper:
                    flush_buffer()
                    return collected
                if "Digitized by Google" in line:
                    continue

            if SECTION_HEADING_RE.search(upper):
                continue

            if LETTER_RE.fullmatch(line):
                flush_buffer()
                current_letter = line
                collected.append(Segment(text=line, source_file=str(path), current_letter=current_letter))
                continue

            if not buffer:
                buffer = line
                buffer_source = str(path)
                continue

            if line[0].islower() or line.startswith(("-", "—", "(", "[", "·")) or buffer.endswith("-"):
                if buffer.endswith("-"):
                    buffer = f"{buffer[:-1]}{line.lstrip()}"
                else:
                    buffer = f"{buffer} {line}"
                buffer_source = str(path)
                continue

            flush_buffer()
            buffer = line
            buffer_source = str(path)

    flush_buffer()
    return collected


def build_helper_request(entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not entry["refs"]:
            continue
        page_hints = []
        page_hint_ints = []
        for ref in entry["refs"]:
            raw = str(ref["page_ref_raw"])
            if raw not in page_hints:
                page_hints.append(raw)
            if ref["page_ref_int"] not in page_hint_ints:
                page_hint_ints.append(ref["page_ref_int"])
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": entry.get("query_names") or make_query_names(entry["entry_raw"], entry.get("lemma_raw")),
                "page_hints": page_hints[:4],
                "page_hint_ints": page_hint_ints[:4],
                "context_raw": entry["entry_raw"],
            }
        )
        if len(helper_entries) >= 24:
            break
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if item.get("entry_id")}


def segment_entry_payload(
    section: dict[str, Any],
    segment: Segment,
    page_map: dict[int, str],
    section_start_file: str,
    helper_item: dict[str, Any] | None,
    entry_order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str | None]:
    refs = extract_refs(segment.text)
    lemma_raw = infer_lemma(segment.text, refs)
    entry_kind = "lemma"
    if is_cross_reference(segment.text, refs):
        entry_kind = "cross_reference"
    elif is_heading_group(segment.text, refs):
        entry_kind = "heading_group"

    entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
    source_file = segment.source_file
    target_file_best = source_file
    current_letter = segment.current_letter
    if helper_item and helper_item.get("best_candidate", {}).get("file"):
        target_file_best = helper_item["best_candidate"]["file"]

    ref_rows: list[dict[str, Any]] = []
    for ref_order, ref in enumerate(refs, start=1):
        target_file = page_map.get(ref["page_ref_int"])
        ref_rows.append(
            {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": ref["ref_kind"],
                "ref_raw": ref["ref_raw"],
                "page_ref_raw": ref["page_ref_raw"],
                "page_ref_int": ref["page_ref_int"],
                "page_ref_col": ref["page_ref_col"],
                "line_ref_raw": ref["line_ref_raw"],
                "range_start_raw": ref["range_start_raw"],
                "range_end_raw": ref["range_end_raw"],
                "target_file": target_file,
                "target_file_probability": 1.0 if target_file else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.97 if target_file else 0.72,
                "raw_json": {
                    "source_file": source_file,
                    "resolver": "page_map_header_text",
                    "section_kind": section["section_kind"],
                    "page_hint": ref["page_ref_int"],
                    "helper_status": helper_item.get("status") if helper_item else None,
                    "helper_best_candidate": helper_item.get("best_candidate") if helper_item else None,
                },
            }
        )

    entry_payload = {
        "entry_key": entry_key,
        "section_key": section["section_key"],
        "parent_node_key": None,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": sort_norm(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": segment.text,
        "context_raw": segment.text,
        "heading_letter": current_letter,
        "inferred_printed_page": None,
        "section_start_file": section_start_file,
        "editorial_anchor_file": source_file,
        "target_file_best": target_file_best,
        "confidence": 0.79 if refs else 0.72,
        "raw_json": {
            "source_file": source_file,
            "section_kind": section["section_kind"],
            "section_kind_reason": section["section_kind_reason"],
            "query_names": make_query_names(segment.text, lemma_raw),
        },
    }
    if helper_item:
        entry_payload["raw_json"]["helper_status"] = helper_item.get("status")
        entry_payload["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
        entry_payload["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
        entry_payload["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
        entry_payload["raw_json"]["helper_top_candidates"] = (helper_item.get("top_candidates") or [])[:5]
        if helper_item.get("best_candidate", {}).get("file"):
            entry_payload["target_file_best"] = helper_item["best_candidate"]["file"]

    return entry_payload, ref_rows, [], current_letter


def build_section_payload(
    section: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
    helper_output: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    segments = collect_segments(section, files)
    helper_by_id = helper_map(helper_output or {})
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    section_start_file = str(next((p for p in files if section["file_start_seq"] <= file_seq(p) <= section["file_end_seq"]), files[0]))

    current_letter_key: str | None = None
    current_letter_label: str | None = None
    node_order = 0
    entry_order = 0

    for segment in segments:
        if section["section_kind"] == "analytic_subject" and LETTER_RE.fullmatch(segment.text):
            node_order += 1
            current_letter_label = segment.text
            current_letter_key = f"{VOLUME_ID}:node:{section['section_order']:02d}:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_letter_key,
                    "section_key": section["section_key"],
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": segment.text,
                    "label_norm": sort_norm(segment.text),
                    "label_sort": sort_norm(segment.text),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": segment.source_file, "role": "alphabetic_letter"},
                }
            )
            continue
        if section["section_kind"] == "analytic_subject" and segment.current_letter and segment.current_letter != current_letter_label:
            node_order += 1
            current_letter_label = segment.current_letter
            current_letter_key = f"{VOLUME_ID}:node:{section['section_order']:02d}:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_letter_key,
                    "section_key": section["section_key"],
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": segment.current_letter,
                    "label_norm": sort_norm(segment.current_letter),
                    "label_sort": sort_norm(segment.current_letter),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": segment.source_file, "role": "alphabetic_letter"},
                }
            )

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
        helper_item = helper_by_id.get(entry_key)
        entry_payload, ref_rows, _, _ = segment_entry_payload(
            section=section,
            segment=segment,
            page_map=page_map,
            section_start_file=section_start_file,
            helper_item=helper_item,
            entry_order=entry_order,
        )
        entry_payload["parent_node_key"] = current_letter_key if section["section_kind"] == "analytic_subject" else None
        entry_payload["heading_letter"] = current_letter_label
        if ref_rows and current_letter_key:
            entry_payload["parent_node_key"] = current_letter_key
        if ref_rows:
            entry_payload["confidence"] = 0.92 if helper_item and helper_item.get("status") == "resolved" else 0.84
            # inferred printed page is the first cited page when available
            entry_payload["inferred_printed_page"] = ref_rows[0]["page_ref_int"]
        entries.append(entry_payload)
        refs.extend(ref_rows)

    return entries, refs, nodes, segments


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    skip_helper: bool = False,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    page_map = build_page_map(files)
    helper_output: dict[str, Any] = {}

    # First pass without helper to generate a request.
    all_entries: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        sect_files = locate_text_window(files, section["file_start_seq"], section["file_end_seq"])
        entries, refs, _, _ = build_section_payload(section, sect_files, page_map, None)
        for entry in entries:
            entry["refs"] = [ref for ref in refs if ref["entry_key"] == entry["entry_key"]]
        all_entries.extend(entries)

    helper_request = build_helper_request(all_entries, source_root)
    write_json(helper_request_json, helper_request)
    if skip_helper and helper_output_json.exists():
        helper_output = read_json(helper_output_json, {})
    elif skip_helper:
        helper_output = {}
    else:
        helper_output = run_helper(helper_request_json, helper_output_json)
    helper_by_id = helper_map(helper_output)

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        sect_files = locate_text_window(files, section["file_start_seq"], section["file_end_seq"])
        section_entries, section_refs, section_nodes, _ = build_section_payload(section, sect_files, page_map, helper_output)
        entries.extend(section_entries)
        refs.extend(section_refs)
        nodes.extend(section_nodes)

        header_pages: list[int] = []
        for path in sect_files:
            parsed = parse_page(path)
            header = normalize(parsed.get("header_text") or "")
            if not header:
                continue
            for match in PAGE_RE.finditer(header):
                header_pages.append(int(match.group("num")))
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": sort_norm(section["heading_raw"]),
                "heading_letter": None,
                "page_start": min(header_pages) if header_pages else None,
                "page_end": max(header_pages) if header_pages else None,
                "file_start": str(sect_files[0]) if sect_files else None,
                "file_end": str(sect_files[-1]) if sect_files else None,
                "confidence": 0.97 if section["section_kind"] == "analytic_subject" else 0.99,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "source_files": [str(p) for p in sect_files],
                    "helper_status": helper_output.get("status"),
                    "helper_entry_count": len(helper_output.get("entries", [])),
                },
            }
        )

    for entry in entries:
        entry_refs = [ref for ref in refs if ref["entry_key"] == entry["entry_key"]]
        entry["raw_json"]["ref_count"] = len(entry_refs)
        if entry_refs:
            entry["refs"] = entry_refs
        entry.pop("refs", None)

    # The payload only stores refs in the top-level refs array.
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
        "notes": "Recovered the closing analytical index and the final Ordo Rerum contents table from the OCR tail.",
    }
    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the index entries, cross-references, and the final Ordo Rerum contents table from the OCR tail window.",
        "evidence_files": [
            str(next(path for path in files if file_seq(path) == INDEX_START_SEQ)),
            str(next(path for path in files if file_seq(path) == ORDO_START_SEQ)),
            str(next(path for path in files if file_seq(path) == ORDO_END_SEQ)),
        ],
    }
    notes = [
        "The first section is an analytical alphabetical index (INDEX IN S. ANSELMUM) with letter-group nodes and remissions.",
        "The second section is the closing Ordo Rerum contents table, kept separate from the index proper.",
        "OCR page numbers, cited references, and OCR file suffixes are preserved as separate numbering systems.",
    ]
    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
    }

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Final payload written and helper run completed.",
        "completed": [
            "Separated the analytical index and the Ordo Rerum sections",
            "Built helper request and ran index_target_locator.py",
            "Assembled sections, entries, refs, nodes, and payload fragments",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact, including remissions and cross-references.",
        ],
    }

    fragments = {
        "manifest.json": manifest,
        "volume.json": volume,
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": scripture_refs,
        "coverage.json": coverage,
        "notes.json": notes,
        "todo.json": todo,
    }
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL159 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--skip-helper", action="store_true", help="Reuse an existing helper output or skip helper execution.")
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(
        args.source_root,
        args.helper_request_json,
        args.helper_output_json,
        args.intermediate_dir,
        skip_helper=args.skip_helper,
    )
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
