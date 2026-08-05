#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/pl195_build_payload.py

Build the PL195 alphabetical-index payload, helper request, and intermediate TODO
from the OCR files under teste/PL195/text.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL195/text"
OUT_PATH = ROOT / "data/alphabetical_index_payloads/PL195_alphabetical_indices.json"
HELPER_REQ_PATH = ROOT / "data/alphabetical_index_payloads/PL195_helper_request.json"
HELPER_OUT_PATH = ROOT / "data/alphabetical_index_payloads/PL195_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL195"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"


SECTION1_FILES = list(range(647, 653))
SECTION2_FILES = list(range(653, 657))


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_path(seq: int) -> Path:
    return SOURCE_ROOT / f"eb95e205-c0e5-45d3-bf88-7d9867cf51eb-{seq}.txt"


def read_text_block(path: Path) -> str:
    text = path.read_text(errors="ignore")
    blocks = re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', text, re.S)
    if not blocks:
        return ""
    lines = []
    for block in blocks:
        for raw in block.splitlines():
            s = raw.strip()
            if s:
                lines.append(s)
    return " ".join(lines)


def normalize_segment_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Remove stray printed-page numbers that leaked into the OCR stream before a new capitalized lemma.
    text = re.sub(r"(?<=\.)\s+\d{1,4}\s+(?=[A-ZÀ-Ü])", " ", text)
    text = re.sub(r"^\d{1,4}\.\s+(?=[A-ZÀ-Ü])", "", text)
    text = re.sub(r"^\d{1,4}\s+(?=[A-ZÀ-Ü])", "", text)
    return text.strip()


def split_segments(text: str) -> list[str]:
    text = normalize_segment_text(text)
    if not text:
        return []
    segments = re.split(r"(?<=\.)\s+(?=[A-ZÀ-Ü])", text)
    cleaned = []
    for seg in segments:
        seg = seg.strip()
        if seg:
            cleaned.append(seg)
    return cleaned


def strip_heading_letter(segment: str) -> tuple[str | None, str]:
    if re.fullmatch(r"[A-Z]", segment):
        return segment, ""
    m = re.match(r"^([A-Z])\s+(.*)$", segment)
    if m and len(m.group(2)) > 3:
        return m.group(1), m.group(2).strip()
    return None, segment


def normalize_latin(text: str) -> str:
    text = text.lower()
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^0-9a-zà-ž]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_page_refs(segment: str, section_kind: str) -> list[int]:
    # Section 2 is a contents table: the last number is usually the printed-page anchor.
    if section_kind == "ordo_rerum":
        nums = re.findall(r"(?<![A-Za-z])(\d{1,4})(?![A-Za-z])", segment)
        if not nums:
            return []
        try:
            return [int(nums[-1])]
        except ValueError:
            return []

    # Section 1 is an alphabetical index with multiple material references per line.
    nums = []
    for match in re.finditer(r"(?<![A-Za-z])(\d{1,4})(?![A-Za-z])", segment):
        value = int(match.group(1))
        nums.append(value)
    return nums


def header_page_numbers(path: Path) -> list[int]:
    text = path.read_text(errors="ignore").splitlines()
    hits = []
    for line in text[:8]:
        for m in re.finditer(r"(?<![A-Za-z])(\d{1,4})(?![A-Za-z])", line):
            hits.append(int(m.group(1)))
    # Keep first occurrences only.
    seen = set()
    out = []
    for n in hits:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def build_page_map() -> OrderedDict[int, str]:
    page_map: OrderedDict[int, str] = OrderedDict()
    for path in sorted(SOURCE_ROOT.glob("*.txt")):
        for n in header_page_numbers(path):
            page_map.setdefault(n, str(path))
    return page_map


def target_file_for(page_ref: int, page_map: OrderedDict[int, str]) -> str | None:
    return page_map.get(page_ref)


