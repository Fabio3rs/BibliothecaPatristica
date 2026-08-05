#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/build_pl023_chunk_002_part_001.py"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path("/homessddata/Projects/pdfocr")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = ROOT / "data/intermediate_payloads/PL023/workplan.json"
FRAGMENT_PATH = ROOT / "data/intermediate_payloads/PL023/chunks/section_002_part_001.json"
FINAL_PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL023_alphabetical_indices.json"

CHUNK_ID = "PL023:chunk:002:001"
SECTION_ID = "PL023:candidate-section:002"
INPUT_FINGERPRINT = "92f19b0f0db7917aa19ffd41dfbd188780039da5c012112acfd7e1a01899ac95"

FILE_821 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-821.txt")
FILE_822 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-822.txt")
FILE_823 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-823.txt")
FILE_824 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-824.txt")
FILE_825 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-825.txt")
FILE_826 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-826.txt")
FILE_820 = str(ROOT / "teste/PL023/text/073a315c-077a-492e-8912-bbaf631c5389-820.txt")

OWNED_FILES = [FILE_826, FILE_825, FILE_824, FILE_823, FILE_822, FILE_821]
TARGET_HEADER_PAGE = {
    FILE_821: 1579,
    FILE_822: 1581,
}


def _run_read_ocr_page_text(path: str) -> str:
    result = subprocess.run(
        [
            "python",
            "scripts/read_ocr_page_text.py",
            "--view",
            "xml",
            "--show-source",
            path,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _extract_text_lines(path: str) -> list[tuple[str | None, str]]:
    xml = _run_read_ocr_page_text(path)
    current_heading: str | None = None
    output: list[tuple[str | None, str]] = []
    block_re = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
    for block_type, raw in block_re.findall(xml):
        text = re.sub(r"\s+\n", "\n", raw)
        text = re.sub(r"\n\s+", "\n", text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if block_type == "nota_marginal":
            if len(lines) == 1 and re.fullmatch(r"[A-Z]", lines[0]):
                current_heading = lines[0]
            continue
        if block_type != "texto_principal":
            continue
        for line in lines:
            if re.fullmatch(r"[A-Z]", line):
                current_heading = line
                continue
            output.append((current_heading, line))
    return output


def _build_entries_from_lines(path: str) -> list[dict]:
    lines = _extract_text_lines(path)
    entries: list[dict] = []
    heading_for_buffer: str | None = None
    buffer = ""

    def flush() -> None:
        nonlocal buffer, heading_for_buffer
        text = buffer.strip()
        if not text:
            buffer = ""
            return
        entries.append(
            {
                "heading_letter": heading_for_buffer,
                "entry_raw": text,
            }
        )
        buffer = ""

    for heading_letter, line in lines:
        starts_new = False
        if not buffer:
            starts_new = True
        elif re.search(r"(?:\b(?:ibid|Ibid)\.|[0-9])\.?$", buffer):
            starts_new = True
        elif re.match(r"^[0-9]+\.", line):
            starts_new = True

        if starts_new:
            flush()
            buffer = line
            heading_for_buffer = heading_letter
        else:
            buffer = f"{buffer} {line}"
    flush()
    return [_finalize_manual_entry(path, item["entry_raw"], item["heading_letter"]) for item in entries]


def _strip_trailing_refs(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(
        r"(?:,?\s*(?:ibid\.|Ibid\.|[0-9]+(?:,\s*[0-9]+)*))+\.*$",
        "",
        stripped,
    )
    return stripped.strip(" ,.;:")


def _guess_lemma(text: str) -> str | None:
    cleaned = re.sub(r"^[0-9]+\.\s*", "", text.strip())
    if not cleaned:
        return None
    if m := re.match(r"^([A-ZÆŒ][^.]{0,40})\.\s+(.*)$", cleaned):
        head, tail = m.groups()
        if len(head.split()) <= 4:
            remainder = _strip_trailing_refs(tail)
            return remainder or head
    if "," in cleaned:
        return cleaned.split(",", 1)[0].strip()
    return _strip_trailing_refs(cleaned)


def _normalize(text: str | None) -> str | None:
    if text is None:
        return None
    lowered = text.lower()
    lowered = re.sub(r"[^\w\sæœ]+", " ", lowered, flags=re.UNICODE)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered or None


def _finalize_manual_entry(path: str, entry_raw: str, heading_letter: str | None) -> dict:
    lemma_raw = _guess_lemma(entry_raw)
    return {
        "entry_key": "",
        "section_key": SECTION_ID,
        "parent_node_key": None,
        "entry_order": 0,
        "entry_kind": "lemma",
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": _normalize(lemma_raw),
        "lemma_sort": _normalize(lemma_raw),
        "entry_raw": entry_raw,
        "context_raw": entry_raw,
        "heading_letter": heading_letter,
        "inferred_printed_page": TARGET_HEADER_PAGE[path],
        "section_start_file": path,
        "editorial_anchor_file": path,
        "target_file_best": path,
        "confidence": 0.72,
        "raw_json": {
            "source_file": path,
            "source_files": [path],
            "source_line": entry_raw,
            "section_kind": "alphabetical_general",
            "line_notes": ["recovered_from_ocr_line_parser"],
        },
    }


def _load_chunk(workplan: dict) -> dict:
    for chunk in workplan.get("chunks", []):
        if chunk.get("chunk_id") == CHUNK_ID:
            return chunk
    raise KeyError(CHUNK_ID)


def _clone_entries(entries: Iterable[dict]) -> list[dict]:
    return [copy.deepcopy(entry) for entry in entries]


def _entries_from_final_payload(path: str) -> list[dict]:
    payload = _read_json(FINAL_PAYLOAD_PATH)
    items = []
    for entry in payload.get("entries", []):
        source_file = (entry.get("raw_json") or {}).get("source_file")
        if source_file != path:
            continue
        new_entry = copy.deepcopy(entry)
        new_entry["section_key"] = SECTION_ID
        new_entry["parent_node_key"] = None
        new_entry["section_start_file"] = path
        new_entry["editorial_anchor_file"] = path
        new_entry["target_file_best"] = path
        raw_json = dict(new_entry.get("raw_json") or {})
        raw_json["source_files"] = [path]
        raw_json.setdefault("line_notes", [])
        raw_json["line_notes"].append("recovered_from_final_payload_checkpoint")
        new_entry["raw_json"] = raw_json
        items.append(new_entry)
    return items


def _refresh_entry_identity(entries: list[dict]) -> None:
    for idx, entry in enumerate(entries, start=1):
        entry["entry_order"] = idx
        entry["entry_key"] = f"{SECTION_ID}:{CHUNK_ID}:{idx:03d}"
        raw_json = dict(entry.get("raw_json") or {})
        raw_json.setdefault("source_files", [raw_json.get("source_file")])
        raw_json["source_files"] = [value for value in raw_json["source_files"] if value]
        entry["raw_json"] = raw_json


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = _load_chunk(workplan)
    existing = _read_json(FRAGMENT_PATH)

    preserved_entries = _clone_entries(existing.get("entries", []))
    transformed_entries = []
    for path in (FILE_824, FILE_823):
        transformed_entries.extend(_entries_from_final_payload(path))
    manual_entries = _build_entries_from_lines(FILE_822) + _build_entries_from_lines(FILE_821)

    all_entries = preserved_entries + transformed_entries + manual_entries
    _refresh_entry_identity(all_entries)

    section = copy.deepcopy((existing.get("sections") or [])[0])
    section["extraction_last_file"] = FILE_821
    section["physical_file_low"] = FILE_821
    section["physical_file_high"] = FILE_826
    section["physical_files"] = OWNED_FILES
    section["header_evidence"] = [
        item
        for item in workplan["sections"][1]["header_evidence"]
        if item["physical_file"] in OWNED_FILES
    ]
    section["raw_json"]["evidence_files"] = [
        FILE_826,
        FILE_825,
        FILE_824,
        FILE_823,
        FILE_822,
        FILE_821,
        FILE_820,
    ]
    section["raw_json"]["notes"] = [
        "Existing 825-826 entries were preserved after adding raw_json.source_files for validator compliance.",
        "Entries for 824-823 were recovered from the canonical PL023 alphabetical payload and rechecked against local OCR ownership.",
        "Entries for 822-821 were rebuilt from read_ocr_page_text XML line inspection; file 820 was checked as left-side context for the non-owned boundary.",
        "The 825/826 high-likelihood boundary remains not_same_entry: 825 ends with a separate Salomon clause, and 826 begins a new numbered Salomon item.",
    ]

    payload = copy.deepcopy(existing)
    payload["volume_id"] = "PL023"
    payload["section_id"] = SECTION_ID
    payload["chunk_id"] = CHUNK_ID
    payload["input_fingerprint"] = INPUT_FINGERPRINT
    payload["status"] = "complete"
    payload["physical_files"] = OWNED_FILES
    payload["sections"] = [section]
    payload["nodes"] = []
    payload["entries"] = all_entries
    payload["refs"] = []
    payload["scripture_refs"] = []
    payload["notes"] = [
        {
            "type": "recovery",
            "message": "Corrected physical_files to match the chunk contract and expanded the fragment to cover owned OCR files 821-826.",
        }
    ]

    FRAGMENT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(FRAGMENT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()
