#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL175_build_alphabetical_payload.py

Builds the PL175 alphabetical-index payload from the OCR tail, writes the
helper request/output, persists intermediate checkpoints, and emits the final
canonical JSON payload.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL175"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 175"
SOURCE_ROOT = ROOT / "teste/PL175/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL175_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL175_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL175_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL175"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

HEADING_RE = re.compile(r"INDEX RERUM ANALYTICUS\.", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_RE = re.compile(r"\b\d{1,4}\b")
NOISE_LINES = {
    "Digitized by Google",
    "BUILDING USE ONLY",
    "DO NOT CIRCULATE",
}
MARGINAL_LETTER_RE = re.compile(r"^\|[A-ZÆŒ]$")
TRAILING_PUNCT_RE = re.compile(r"[.?!:;]\s*$")
REF_SPLIT_RE = re.compile(r"(?<=\d)\.\s+(?=[A-ZÆŒ])")
REF_TAIL_RE = re.compile(r"\b\d{1,4}(?:\s+et\s+seq\.?|\s+et\s+seqq\.?|\s+et\s+seq|[\.,])?", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def load_page_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r'<bloco[^>]*>(.*?)</bloco>', raw, re.IGNORECASE | re.DOTALL):
        block = match.group(1)
        for raw_line in block.splitlines():
            line = normalize_space(raw_line)
            if not line or line in NOISE_LINES:
                continue
            lines.append(line)
    return lines


def discover_index_window(files: list[Path]) -> tuple[list[Path], Path, Path]:
    first_idx: int | None = None
    last_idx: int | None = None
    for idx, path in enumerate(files):
        text = path.read_text(encoding="utf-8", errors="replace")
        if HEADING_RE.search(text):
            if first_idx is None:
                first_idx = idx
            last_idx = idx
    if first_idx is None or last_idx is None:
        raise FileNotFoundError("Could not locate INDEX RERUM ANALYTICUS in PL175 OCR files")
    return files[first_idx : last_idx + 1], files[first_idx], files[last_idx]


def merge_wrapped_lines(lines: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    merged: list[tuple[Path, str]] = []
    buffer_file: Path | None = None
    buffer = ""
    started = False
    for source_file, line in lines:
        line = normalize_space(line)
        if not line:
            continue
        if line in NOISE_LINES or line == "PATROL. CLXXV.":
            continue
        if MARGINAL_LETTER_RE.fullmatch(line):
            continue
        if not started:
            if HEADING_RE.search(line):
                started = True
            continue
        if LETTER_RE.fullmatch(line):
            if buffer:
                merged.append((buffer_file or source_file, buffer.strip()))
                buffer = ""
            merged.append((source_file, line))
            continue
        if HEADING_RE.search(line):
            if buffer:
                merged.append((buffer_file or source_file, buffer.strip()))
                buffer = ""
            continue
        if not buffer:
            buffer_file = source_file
            buffer = line
            continue
        if buffer.endswith("-"):
            buffer = buffer[:-1] + line.lstrip()
            continue
        if not TRAILING_PUNCT_RE.search(buffer) and line and line[0].islower():
            buffer = f"{buffer} {line}"
            continue
        if not TRAILING_PUNCT_RE.search(buffer) and line.startswith("("):
            buffer = f"{buffer} {line}"
            continue
        merged.append((buffer_file or source_file, buffer.strip()))
        buffer_file = source_file
        buffer = line
    if buffer:
        merged.append((buffer_file or (lines[-1][0] if lines else Path("")), buffer.strip()))
    return merged


def split_clear_segments(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    segments: list[str] = []
    start = 0
    for match in REF_SPLIT_RE.finditer(text):
        prev = text[start : match.start() + 1]
        next_part = text[match.end() : match.end() + 80]
        if not re.search(r"\d", prev):
            continue
        if not re.search(r",\s*\d", next_part):
            continue
        segments.append(prev.strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def page_hints(text: str) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for match in PAGE_RE.finditer(text):
        value = int(match.group(0))
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def lemma_from_segment(segment: str) -> str:
    text = normalize_space(segment)
    if not text:
        return text
    first_ref = PAGE_RE.search(text)
    if first_ref:
        lemma = text[: first_ref.start()].strip()
    elif "." in text:
        lemma = text.split(".", 1)[0].strip()
    else:
        lemma = text
    lemma = lemma.strip(" ,;:.")
    return lemma or text


def is_cross_reference(segment: str) -> bool:
    return bool(re.match(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", segment, re.IGNORECASE))


def clean_query_name(text: str) -> str:
    value = normalize_space(text)
    value = re.sub(r"\([^)]*\)", "", value)
    value = value.strip(" ,;:.")
    return value


def build_entries(source_files: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lines: list[tuple[Path, str]] = []
    for path in source_files:
        lines.extend((path, line) for line in load_page_lines(path))
    lines = merge_wrapped_lines(lines)

    nodes: list[dict[str, Any]] = []
    node_map: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    current_letter: str | None = None
    entry_order = 0
    node_order = 0

    def ensure_letter_node(letter: str) -> str:
        nonlocal node_order
        if letter not in node_map:
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            node_map[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"role": "alphabetic divider"},
                }
            )
        return node_map[letter]

    for source_file, raw_line in lines:
        line = normalize_space(raw_line)
        if not line:
            continue
        if MARGINAL_LETTER_RE.fullmatch(line):
            continue
        if HEADING_RE.search(line):
            continue
        if LETTER_RE.fullmatch(line):
            current_letter = line
            ensure_letter_node(line)
            continue
        for segment in split_clear_segments(line):
            segment = normalize_space(segment)
            if not segment or segment == "PATROL. CLXXV.":
                continue
            if MARGINAL_LETTER_RE.fullmatch(segment):
                continue
            if LETTER_RE.fullmatch(segment):
                current_letter = segment
                ensure_letter_node(segment)
                continue
            if current_letter is None:
                continue
            entry_order += 1
            lemma_raw = lemma_from_segment(segment)
            hints = page_hints(segment)
            lemma_display = lemma_raw
            entry_kind = "cross_reference" if is_cross_reference(segment) else "lemma"
            query_names = [clean_query_name(lemma_raw), clean_query_name(segment)]
            if lemma_raw != segment:
                query_names.append(clean_query_name(segment.split(",", 1)[0]))
            query_names = [q for q in OrderedDict.fromkeys(q for q in query_names if q)]
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            entry = {
                "entry_key": entry_key,
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                "parent_node_key": node_map.get(current_letter) if current_letter else None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_display,
                "lemma_norm": normalize_sort(lemma_raw),
                "lemma_sort": normalize_sort(lemma_raw),
                "entry_raw": segment,
                "context_raw": segment,
                "heading_letter": current_letter,
                "inferred_printed_page": hints[0] if hints else None,
                "section_start_file": str(source_files[0]),
                "editorial_anchor_file": None,
                "target_file_best": None,
                "confidence": 0.6 if hints else 0.45,
                "raw_json": {
                    "source_file": str(source_file),
                    "ocr_segment_raw": segment,
                    "page_hints": hints,
                    "query_names": query_names,
                    "entry_kind_reason": "Cross-reference without a material locator" if entry_kind == "cross_reference" else "Alphabetical index line recovered from OCR.",
                },
            }
            entries.append(entry)
            helper_entries.append(
                {
                    "entry_id": entry_key.lower().replace(":", "_"),
                    "lemma_raw": lemma_raw,
                    "query_names": query_names,
                    "page_hints": [str(v) for v in hints],
                    "page_hint_ints": hints,
                    "context_raw": segment,
                }
            )

    section = {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ANALYTICUS.",
        "heading_norm": "index rerum analyticus",
        "heading_letter": None,
        "page_start": 1153,
        "page_end": 1164,
        "file_start": str(source_files[0]),
        "file_end": str(source_files[-1]),
        "confidence": 0.97,
        "raw_json": {
            "section_kind_reason": "Alphabetical analytical subject index with letter dividers A-V and dense lemma-to-page citations.",
            "evidence_files": [str(source_files[0]), str(source_files[-1])],
            "ocr_header_notes": [
                "The last OCR spread reads 4163/4164 in error; the printed sequence continues 1163/1164.",
            ],
        },
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_LABEL,
    }

    return volume, [section], nodes, entries, helper_entries


def run_helper_request(helper_request: Path, helper_output: Path) -> None:
    cmd = [
        sys.executable,
        str(SCRIPT_TARGET_LOCATOR),
        "--input",
        str(helper_request),
        "--output",
        str(helper_output),
        "--pretty",
    ]
    subprocess.run(cmd, check=True)


def helper_entry_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            out[entry_id] = item
    return out


def build_refs(entries: list[dict[str, Any]], helper_map: dict[str, dict[str, Any]], section_start_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in entries:
        entry_id = entry["entry_key"].lower().replace(":", "_")
        helper_item = helper_map.get(entry_id, {})
        best = helper_item.get("best_candidate") or {}
        target_file = best.get("file")
        target_prob = best.get("probability")
        entry["target_file_best"] = target_file
        entry["editorial_anchor_file"] = entry["raw_json"].get("source_file")
        if target_file:
            entry["confidence"] = max(entry["confidence"], float(target_prob or 0.0))
        raw_hints = entry["raw_json"].get("page_hints", [])
        ref_orders = 0
        for hint in raw_hints:
            ref_orders += 1
            ref_raw = str(hint)
            if ref_orders == len(raw_hints) and re.search(r"et\s+seqq?\.?|et\s+seq", entry["entry_raw"], re.IGNORECASE):
                ref_raw = f"{ref_raw} et seq."
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_orders,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": str(hint),
                    "page_ref_int": int(hint),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": entry["confidence"],
                    "raw_json": {
                        "helper_status": helper_item.get("status"),
                        "candidate_role": best.get("candidate_role"),
                        "reason_summary": best.get("reason_summary"),
                        "best_candidate": {
                            "file": target_file,
                            "probability": target_prob,
                            "candidate_role": best.get("candidate_role"),
                            "evidence_kinds": [ev.get("kind") for ev in best.get("evidence", [])[:8] if isinstance(ev, dict)],
                        } if best else {},
                    },
                }
            )
    return refs


def build_notes() -> list[str]:
    return [
        "PL175 is a dense INDEX RERUM ANALYTICUS with alphabetical dividers from A through V.",
        "OCR header corruption on the last spread was treated conservatively as printed pages 1163/1164.",
        "Entries are line-grouped conservatively where OCR wrapping or punctuation makes exact lemma splitting unsafe.",
    ]


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)

    all_files = sorted(SOURCE_ROOT.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    index_window, section_start_file, section_end_file = discover_index_window(all_files)
    volume, sections, nodes, entries, helper_entries = build_entries(index_window)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve PL175 helper targets and assemble alphabetical index payload",
        "completed": [
            "index window detected",
            "logical entries segmented from OCR",
            "helper request prepared",
        ],
        "pending": [
            "run helper target locator",
            "populate refs and target files",
            "write final payload",
        ],
        "blocked": [],
        "notes": [
            f"Index window files: {section_start_file.name} to {section_end_file.name}.",
        ],
    }
    write_json(TODO_JSON, todo)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(HELPER_REQUEST_JSON, helper_request)

    run_helper_request(HELPER_REQUEST_JSON, HELPER_OUTPUT_JSON)
    helper_output = read_json(HELPER_OUTPUT_JSON, {})
    helper_map = helper_entry_map(helper_output)

    refs = build_refs(entries, helper_map, str(section_start_file))
    scripture_refs: list[dict[str, Any]] = []

    notes = build_notes()
    coverage = {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": "Recovered the analytical index lines from OCR and resolved target files with the helper, but some long OCR rows were conservatively grouped where the line break structure does not support exact lemma splitting.",
        "evidence_files": [str(section_start_file), str(section_end_file)],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    write_json(OUTPUT_FILE, payload)

    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", scripture_refs)
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(INTERMEDIATE_DIR / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})

    todo["updated_at"] = payload["generated_at"]
    todo["current_focus"] = "PL175 alphabetical payload assembled"
    todo["completed"].append("helper resolved and final payload written")
    todo["pending"] = []
    write_json(TODO_JSON, todo)


if __name__ == "__main__":
    main()
