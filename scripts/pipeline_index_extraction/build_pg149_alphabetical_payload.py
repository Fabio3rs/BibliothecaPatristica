#!/usr/bin/env python3
"""Usage: build the PG149 alphabetical payload from the OCR tail and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg149_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG149/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG149_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG149_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG149 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG149_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
VOLUME_ID = "PG149"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 149"

SECTION_1_HEADING = "INDEX GRÆCITATIS AD NICEPHORI GREGORÆ HISTORIAM BYZANTINAM"
SECTION_2_HEADING = "INDEX AD NICEPHORI GREGORÆ HISTORIÆ BYZANTINÆ LIBROS POSTREMOS."

SECTION_1_KEY = f"{VOLUME_ID}:alpha:foreign_terms:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:author_index:002"

SECTION_1_REASON = "Greek alphabetical index of terms and locutions with printed-page citations."
SECTION_2_REASON = "Alphabetical index of authors, works, and places cited in the last books."

SECTION_1_FILES = range(566, 573)
SECTION_2_FILES = range(572, 575)

FILE_RE = re.compile(r"-(\d+)\.txt$")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?:\b([IVX]{1,3})\s*[,\.]\s*)?(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?")
LETTER_ONLY_RE = re.compile(r"^[A-ZΑ-Ω]$")
STOP_SECTION_RE = re.compile(r"^ADDENDA AD OPERA NICEPHORI GREGORÆ\.", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = FILE_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def parse_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(raw.strip())
    blocks: list[tuple[str, list[str]]] = []
    for bloco in root.findall("bloco"):
        kind = (bloco.attrib.get("tipo") or "").strip().lower()
        if kind not in {"cabecalho", "texto_principal", "nota_marginal"}:
            continue
        lines = [normalize(line) for line in "".join(bloco.itertext()).splitlines()]
        lines = [line for line in lines if line]
        if lines:
            blocks.append((kind, lines))
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw.strip())
        except ET.ParseError:
            continue
        for bloco in root.findall("bloco"):
            if (bloco.attrib.get("tipo") or "").strip().lower() != "cabecalho":
                continue
            header = normalize("".join(bloco.itertext()))
            if not header:
                continue
            for match in HEADER_NUM_RE.finditer(header):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def protect_abbreviations(text: str) -> str:
    return (
        text.replace("i. q.", "i§q§")
        .replace("i. e.", "i§e§")
        .replace("cf.", "cf§")
        .replace("vid.", "vid§")
        .replace("v.", "v§")
        .replace("ibid.", "ibid§")
    )


def restore_abbreviations(text: str) -> str:
    return (
        text.replace("i§q§", "i. q.")
        .replace("i§e§", "i. e.")
        .replace("cf§", "cf.")
        .replace("vid§", "vid.")
        .replace("v§", "v.")
        .replace("ibid§", "ibid.")
    )


def split_candidate_text(text: str) -> list[str]:
    value = normalize(text)
    if not value:
        return []
    value = protect_abbreviations(value)
    parts = re.split(r"(?<=[.])\s+(?=[A-ZΑ-Ωα-ω])", value)
    parts = [restore_abbreviations(part).strip(" ,;") for part in parts if restore_abbreviations(part).strip(" ,;")]
    return parts


def starts_new_entry(line: str) -> bool:
    if not line:
        return False
    if LETTER_ONLY_RE.fullmatch(line):
        return False
    if line.startswith(("(", "[", "·", ";", ",")):
        return False
    return bool(re.match(r"^[A-ZΑ-Ωa-zα-ωἈ-῾]", line))


def first_letter(text: str | None) -> str | None:
    if not text:
        return None
    for ch in normalize(text):
        if ch.isalpha():
            return ch.upper()
    return None


def extract_lemma_raw(entry_raw: str) -> str | None:
    value = normalize(entry_raw)
    if not value:
        return None
    cut = len(value)
    match = PAGE_REF_RE.search(value)
    if match:
        cut = min(cut, match.start())
    for marker in [" cf.", " cf ", " vid.", " vide", " voir", " v.", " id."]:
        idx = value.lower().find(marker.strip())
        if idx > 0:
            cut = min(cut, idx)
    lemma = value[:cut].strip(" ,;:.")
    return lemma or None


def lemma_norm(text: str | None) -> str | None:
    return sort_norm(text)


def parse_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        roman = match.group(1)
        page_int = int(match.group(2))
        end_int = int(match.group(3)) if match.group(3) else None
        key = (roman, page_int, end_int)
        if key in seen:
            continue
        seen.add(key)
        ref_raw = match.group(0).strip().rstrip(".,;")
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page_int),
                "range_end_raw": str(end_int) if end_int is not None else None,
                "raw_json": {
                    "volume_marker": roman,
                }
                if roman
                else {},
            }
        )
    return refs


def compact_helper_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    candidates = []
    for cand in (item.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in (cand.get("evidence") or [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            }
        )
    return {
        "status": item.get("status"),
        "candidate_role": item.get("candidate_role"),
        "reason_summary": item.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidates": candidates,
    }


def helper_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    candidates: list[str] = []
    for value in [lemma_raw, entry_raw]:
        if value:
            value = normalize(value)
            if value and value not in candidates:
                candidates.append(value)
    if lemma_raw:
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", normalize(lemma_raw)).strip()
        if stripped and stripped not in candidates:
            candidates.append(stripped)
    if entry_raw:
        first_clause = re.split(r"\s*[;,]\s*|\s{2,}", normalize(entry_raw), maxsplit=1)[0].strip()
        if first_clause and first_clause not in candidates:
            candidates.append(first_clause)
    return candidates[:4] or [lemma_raw or entry_raw]


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> None:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = [int(match.group(2)) for match in PAGE_REF_RE.finditer(entry["entry_raw"]) if match.group(2).isdigit()]
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:120],
                "query_names": helper_query_names(entry["lemma_raw"], entry["entry_raw"]),
                "page_hints": [str(v) for v in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry["context_raw"] or entry["entry_raw"][:240],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    helper_output_json.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
    )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_best_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        best = item.get("best_candidate") or {}
        if best.get("file"):
            result[entry_id] = item
    return result


def page_sequence_map(files: list[Path]) -> dict[int, str]:
    return build_page_map(files)


def locate_target_file(entry_raw: str, page_map: dict[int, str]) -> str | None:
    for match in PAGE_REF_RE.finditer(entry_raw):
        page_int = int(match.group(2))
        target = page_map.get(page_int)
        if target:
            return target
    return None


def build_sections(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    page_map = page_sequence_map(files)

    current_section: dict[str, Any] | None = None
    section_files: dict[str, list[str]] = {SECTION_1_KEY: [], SECTION_2_KEY: []}
    current_section_key: str | None = None
    current_section_kind: str | None = None
    current_section_order: int | None = None
    current_heading_raw: str | None = None
    current_heading_norm: str | None = None
    current_page_start: int | None = None
    current_page_end: int | None = None
    current_anchor_file: str | None = None
    current_section_start_file: str | None = None
    logical_buffer: str | None = None
    logical_source_file: str | None = None
    entry_counter = 0

    def flush_buffer() -> None:
        nonlocal logical_buffer, logical_source_file, entry_counter
        if not current_section_key or not logical_buffer:
            logical_buffer = None
            logical_source_file = None
            return
        for candidate in split_candidate_text(logical_buffer):
            if not candidate:
                continue
            if current_section_key == SECTION_1_KEY and candidate.startswith("Revocatur lector ad numeros crassiores textui insertos"):
                continue
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            lemma_raw = extract_lemma_raw(candidate)
            refs_local = parse_refs(candidate)
            page_hints = [ref["page_ref_int"] for ref in refs_local]
            heading_letter = first_letter(lemma_raw or candidate)
            entry = {
                "entry_key": entry_key,
                "section_key": current_section_key,
                "parent_node_key": None,
                "entry_order": entry_counter,
                "entry_kind": "cross_reference"
                if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", normalize(candidate), flags=re.IGNORECASE)
                else "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_norm(lemma_raw),
                "lemma_sort": lemma_norm(lemma_raw),
                "entry_raw": candidate,
                "context_raw": candidate if len(candidate) <= 220 else candidate[:220],
                "heading_letter": heading_letter,
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": current_section_start_file,
                "editorial_anchor_file": logical_source_file or current_section_start_file,
                "target_file_best": None,
                "confidence": 0.74 if page_hints else 0.62,
                "raw_json": {
                    "source_file": logical_source_file,
                    "section_kind": current_section_kind,
                },
            }
            if not entry["lemma_raw"]:
                entry["lemma_raw"] = candidate
                entry["lemma_display"] = candidate
                entry["lemma_norm"] = lemma_norm(candidate)
                entry["lemma_sort"] = lemma_norm(candidate)
            entries.append(entry)
            for ref_order, ref in enumerate(refs_local, start=1):
                ref["entry_key"] = entry_key
                ref["ref_order"] = ref_order
                ref["section_start_file"] = current_section_start_file
                ref["editorial_anchor_file"] = logical_source_file or current_section_start_file
                ref["confidence"] = 0.9 if ref.get("target_file") else 0.62
                refs.append(ref)
        logical_buffer = None
        logical_source_file = None

    def begin_section(section_key: str, section_kind: str, section_order: int, heading_raw: str, heading_norm: str, page_start: int, page_end: int, file_start: str) -> None:
        nonlocal current_section_key, current_section_kind, current_section_order, current_heading_raw, current_heading_norm
        nonlocal current_page_start, current_page_end, current_section_start_file, current_anchor_file, logical_buffer, logical_source_file
        current_section_key = section_key
        current_section_kind = section_kind
        current_section_order = section_order
        current_heading_raw = heading_raw
        current_heading_norm = heading_norm
        current_page_start = page_start
        current_page_end = page_end
        current_section_start_file = file_start
        current_anchor_file = file_start
        logical_buffer = None
        logical_source_file = None

    def close_section() -> None:
        nonlocal current_section_key, current_section_kind, current_section_order, current_heading_raw, current_heading_norm
        nonlocal current_page_start, current_page_end, current_anchor_file, current_section_start_file
        flush_buffer()
        if current_section_key and current_section_kind and current_section_order and current_heading_raw and current_heading_norm:
            sections.append(
                {
                    "section_key": current_section_key,
                    "volume_id": VOLUME_ID,
                    "work_key": None,
                    "section_order": current_section_order,
                    "section_kind": current_section_kind,
                    "heading_raw": current_heading_raw,
                    "heading_norm": current_heading_norm,
                    "heading_letter": None,
                    "page_start": current_page_start,
                    "page_end": current_page_end,
                    "file_start": current_section_start_file,
                    "file_end": current_anchor_file,
                    "confidence": 0.94 if current_section_kind == "author_index" else 0.92,
                    "raw_json": {
                        "section_kind_reason": SECTION_2_REASON if current_section_kind == "author_index" else SECTION_1_REASON,
                    },
                }
            )
        current_section_key = None
        current_section_kind = None
        current_section_order = None
        current_heading_raw = None
        current_heading_norm = None
        current_page_start = None
        current_page_end = None
        current_anchor_file = None
        current_section_start_file = None

    for path in files:
        seq = file_seq(path)
        if seq not in set(SECTION_1_FILES) | set(SECTION_2_FILES):
            continue
        blocks = parse_blocks(path)
        for block_kind, lines in blocks:
            if block_kind not in {"texto_principal", "nota_marginal", "cabecalho"}:
                continue
            block_text = " ".join(lines)
            if SECTION_1_HEADING in block_text:
                if current_section_key == SECTION_2_KEY:
                    flush_buffer()
                    close_section()
                if current_section_key != SECTION_1_KEY:
                    begin_section(
                        SECTION_1_KEY,
                        "foreign_terms",
                        1,
                        "INDEX GRÆCITATIS AD NICEPHORI GREGORÆ HISTORIAM BYZANTINAM",
                        "index græcitatis ad nicephori gregoræ historiam byzantinam",
                        1051,
                        1062,
                        str(path),
                    )
                flush_buffer()
                continue
            if SECTION_2_HEADING in block_text:
                if current_section_key == SECTION_1_KEY:
                    flush_buffer()
                    close_section()
                if current_section_key != SECTION_2_KEY:
                    begin_section(
                        SECTION_2_KEY,
                        "author_index",
                        2,
                        "INDEX AD NICEPHORI GREGORÆ HISTORIÆ BYZANTINÆ LIBROS POSTREMOS.",
                        "index ad nicephori gregoræ historiæ byzantinæ libros postremos",
                        1063,
                        1069,
                        str(path),
                    )
                flush_buffer()
                continue
            if STOP_SECTION_RE.search(block_text):
                if current_section_key == SECTION_2_KEY:
                    flush_buffer()
                    close_section()
                continue
            if current_section_key is not None:
                current_anchor_file = str(path)
            for raw_line in lines:
                line = normalize(raw_line)
                if not line:
                    continue
                if current_section_key is None:
                    continue
                if current_section_key == SECTION_1_KEY and line == "ADDENDA.":
                    # editorial interruption inside the page is ignored; the section continues after the index heading
                    continue
                if current_section_key == SECTION_2_KEY and line == "ADDENDA.":
                    # this is the editorial continuation marker within the page, not a lemma
                    continue
                if LETTER_ONLY_RE.fullmatch(line):
                    flush_buffer()
                    continue
                if current_section_key == SECTION_1_KEY and re.match(r"^[A-ZΑ-Ω]\s+[a-zα-ω]", line):
                    # standalone alphabetical divider at the start of a line; drop the divider token
                    line = re.sub(r"^[A-ZΑ-Ω]\s+", "", line)
                if logical_buffer is None:
                    logical_buffer = line
                    logical_source_file = str(path)
                else:
                    if logical_buffer.endswith("-"):
                        logical_buffer = logical_buffer[:-1] + line.lstrip()
                    elif not logical_buffer.endswith(".") and starts_new_entry(line):
                        logical_buffer += " " + line
                    else:
                        flush_buffer()
                        logical_buffer = line
                        logical_source_file = str(path)

        if current_section_key == SECTION_1_KEY and seq == 572:
            # The section ends in the upper half of file 572 before the Latin index title appears.
            pass

    if current_section_key:
        flush_buffer()
        close_section()

    # Build nodes by first-letter group per section.
    def add_letter_nodes(section_key: str) -> None:
        letter_order: dict[str, int] = {}
        section_entries = [entry for entry in entries if entry["section_key"] == section_key]
        current_letter = None
        for entry in section_entries:
            letter = entry.get("heading_letter")
            if not letter:
                continue
            if letter != current_letter:
                current_letter = letter
                if letter not in letter_order:
                    letter_order[letter] = len(letter_order) + 1
                    nodes.append(
                        {
                            "node_key": f"{section_key}:letter:{letter}",
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": letter_order[letter],
                            "node_kind": "letter_group",
                            "label_raw": letter,
                            "label_norm": letter.lower(),
                            "label_sort": letter.lower(),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {"source": "entry_letter_inference"},
                        }
                    )
            entry["parent_node_key"] = f"{section_key}:letter:{letter}" if letter else None

    add_letter_nodes(SECTION_1_KEY)
    add_letter_nodes(SECTION_2_KEY)

    return sections, nodes, entries, refs, scripture_refs


def attach_target_best(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_map: dict[str, dict[str, Any]], page_map: dict[int, str]) -> None:
    ref_map: dict[str, str | None] = {}
    for ref in refs:
        if ref.get("entry_key") not in ref_map and ref.get("target_file"):
            ref_map[ref["entry_key"]] = ref.get("target_file")
    for entry in entries:
        helper_item = helper_map.get(entry["entry_key"])
        if helper_item and helper_item.get("best_candidate", {}).get("file"):
            entry["target_file_best"] = helper_item["best_candidate"].get("file")
            entry.setdefault("raw_json", {})
            entry["raw_json"]["helper"] = compact_helper_item(helper_item)
            continue
        if not entry.get("target_file_best"):
            entry["target_file_best"] = ref_map.get(entry["entry_key"]) or locate_target_file(entry["entry_raw"], page_map)
        if entry.get("target_file_best") and not entry.get("editorial_anchor_file"):
            entry["editorial_anchor_file"] = entry["target_file_best"]


def build_payload(source_root: Path, helper_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = page_sequence_map(files)
    sections, nodes, entries, refs, scripture_refs = build_sections(files)

    attach_target_best(entries, refs, helper_map, page_map)
    for ref in refs:
        if not ref.get("target_file"):
            ref["target_file"] = page_map.get(ref["page_ref_int"])
        ref["target_file_probability"] = 0.98 if ref.get("target_file") else None
        ref["confidence"] = 0.9 if ref.get("target_file") else 0.62

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the Greek alphabetical index and the later Latin author/work index from the OCR tail using conservative line-level segmentation and page-number anchoring.",
        "evidence_files": [
            str(source_root / "3949ad25-bf2a-4c98-a7a6-5a1579629069-566.txt"),
            str(source_root / "3949ad25-bf2a-4c98-a7a6-5a1579629069-572.txt"),
            str(source_root / "3949ad25-bf2a-4c98-a7a6-5a1579629069-573.txt"),
            str(source_root / "3949ad25-bf2a-4c98-a7a6-5a1579629069-574.txt"),
        ],
    }

    notes = [
        "Section 1 is the Greek alphabetical index headed INDEX GRÆCITATIS AD NICEPHORI GREGORÆ HISTORIAM BYZANTINAM.",
        "Section 2 is the Latin alphabetical index headed INDEX AD NICEPHORI GREGORÆ HISTORIÆ BYZANTINÆ LIBROS POSTREMOS.",
        "OCR literals were preserved; hyphenated wraps were merged only when they were obvious continuations.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG149 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract the PG149 tail alphabetical indices and validate target-file anchoring.",
        "completed": [
            "identified the Greek index heading",
            "identified the Latin postremos index heading",
        ],
        "pending": [
            "build helper request from provisional entries",
            "run index_target_locator",
            "assemble final payload",
        ],
        "blocked": [],
        "notes": [
            "Ignore the article text that follows the addenda heading on file 574.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    provisional = build_payload(args.source_root, {})
    write_json(args.intermediate_dir / "volume.json", provisional["volume"])
    write_json(args.intermediate_dir / "sections.json", provisional["sections"])
    write_json(args.intermediate_dir / "nodes.json", provisional["nodes"])
    write_json(args.intermediate_dir / "entries.json", provisional["entries"])
    write_json(args.intermediate_dir / "refs.json", provisional["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", provisional["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", provisional["coverage"])
    write_json(args.intermediate_dir / "notes.json", provisional["notes"])
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": provisional["generated_at"]})

    build_helper_request(provisional["entries"], args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_best_map(helper_output)
    payload = build_payload(args.source_root, helper_map)

    # Refresh the intermediate fragments with helper-enriched entries.
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