def parse_section(files: list[int], section_key: str, section_kind: str):
    page_map = build_page_map()
    entries = []
    refs = []
    helper_entries = []
    entry_counter = 0

    for seq in files:
        path = file_path(seq)
        text = read_text_block(path)
        if not text:
            continue
        segments = split_segments(text)

        for segment in segments:
            heading_letter, body = strip_heading_letter(segment)
            if heading_letter and not body:
                entry_counter += 1
                entry_key = f"PL195:entry:{entry_counter:04d}"
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "entry_order": entry_counter,
                        "entry_kind": "heading_group",
                        "lemma_raw": heading_letter,
                        "lemma_display": heading_letter,
                        "lemma_norm": heading_letter.lower(),
                        "lemma_sort": heading_letter.lower(),
                        "entry_raw": heading_letter,
                        "context_raw": heading_letter,
                        "heading_letter": heading_letter,
                        "inferred_printed_page": None,
                        "section_start_file": str(file_path(files[0])),
                        "editorial_anchor_file": str(path),
                        "target_file_best": None,
                        "confidence": 0.99,
                        "raw_json": {"source_file": str(path), "entry_kind_reason": "alphabetic heading"},
                    }
                )
                continue

            # Extract and remove a leading single-letter heading if present.
            stripped_heading, body2 = strip_heading_letter(body if body else segment)
            if stripped_heading and body2:
                entry_counter += 1
                entry_key = f"PL195:entry:{entry_counter:04d}"
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "entry_order": entry_counter,
                        "entry_kind": "heading_group",
                        "lemma_raw": stripped_heading,
                        "lemma_display": stripped_heading,
                        "lemma_norm": stripped_heading.lower(),
                        "lemma_sort": stripped_heading.lower(),
                        "entry_raw": stripped_heading,
                        "context_raw": stripped_heading,
                        "heading_letter": stripped_heading,
                        "inferred_printed_page": None,
                        "section_start_file": str(file_path(files[0])),
                        "editorial_anchor_file": str(path),
                        "target_file_best": None,
                        "confidence": 0.99,
                        "raw_json": {"source_file": str(path), "entry_kind_reason": "alphabetic heading"},
                    }
                )
                segment = body2

            segment = segment.strip()
            if not segment:
                continue

            entry_counter += 1
            entry_key = f"PL195:entry:{entry_counter:04d}"
            page_refs = extract_page_refs(segment, section_kind)
            unique_refs = []
            seen_refs = set()
            for p in page_refs:
                if p not in seen_refs:
                    seen_refs.add(p)
                    unique_refs.append(p)

            inferred_printed_page = unique_refs[0] if unique_refs else None
            target_best = target_file_for(inferred_printed_page, page_map) if inferred_printed_page else None
            entry_kind = "heading_group" if section_kind == "ordo_rerum" else "lemma"
            if re.fullmatch(r"[A-Z]", segment):
                entry_kind = "heading_group"
            confidence = 0.91 if unique_refs else 0.76
            if "ibid." in segment.lower():
                confidence = min(confidence, 0.72)

            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_counter,
                    "entry_kind": entry_kind,
                    "lemma_raw": segment,
                    "lemma_display": segment,
                    "lemma_norm": normalize_latin(segment),
                    "lemma_sort": normalize_latin(segment),
                    "entry_raw": segment,
                    "context_raw": segment,
                    "heading_letter": None,
                    "inferred_printed_page": inferred_printed_page,
                    "section_start_file": str(file_path(files[0])),
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_best,
                    "confidence": confidence,
                    "raw_json": {"source_file": str(path), "segment_kind": section_kind},
                }
            )

            if section_kind == "analytic_subject":
                # Helper request only for lines that need target-location help.
                if not unique_refs or "ibid." in segment.lower() or len(unique_refs) > 1:
                    helper_entries.append(
                        {
                            "entry_id": f"pl195_entry_{entry_counter:04d}",
                            "lemma_raw": segment,
                            "query_names": [segment[:120]],
                            "page_hints": [str(p) for p in unique_refs[:4]],
                            "page_hint_ints": unique_refs[:4],
                            "context_raw": segment,
                        }
                    )

            for ref_order, ref in enumerate(unique_refs, 1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(ref),
                        "page_ref_raw": str(ref),
                        "page_ref_int": ref,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_best,
                        "target_file_probability": 0.95 if target_best else None,
                        "section_start_file": str(file_path(files[0])),
                        "editorial_anchor_file": str(path),
                        "confidence": confidence,
                        "raw_json": {"source_file": str(path)},
                    }
                )

    return entries, refs, helper_entries


