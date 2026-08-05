#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pl022_alphabetical_payload.py
# Rebuild the PL022 alphabetical payload from the assembled-fragments checkpoint
# and reindex entry_order values globally within each section.
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = ROOT / "data/intermediate_payloads/PL022/assembled_fragments.json"
EXISTING_PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL022_alphabetical_indices.json"
OUTPUT_PATH = EXISTING_PAYLOAD_PATH
TODO_PATH = ROOT / "data/intermediate_payloads/PL022/todo.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def reindex_entries(entries: list[dict[str, Any]]) -> None:
    next_order_by_section: dict[str, int] = {}
    for entry in entries:
        section_key = entry["section_key"]
        next_order = next_order_by_section.get(section_key, 1)
        entry["entry_order"] = next_order
        next_order_by_section[section_key] = next_order + 1


def validate_wrapper_vs_existing(
    wrapper_entries: list[dict[str, Any]],
    wrapper_refs: list[dict[str, Any]],
    existing_payload: dict[str, Any],
) -> None:
    existing_entries = existing_payload["entries"]
    existing_refs = existing_payload["refs"]

    wrapper_entry_keys = [entry["entry_key"] for entry in wrapper_entries]
    existing_entry_keys = [entry["entry_key"] for entry in existing_entries]
    if wrapper_entry_keys != existing_entry_keys:
        raise ValueError("Existing payload entry ordering drifted from assembled_fragments.json.")

    wrapper_ref_keys = [
        (ref["entry_key"], ref["ref_order"], ref["ref_raw"])
        for ref in wrapper_refs
    ]
    existing_ref_keys = [
        (ref["entry_key"], ref["ref_order"], ref["ref_raw"])
        for ref in existing_refs
    ]
    if wrapper_ref_keys != existing_ref_keys:
        raise ValueError("Existing payload refs drifted from assembled_fragments.json.")


def build_payload(wrapper: dict[str, Any], existing_payload: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(wrapper["data"])
    entries = data["entries"]
    reindex_entries(entries)

    notes = list(existing_payload.get("notes", []))
    note = (
        "Section-local chunk entry_order collisions were renumbered globally within each "
        "section_key to satisfy the importer uniqueness constraint."
    )
    if note not in notes:
        notes.append(note)

    coverage = deepcopy(existing_payload.get("coverage", {}))
    coverage["entries_status"] = "recovered"
    coverage["entries_status_reason"] = (
        "Rebuilt from validated closing-index fragments in "
        "data/intermediate_payloads/PL022/assembled_fragments.json and renumbered globally "
        "within each section_key so alphabetical_entries(section_key, entry_order) is unique."
    )

    payload = deepcopy(existing_payload)
    payload["generated_at"] = utc_now()
    payload["sections"] = data["sections"]
    payload["nodes"] = data["nodes"]
    payload["entries"] = entries
    payload["refs"] = data["refs"]
    payload["scripture_refs"] = data["scripture_refs"]
    payload["coverage"] = coverage
    payload["notes"] = notes
    return payload


def update_todo() -> None:
    todo = {
        "volume_id": "PL022",
        "updated_at": utc_now(),
        "current_focus": "PL022 payload rebuilt and awaiting importer validation",
        "completed": [
            "Confirmed from OCR that files 694-697 are epistolary alphabetical indexes and 698-704 are INDEX RERUM / ordo_rerum.",
            "Verified the import failure came from duplicate entry_order values inside PL022:candidate-section:001.",
            "Consumed the stable objects from assembled_fragments.json and rebuilt the canonical payload from them.",
            "Renumbered entry_order globally within each section_key without changing entry_key or ref objects.",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only on the rebuilt payload."
        ],
        "blocked": [],
        "notes": [
            "The only structural fix is entry ordering inside the ordo_rerum section.",
            "Refs and scripture_refs were preserved from the assembled fragment wrapper.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    wrapper = load_json(WRAPPER_PATH)
    existing_payload = load_json(EXISTING_PAYLOAD_PATH)
    validate_wrapper_vs_existing(wrapper["data"]["entries"], wrapper["data"]["refs"], existing_payload)
    payload = build_payload(wrapper, existing_payload)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_todo()
    print(json.dumps({"written_file": str(OUTPUT_PATH), "entries": len(payload["entries"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
