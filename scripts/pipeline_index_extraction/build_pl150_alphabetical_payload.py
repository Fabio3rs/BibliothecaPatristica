#!/usr/bin/env python3
"""Usage: build the PL150 alphabetical/index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl150_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL150/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL150_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL150_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL150 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL150_alphabetical_indices.json
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
VOLUME_ID = "PL150"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 150"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_START_SEQ = 824
INDEX_END_SEQ = 838
ORDO_START_SEQ = 839
ORDO_END_SEQ = 846

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET VERBORUM MEMORABILIUM QUÆ IN OPERIBUS B. LANFRANCI CONTINENTUR.",
        "section_kind_reason": "Alphabetical analytical index of memorable things and words cited in Lanfranc's works, with dense alphabetical letter groups and many cross-references.",
        "file_start_seq": INDEX_START_SEQ,
        "file_end_seq": INDEX_END_SEQ,
        "start_marker": "INDEX RERUM ET VERBORUM MEMORABILIUM",
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
        "stop_marker": None,
    },
]

BLOCK_NOISE_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
REF_RE = re.compile(
    r"(?<!\w)(?:(?P<prefix>[avAV])\.\s*)?(?P<num>\d{1,4})(?:\s*(?P<tail>et\s+seqq?\.?|et\s+seq\.|seqq\.|seq\.))?",
    re.IGNORECASE,
)
ENTRY_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ][a-z])")
SECTION_HEADING_RE = re.compile(
    r"^(?:INDEX\s+RERUM\s+ET\s+VERBORUM\s+MEMORABILIUM.*|ORDO\s+RERUM.*)$",
    re.IGNORECASE,
)
BARE_REMISSION_RE = re.compile(r"^(?:V\.|Vid\.?|Vide|Voir|Cf\.?|Id\.?)\s+.*", re.IGNORECASE)


@dataclass(slots=True)
class Segment:
    text: str
    source_file: str
    section_key: str
    section_kind: str
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
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> dict[str, str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "header_text": parsed.get("header_text") or "",
        "footer_text": parsed.get("footer_text") or "",
        "all_text": parsed.get("all_text") or "",
    }


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        line = normalize(raw)
        if not line or BLOCK_NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = extract_blocks(path)
        for text in (parsed["header_text"], parsed["footer_text"]):
            for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def is_section_heading(line: str) -> bool:
    return bool(SECTION_HEADING_RE.fullmatch(line))


def merge_logical_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            merged.append(buffer.strip())
            buffer = ""

    for line in lines:
        if not line:
            continue
        if LETTER_RE.fullmatch(line) or is_section_heading(line):
            flush()
            merged.append(line)
            continue
        if not buffer:
            buffer = line
            continue
        if line[0].islower() or line.startswith(("-", "—", "(", "[", "·")) or buffer.endswith("-"):
            if buffer.endswith("-"):
                buffer = f"{buffer[:-1]}{line.lstrip()}"
            else:
                buffer = f"{buffer} {line}"
            continue
        flush()
        buffer = line
    flush()
    return merged


def split_entry_segments(text: str) -> list[str]:
    parts = [part.strip() for part in ENTRY_SPLIT_RE.split(text) if part.strip()]
    return parts or [text]


def extract_refs(segment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in REF_RE.finditer(segment):
        raw = match.group(0).strip().rstrip(" ,;:.")
        num = int(match.group("num"))
        tail = match.group("tail")
        ref_kind = "editorial_range" if tail else "editorial_page"
        refs.append(
            {
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(num) if tail else None,
                "range_end_raw": None,
            }
        )
    return refs


def strip_lemma(segment: str, refs: list[dict[str, Any]]) -> str | None:
    if not refs:
        return normalize(segment)
    first = refs[0]["ref_raw"]
    idx = segment.find(first)
    if idx > 0:
        candidate = segment[:idx]
    else:
        candidate = segment
    candidate = candidate.strip(" ,;:.")
    candidate = re.sub(r"\s+[—-]\s*$", "", candidate).strip(" ,;:.")
    return normalize(candidate)


def normalize_segment_text(segment: Segment) -> str:
    text = segment.text
    if segment.section_kind == "analytic_subject" and segment.current_letter:
        prefix = f"{segment.current_letter} "
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip()
    return text


def is_cross_reference(segment: str, refs: list[dict[str, Any]]) -> bool:
    if refs:
        return False
    return bool(BARE_REMISSION_RE.fullmatch(segment) or re.search(r"^(?:V\.|Vid\.?|Vide|Voir|Cf\.?|Id\.?)\b", segment, re.IGNORECASE))


def is_letter_heading(segment: str) -> bool:
    return bool(LETTER_RE.fullmatch(segment))


def is_heading_group(segment: str, refs: list[dict[str, Any]]) -> bool:
    if refs or is_cross_reference(segment, refs) or is_letter_heading(segment):
        return False
    cleaned = segment.strip(" .;:")
    if not cleaned:
        return False
    if cleaned.isupper():
        return True
    if re.match(r"^(?:[A-ZÆŒ][A-Za-zÆŒæœ'\-]+\s+){2,}[A-ZÆŒ][A-Za-zÆŒæœ'\-]+\.?$", cleaned):
        return True
    return False


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
    seen_heading = False
    current_letter: str | None = None

    def flush_buffer() -> None:
        nonlocal buffer, buffer_source
        if not buffer:
            return
        for chunk in split_entry_segments(buffer):
            chunk = normalize(chunk) or ""
            if not chunk:
                continue
            if is_section_heading(chunk):
                continue
            collected.append(
                Segment(
                    text=chunk,
                    source_file=buffer_source,
                    section_key=section["section_key"],
                    section_kind=section["section_kind"],
                    current_letter=current_letter,
                )
            )
        buffer = ""
        buffer_source = ""

    for path in files:
        if file_seq(path) < section["file_start_seq"] or file_seq(path) > section["file_end_seq"]:
            continue
        for line in clean_lines(path):
            if section["section_kind"] == "analytic_subject" and not seen_heading:
                if "INDEX RERUM ET VERBORUM MEMORABILIUM" in line.upper():
                    seen_heading = True
                continue
            if section["section_kind"] == "ordo_rerum" and not seen_heading:
                if "ORDO RERUM" in line.upper():
                    seen_heading = True
                continue
            if section["section_kind"] == "analytic_subject" and "ORDO RERUM" in line.upper():
                flush_buffer()
                return collected
            if section["section_kind"] == "ordo_rerum" and "Digitized by Google" in line:
                continue

            if LETTER_RE.fullmatch(line):
                flush_buffer()
                current_letter = line
                collected.append(
                    Segment(
                        text=line,
                        source_file=str(path),
                        section_key=section["section_key"],
                        section_kind=section["section_kind"],
                        current_letter=current_letter,
                    )
                )
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


def locate_segment_source(segment: str, files: list[Path], section: dict[str, Any]) -> str:
    # Best-effort: use the first file in the section unless a better match is obvious.
    if section["section_kind"] == "analytic_subject":
        if re.search(r"^\s*[A-ZÆŒ]\s*$", segment):
            for path in files:
                if file_seq(path) >= section["file_start_seq"] and file_seq(path) <= section["file_end_seq"]:
                    return str(path)
    return str(next(path for path in files if file_seq(path) == section["file_start_seq"]))


def locate_text_window(files: list[Path], start_seq: int, end_seq: int) -> list[Path]:
    return [path for path in files if start_seq <= file_seq(path) <= end_seq]


def build_helper_request(entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_refs = extract_refs(entry.get("entry_raw") or "")
        if not entry_refs and entry.get("inferred_printed_page") is None:
            continue
        page_hints = []
        page_hint_ints = []
        for ref in entry_refs:
            raw = str(ref["page_ref_raw"])
            if raw not in page_hints:
                page_hints.append(raw)
            if ref["page_ref_int"] not in page_hint_ints:
                page_hint_ints.append(ref["page_ref_int"])
        if not page_hints and isinstance(entry.get("inferred_printed_page"), int):
            page_hints.append(str(entry["inferred_printed_page"]))
            page_hint_ints.append(int(entry["inferred_printed_page"]))
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
    helper_entries = helper_entries[:24]
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


def make_entries_and_refs(
    section: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
    helper_output: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_letter_node: str | None = None
    current_letter_label: str | None = None
    entry_order = 0
    node_order = 0
    helper_by_id = helper_map(helper_output or {})

    segments = collect_segments(section, files)
    for segment in segments:
        segment_text = segment.text
        if section["section_kind"] == "analytic_subject" and current_letter_node is None:
            prefix_match = re.match(r"^([A-ZÆŒ])\s+(.+)$", segment_text)
            if prefix_match:
                current_letter_label = prefix_match.group(1)
                node_order += 1
                current_letter_node = f"{VOLUME_ID}:node:{section['section_order']:02d}:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_letter_node,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter_label,
                        "label_norm": sort_norm(current_letter_label),
                        "label_sort": sort_norm(current_letter_label),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": segment.source_file, "role": "alphabetic_letter"},
                    }
                )
                segment_text = prefix_match.group(2)
        refs_for_segment = extract_refs(segment_text)
        lemma_raw = strip_lemma(segment_text, refs_for_segment)
        if section["section_kind"] == "analytic_subject" and is_letter_heading(segment.text):
            node_order += 1
            current_letter_node = f"{VOLUME_ID}:node:{section['section_order']:02d}:{node_order:03d}"
            current_letter_label = segment.text
            nodes.append(
                {
                    "node_key": current_letter_node,
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

        entry_kind = "cross_reference" if is_cross_reference(segment_text, refs_for_segment) else "lemma"
        if not refs_for_segment and is_heading_group(segment_text, refs_for_segment):
            entry_kind = "heading_group"

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
        helper_item = helper_by_id.get(entry_key)
        query_names = make_query_names(segment_text, lemma_raw)
        first_target = None
        if refs_for_segment:
            for ref in refs_for_segment:
                ref["target_file"] = page_map.get(ref["page_ref_int"])
                ref["target_file_probability"] = 1.0 if ref["target_file"] else None
                ref["section_start_file"] = str(files[0])
                ref["editorial_anchor_file"] = segment.source_file
                ref["confidence"] = 0.97 if ref["target_file"] else 0.72
                ref["raw_json"] = {
                    "source_file": segment.source_file,
                    "segment_raw": segment_text,
                    "resolver": "page_map_header_footer",
                }
                if helper_item:
                    ref["raw_json"]["helper_status"] = helper_item.get("status")
                    ref["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": len([r for r in refs if r["entry_key"] == entry_key]) + 1,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": ref["target_file"],
                        "target_file_probability": ref["target_file_probability"],
                        "section_start_file": ref["section_start_file"],
                        "editorial_anchor_file": ref["editorial_anchor_file"],
                        "confidence": ref["confidence"],
                        "raw_json": ref["raw_json"],
                    }
                )
                if first_target is None and ref["target_file"]:
                    first_target = ref["target_file"]

        if refs_for_segment:
            first_ref_page = refs_for_segment[0]["page_ref_int"]
        else:
            first_ref_page = None

        target_best = first_target
        if target_best is None and helper_item and helper_item.get("best_candidate"):
            target_best = helper_item["best_candidate"].get("file")

        entry_payload = {
            "entry_key": entry_key,
            "section_key": section["section_key"],
            "parent_node_key": current_letter_node if section["section_kind"] == "analytic_subject" else None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": segment_text,
            "context_raw": segment_text,
            "heading_letter": current_letter_label,
            "inferred_printed_page": first_ref_page,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": segment.source_file,
            "target_file_best": target_best,
            "confidence": 0.79 if refs_for_segment else 0.72,
            "raw_json": {
                "source_file": segment.source_file,
                "section_kind": section["section_kind"],
                "section_kind_reason": section["section_kind_reason"],
                "query_names": query_names,
            },
        }
        if helper_item:
            entry_payload["raw_json"]["helper_status"] = helper_item.get("status")
            entry_payload["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
            entry_payload["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
            entry_payload["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
            entry_payload["raw_json"]["helper_top_candidates"] = (helper_item.get("top_candidates") or [])[:5]
            if helper_item.get("best_candidate", {}).get("file"):
                entry_payload["target_file_best"] = entry_payload["target_file_best"] or helper_item["best_candidate"]["file"]
        if section["section_kind"] == "analytic_subject" and current_letter_node:
            entry_payload["parent_node_key"] = current_letter_node
        if refs_for_segment and target_best:
            entry_payload["confidence"] = 0.92 if helper_item and helper_item.get("status") == "resolved" else 0.84
        entries.append(entry_payload)

    return entries, refs, nodes


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_text_files(source_root)
    page_map = build_page_map(files)
    helper_output: dict[str, Any] = {}

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        sect_files = locate_text_window(files, section["file_start_seq"], section["file_end_seq"])
        section_entries, section_refs, section_nodes = make_entries_and_refs(section, sect_files, page_map, None)
        entries.extend(section_entries)
        refs.extend(section_refs)
        nodes.extend(section_nodes)

        page_vals = [ref["page_ref_int"] for ref in section_refs if isinstance(ref.get("page_ref_int"), int)]
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
                "page_start": min(page_vals) if page_vals else None,
                "page_end": max(page_vals) if page_vals else None,
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

    helper_request = build_helper_request(entries, source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_by_id = helper_map(helper_output)

    # Rebuild entries/refs with helper-backed target evidence now that the final keys are known.
    sections = []
    nodes = []
    entries = []
    refs = []
    for section in SECTION_DEFS:
        sect_files = locate_text_window(files, section["file_start_seq"], section["file_end_seq"])
        section_entries, section_refs, section_nodes = make_entries_and_refs(section, sect_files, page_map, helper_output)
        entries.extend(section_entries)
        refs.extend(section_refs)
        nodes.extend(section_nodes)
        page_vals = [ref["page_ref_int"] for ref in section_refs if isinstance(ref.get("page_ref_int"), int)]
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
                "page_start": min(page_vals) if page_vals else None,
                "page_end": max(page_vals) if page_vals else None,
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

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
        "notes": "Recovered the closing analytical index and the final Ordo Rerum table from the OCR tail.",
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
        "The first section is an analytical alphabetical index (INDEX RERUM ET VERBORUM MEMORABILIUM) with letter groups and remissions.",
        "The second section is the closing Ordo Rerum contents table, kept separate from the index proper.",
        "OCR page-number sequences, appendical markers such as a./v., and the OCR file suffixes are preserved as distinct numbering systems.",
    ]
    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
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
    }
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

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
            "Keep OCR literals intact, including a./v. markers and cross-reference forms.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

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
    ap = argparse.ArgumentParser(description="Build the PL150 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output_file.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
