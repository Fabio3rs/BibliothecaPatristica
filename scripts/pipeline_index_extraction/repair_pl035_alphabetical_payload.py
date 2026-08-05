# Usage: python scripts/pipeline_index_extraction/repair_pl035_alphabetical_payload.py
# Rebuild the PL035 alphabetical payload from the assembled-fragments wrapper,
# normalize it to the canonical import format, and reindex entries globally.
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = ROOT / "data/intermediate_payloads/PL035/assembled_fragments.json"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL035_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PL035/todo.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_wrapper() -> dict[str, Any]:
    return json.loads(WRAPPER_PATH.read_text(encoding="utf-8"))


def transform_payload(wrapper: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(wrapper["data"])
    entries = data["entries"]
    refs = data["refs"]
    sections = data["sections"]
    notes = list(data.get("notes", []))

    # Reindex entries globally so the single section can satisfy the importer
    # uniqueness constraint on (section_key, entry_order).
    for idx, entry in enumerate(entries, start=1):
        entry["entry_order"] = idx

    entry_by_key = {entry["entry_key"]: entry for entry in entries}
    section = sections[0]
    section_key = section["section_key"]
    source_files = [
        str(ROOT / f"teste/PL035/text/fee2b1f0-c5a2-413b-bb2f-20ff81c04f56-{seq}.txt")
        for seq in range(574, 588)
    ]

    canonical_section = {
        "section_key": section_key,
        "volume_id": wrapper["volume_id"],
        "work_key": None,
        "section_order": section["section_order"],
        "section_kind": "ordo_rerum",
        "heading_raw": section["heading_raw"],
        "heading_norm": section["heading_raw"].lower(),
        "heading_letter": None,
        "page_start": 2169,
        "page_end": 2480,
        "file_start": source_files[0],
        "file_end": source_files[-1],
        "confidence": 0.94,
        "raw_json": {
            "source_window": source_files,
            "section_kind_reason": (
                "Ordered contents / index rerum with tractate and work titles rather than "
                "a lettered alphabetical lemma list."
            ),
            "notes": [
                "OCR pagination drifts across the chunk; physical order is authoritative for ownership, not filename suffix equality."
            ],
            "header_evidence": section.get("header_evidence", []),
            "scan_diagnostics": wrapper.get("scan_diagnostics", {}),
        },
    }

    canonical_refs = []
    for ref in refs:
        ref_out = deepcopy(ref)
        entry = entry_by_key.get(ref_out["entry_key"])
        ref_out.setdefault("page_ref_col", None)
        ref_out.setdefault("line_ref_raw", None)
        ref_out.setdefault("range_start_raw", None)
        ref_out.setdefault("range_end_raw", None)
        ref_out.setdefault("target_file", None)
        ref_out.setdefault("target_file_probability", None)
        ref_out.setdefault("section_start_file", entry.get("section_start_file") if entry else None)
        ref_out.setdefault("editorial_anchor_file", entry.get("editorial_anchor_file") if entry else None)
        canonical_refs.append(ref_out)

    canonical = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "volume": {
            "volume_id": wrapper["volume_id"],
            "collection": wrapper["volume_id"][:2],
            "source_root": str(ROOT / "teste/PL035/text"),
            "volume_label": wrapper["volume_id"],
            "notes": "Index Rerum / contents-style ordo_rerum block recovered from the tail OCR pages.",
        },
        "sections": [canonical_section],
        "nodes": data["nodes"],
        "entries": entries,
        "refs": canonical_refs,
        "scripture_refs": data["scripture_refs"],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": (
                "Reassembled from validated fragments and renumbered globally to satisfy "
                "the importer's unique entry_order constraint."
            ),
            "evidence_files": source_files,
        },
        "notes": notes,
    }
    return canonical


def update_todo() -> None:
    todo = {
        "volume_id": "PL035",
        "updated_at": utc_now(),
        "current_focus": "PL035 alphabetical payload rebuilt as canonical JSON and ready for validation",
        "completed": [
            "Inspected the duplicate entry_order failure in the current payload.",
            "Verified the offending collisions were chunk-local repeats inside the same section_key.",
            "Rebuilt the final payload object from the assembled fragment wrapper.",
            "Renumbered entries globally so section_key + entry_order is unique.",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only on the rebuilt payload.",
        ],
        "blocked": [],
        "notes": [
            "The wrapper payload was converted to the canonical import shape before writing the output file.",
            "Refs keep their existing locator evidence; missing target_file fields remain null unless the fragment already supplied them.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    wrapper = load_wrapper()
    canonical = transform_payload(wrapper)
    OUTPUT_PATH.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_todo()
    print(json.dumps({"written_file": str(OUTPUT_PATH), "entries": len(canonical["entries"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
