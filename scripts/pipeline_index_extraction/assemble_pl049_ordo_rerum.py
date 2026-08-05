#!/usr/bin/env python3
"""Assemble the PL049 closing ORDO RERUM payload from validated chunk JSON.

Usage:
  python scripts/pipeline_index_extraction/assemble_pl049_ordo_rerum.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
CHUNK_PATHS = [
    ROOT / "data/intermediate_payloads/PL049/chunks/section_001_part_001.json",
    ROOT / "data/intermediate_payloads/PL049/chunks/section_001_part_002.json",
]
OUTFILE = ROOT / "data/alphabetical_index_payloads/PL049_alphabetical_indices.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float_confidence(value: Any, target_prob: Any | None = None) -> float:
    if isinstance(value, (int, float)):
        base = float(value)
    elif isinstance(value, str):
        mapping = {"high": 0.9, "medium": 0.55, "low": 0.35}
        base = mapping.get(value.lower(), 0.5)
    else:
        base = 0.5

    if isinstance(target_prob, (int, float)):
        base = max(base, float(target_prob))
    return round(base, 6)


def normalize_key_refs(raw_json: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    updated = dict(raw_json)
    if "grouped_entry" in updated and updated["grouped_entry"] in key_map:
        updated["grouped_entry"] = key_map[updated["grouped_entry"]]
    if "inherited_from_entry_key" in updated and updated["inherited_from_entry_key"] in key_map:
        updated["inherited_from_entry_key"] = key_map[updated["inherited_from_entry_key"]]
    return updated


def main() -> None:
    chunks = [load_json(path) for path in CHUNK_PATHS]
    section = chunks[0]["sections"][0]
    physical_files = chunks[0]["physical_files"] + chunks[1]["physical_files"]
    source_files = list(
        dict.fromkeys(
            chunks[0]["sections"][0]["raw_json"]["source_files"]
            + chunks[1]["sections"][0]["raw_json"]["source_files"]
        )
    )

    merged_entries: list[dict[str, Any]] = []
    merged_refs: list[dict[str, Any]] = []
    key_map: dict[str, str] = {}

    seq = 1
    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk_id = chunk["chunk_id"]
        for entry in chunk["entries"]:
            old_key = entry["entry_key"]
            new_key = f"PL049:entry:{seq:04d}"
            key_map[old_key] = new_key
            seq += 1

            chunk_ref = next((r for r in chunk["refs"] if r["entry_key"] == old_key), None)
            target_file = chunk_ref["target_file"] if chunk_ref else entry.get("target_file")
            target_prob = chunk_ref["target_file_probability"] if chunk_ref else None
            normalized_target = entry["normalized_target"]
            page_ref_raw = entry["page_ref_raw"]
            page_ref_int = entry["page_ref_int"]

            merged_entries.append(
                {
                    "entry_key": new_key,
                    "section_key": "PL049:section:volume_end:ordo_rerum:001",
                    "parent_node_key": None,
                    "entry_order": len(merged_entries) + 1,
                    "entry_kind": "lemma",
                    "lemma_raw": entry["target_raw"],
                    "lemma_display": entry["target_raw"],
                    "lemma_norm": normalized_target,
                    "lemma_sort": normalized_target,
                    "entry_raw": entry["entry_raw"],
                    "context_raw": None,
                    "heading_letter": None,
                    "inferred_printed_page": page_ref_int,
                    "section_start_file": entry["raw_json"]["source_file"],
                    "editorial_anchor_file": entry["raw_json"]["source_file"],
                    "target_file_best": target_file,
                    "confidence": as_float_confidence(entry["confidence"], target_prob),
                    "raw_json": {
                        **entry["raw_json"],
                        "source_chunk_id": chunk_id,
                        "source_chunk_entry_key": old_key,
                        "source_chunk_entry_order": entry["entry_order"],
                    },
                }
            )

            if chunk_ref is not None:
                ref_raw = dict(chunk_ref)
                ref_raw["entry_key"] = new_key
                ref_raw["raw_json"] = normalize_key_refs(
                    {
                        **chunk_ref["raw_json"],
                        "source_chunk_id": chunk_id,
                        "source_chunk_entry_key": old_key,
                        "source_chunk_ref_order": chunk_ref["ref_order"],
                    },
                    key_map,
                )
                if page_ref_raw and str(page_ref_raw).lower().startswith("ibid"):
                    range_start_raw = str(page_ref_int)
                else:
                    range_start_raw = str(page_ref_int) if page_ref_int is not None else None
                ref_raw.update(
                    {
                        "ref_raw": entry["entry_raw"],
                        "page_ref_raw": page_ref_raw,
                        "page_ref_int": page_ref_int,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": range_start_raw,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": target_prob,
                        "section_start_file": entry["raw_json"]["source_file"],
                        "editorial_anchor_file": entry["raw_json"]["source_file"],
                        "confidence": as_float_confidence(chunk_ref["confidence"], target_prob),
                    }
                )
                merged_refs.append(ref_raw)

    final_section = {
        "section_key": "PL049:alpha:ordo_rerum:001",
        "volume_id": "PL049",
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": physical_files[0],
        "file_end": physical_files[-1],
        "confidence": 0.98,
        "raw_json": {
            "source_files": source_files,
            "adjacent_context_files": section["raw_json"]["adjacent_context_files"],
            "section_kind": "volume_end_inventory",
            "section_kind_reason": "End-of-volume contents table (ORDO RERUM) with chapter and work locators rather than lemma entries.",
            "entries_status": "complete",
            "evidence_files": source_files,
            "notes": [
                "Merged the two validated chunk fragments that cover the closing ORDO RERUM table.",
                "OCR file suffix order does not match the cited-page order inside the table; the payload preserves the OCR traversal."
            ],
        },
    }

    volume = {
        "volume_id": "PL049",
        "collection": "PL",
        "source_root": "/homessddata/Projects/pdfocr/teste/PL049/text",
        "volume_label": "PL049",
        "notes": [
            "Recovered the closing ORDO RERUM contents table from the tail of PL049.",
            "Conservative Ibid. inheritance and direct OCR inspection were used to preserve the table's printed locator sequence."
        ],
    }

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": f"Recovered the closing ORDO RERUM table as {len(merged_entries)} serialized entries and {len(merged_refs)} material refs from the validated chunk fragments.",
        "evidence_files": final_section["raw_json"]["evidence_files"],
    }

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds"),
        "volume": volume,
        "sections": [final_section],
        "nodes": [],
        "entries": merged_entries,
        "refs": merged_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            "The final payload is the closing index table for PL049, not the earlier contents-block checkpoint.",
            "Structural lines without a recoverable locator were retained only where the OCR itself presented them as table lines."
        ],
    }

    OUTFILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTFILE)
    print(f"entries={len(merged_entries)} refs={len(merged_refs)}")


if __name__ == "__main__":
    main()