def build_section_metadata() -> list[dict]:
    return [
        {
            "section_key": "PL195:alpha:analytic_subject:001",
            "volume_id": "PL195",
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM, VERBORUM ET SENTENTIARUM MEMORABLIUM AD WOLBERONIS COMMENTARIUM IN CANTICA.",
            "heading_norm": "index rerum verborum et sententiarum memorabilium ad wolberonis commentarium in cantica",
            "heading_letter": None,
            "page_start": 1277,
            "page_end": 1286,
            "file_start": str(file_path(647)),
            "file_end": str(file_path(652)),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Recoverable analytical subject index at the end of the Wolberon commentary block; alphabetic entries span files 647-652.",
                "evidence_files": [str(file_path(n)) for n in SECTION1_FILES],
            },
        },
        {
            "section_key": "PL195:alpha:ordo_rerum:002",
            "volume_id": "PL195",
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1290,
            "page_end": 1296,
            "file_start": str(file_path(653)),
            "file_end": str(file_path(656)),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Editorial contents table following the Wolberon index; OCR headers vary, but the material is clearly a closing ORDO RERUM.",
                "evidence_files": [str(file_path(n)) for n in SECTION2_FILES],
            },
        },
    ]


def build_payload():
    section1_entries, section1_refs, helper_entries = parse_section(SECTION1_FILES, "PL195:alpha:analytic_subject:001", "analytic_subject")
    section2_entries, section2_refs, _ = parse_section(SECTION2_FILES, "PL195:alpha:ordo_rerum:002", "ordo_rerum")
    sections = build_section_metadata()

    payload = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "volume": {
            "volume_id": "PL195",
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 195",
        },
        "sections": sections,
        "nodes": [],
        "entries": section1_entries + section2_entries,
        "refs": section1_refs + section2_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the closing analytical index and the following ORDO RERUM contents table from OCR files 647-656.",
            "evidence_files": [str(file_path(n)) for n in [647, 648, 649, 650, 651, 652, 653, 654, 655, 656]],
        },
        "notes": [
            {
                "note_type": "extraction",
                "source": "pl195_build_payload.py",
                "status": "completed",
            },
            {
                "note_type": "helper",
                "source": "PL195_helper_output.json",
                "status": None,
            },
        ],
    }

    # Build the helper request from the entries that need material resolution.
    helper_request = {
        "volume_id": "PL195",
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }

    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODO_PATH.write_text(
        json.dumps(
            {
                "volume_id": "PL195",
                "updated_at": iso_now(),
                "current_focus": "Resolve ambiguous target locations in the analytical index and assemble the final payload.",
                "completed": [
                    "OCR window inspected",
                    "index and ordo sections identified",
                ],
                "pending": [
                    "run index_target_locator on ambiguous analytical entries",
                    "validate target_file mappings for the extracted refs",
                    "write final payload",
                ],
                "blocked": [],
                "notes": [
                    "Keep OCR literals intact.",
                    "Use neighboring files when a page anchor is weak or duplicated.",
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    HELPER_REQ_PATH.write_text(json.dumps(helper_request, indent=2, ensure_ascii=False) + "\n")
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return payload, helper_request


def main():
    payload, helper_request = build_payload()
    print(json.dumps({
        "payload_written": str(OUT_PATH),
        "helper_request_written": str(HELPER_REQ_PATH),
        "helper_entries": len(helper_request["entries"]),
        "entries": len(payload["entries"]),
        "refs": len(payload["refs"]),
    }, indent=2))


if __name__ == "__main__":
    main()
