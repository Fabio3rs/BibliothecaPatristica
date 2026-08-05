#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/build_pl060_chunk_004_part_001.py"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/chunks/section_004_part_001.json"
CHUNK_ID = "PL060:chunk:004:001"
SECTION_ID = "PL060:candidate-section:004"
INPUT_FINGERPRINT = "0b1d4de98150ed0dc85f4c7477eb57d2d7a8eacf514948fe436aa3c5ba4ddcb9"
NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}


def _normalize_entries(entries: list[dict], owned_file: str) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries:
        item = copy.deepcopy(entry)
        raw_json = item.setdefault("raw_json", {})
        raw_json["source_files"] = [owned_file]
        normalized.append(item)
    return normalized


def _normalize_refs(refs: list[dict], owned_file: str) -> list[dict]:
    normalized: list[dict] = []
    for ref in refs:
        item = copy.deepcopy(ref)
        raw_json = item.setdefault("raw_json", {})
        raw_json.setdefault("source_file", owned_file)
        raw_json["source_files"] = [owned_file]
        normalized.append(item)
    return normalized


def _normalize_sections(sections: list[dict], context_files: list[str], owned_file: str) -> list[dict]:
    normalized: list[dict] = []
    for section in sections:
        item = copy.deepcopy(section)
        raw_json = item.setdefault("raw_json", {})
        raw_json.setdefault("source_files", [owned_file])
        raw_json["source_files"] = [owned_file]
        raw_json.setdefault("evidence_files_checked", context_files)
        normalized.append(item)
    return normalized


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    checkpoint = _read_json(OUTPUT_PATH)

    owned_files = [str(path) for path in chunk["physical_files"]]
    owned_file = owned_files[0]
    context_files = [str(path) for path in chunk.get("context_files") or []]

    payload = {
        "schema_version": 2,
        "volume_id": "PL060",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": INPUT_FINGERPRINT,
        "status": "complete",
        "physical_files": owned_files,
        "numbering_semantics": NUMBERING,
        "boundary_decisions": [],
        "sections": _normalize_sections(checkpoint.get("sections") or [], context_files, owned_file),
        "nodes": copy.deepcopy(checkpoint.get("nodes") or []),
        "entries": _normalize_entries(checkpoint.get("entries") or [], owned_file),
        "refs": _normalize_refs(checkpoint.get("refs") or [], owned_file),
        "scripture_refs": _normalize_refs(checkpoint.get("scripture_refs") or [], owned_file),
        "notes": list(checkpoint.get("notes") or [])
        + [
            "Checkpoint content was recovered from an invalid volume-level wrapper and rewritten into the chunk-fragment contract for PL060:chunk:004:001."
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()
