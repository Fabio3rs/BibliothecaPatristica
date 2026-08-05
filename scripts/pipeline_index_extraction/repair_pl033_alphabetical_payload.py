#!/usr/bin/env python3
"""Repair PL033 alphabetical payload from assembled fragments.

Usage:
    python scripts/pipeline_index_extraction/repair_pl033_alphabetical_payload.py \
        --fragments data/intermediate_payloads/PL033/assembled_fragments.json \
        --previous data/alphabetical_index_payloads/PL033_alphabetical_indices.json \
        --output data/alphabetical_index_payloads/PL033_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


FULL_THEMATIC = "PL033:candidate-section:001"
THEMATIC_SUBSET = "PL033:candidate-section:002:001"
FULL_ALPHABETICAL = "PL033:candidate-section:002:002"
ALPHABETICAL_SUBSET = "PL033:candidate-section:003:002"
ORDO_SECTION = "PL033:candidate-section:003:001"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clone_entry(entry: dict, new_section_key: str) -> dict:
    cloned = copy.deepcopy(entry)
    old_section_key = cloned["section_key"]
    cloned["section_key"] = new_section_key
    cloned["entry_key"] = cloned["entry_key"].replace(old_section_key, new_section_key, 1)
    return cloned


def clone_ref(ref: dict, entry_key_map: dict[str, str]) -> dict:
    cloned = copy.deepcopy(ref)
    cloned["entry_key"] = entry_key_map[cloned["entry_key"]]
    return cloned


def dedupe_notes(notes: list) -> list:
    seen: set[str] = set()
    result = []
    for note in notes:
        key = json.dumps(note, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(note)
    return result


def sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda item: (item.get("entry_order", 0), item["entry_key"]))


def has_material_anchor(ref: dict) -> bool:
    return any(
        ref.get(field)
        for field in ("page_ref_raw", "target_file", "range_start_raw", "range_end_raw")
    )


def normalize_refs(refs: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for ref in refs:
        grouped[ref["entry_key"]].append(ref)
    normalized: list[dict] = []
    for entry_key in sorted(grouped):
        bucket = sorted(grouped[entry_key], key=lambda item: item.get("ref_order", 0))
        anchored_exists = any(has_material_anchor(ref) for ref in bucket)
        cleaned = [
            ref for ref in bucket
            if has_material_anchor(ref) or not anchored_exists
        ]
        for order, ref in enumerate(cleaned, start=1):
            ref["ref_order"] = order
            normalized.append(ref)
    return normalized


def normalize_scripture_refs(refs: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for ref in refs:
        grouped[ref["entry_key"]].append(ref)
    normalized: list[dict] = []
    for entry_key in sorted(grouped):
        bucket = sorted(grouped[entry_key], key=lambda item: (item.get("ref_order", 0), item.get("ref_role", "")))
        for order, ref in enumerate(bucket, start=1):
            ref["ref_order"] = order
            normalized.append(ref)
    return normalized


def repair_payload(fragments_path: Path, previous_path: Path) -> dict:
    fragments = load_json(fragments_path)["data"]
    previous = load_json(previous_path)

    sections = copy.deepcopy(fragments["sections"])
    section_index = {section["section_key"]: section for section in sections}
    ordered_section_keys = [
        FULL_THEMATIC,
        THEMATIC_SUBSET,
        FULL_ALPHABETICAL,
        ORDO_SECTION,
        ALPHABETICAL_SUBSET,
    ]
    for order, section_key in enumerate(ordered_section_keys, start=1):
        section_index[section_key]["section_order"] = order

    entries_by_section: dict[str, list[dict]] = defaultdict(list)
    for entry in fragments["entries"]:
        entries_by_section[entry["section_key"]].append(copy.deepcopy(entry))
    for section_key in list(entries_by_section):
        entries_by_section[section_key] = sort_entries(entries_by_section[section_key])

    refs_by_entry: dict[str, list[dict]] = defaultdict(list)
    for ref in fragments["refs"]:
        refs_by_entry[ref["entry_key"]].append(copy.deepcopy(ref))
    srefs_by_entry: dict[str, list[dict]] = defaultdict(list)
    for ref in fragments["scripture_refs"]:
        srefs_by_entry[ref["entry_key"]].append(copy.deepcopy(ref))

    new_entries: list[dict] = []
    new_refs: list[dict] = []
    new_srefs: list[dict] = []

    def emit_section(section_key: str, source_entries: list[dict]) -> None:
        for new_order, entry in enumerate(source_entries, start=1):
            entry["entry_order"] = new_order
            new_entries.append(entry)
            for ref in refs_by_entry.get(entry["entry_key"], []):
                new_refs.append(ref)
            for ref in srefs_by_entry.get(entry["entry_key"], []):
                new_srefs.append(ref)

    def build_clone_block(source_section_key: str, target_section_key: str) -> tuple[list[dict], dict[str, str]]:
        cloned_entries = []
        entry_key_map: dict[str, str] = {}
        for entry in entries_by_section[source_section_key]:
            cloned = clone_entry(entry, target_section_key)
            entry_key_map[entry["entry_key"]] = cloned["entry_key"]
            cloned_entries.append(cloned)
        return cloned_entries, entry_key_map

    thematic_subset_entries = entries_by_section[THEMATIC_SUBSET]
    thematic_full_existing = entries_by_section[FULL_THEMATIC]
    thematic_clones, thematic_map = build_clone_block(THEMATIC_SUBSET, FULL_THEMATIC)
    for source_key, new_key in thematic_map.items():
        refs_by_entry[new_key] = [clone_ref(ref, thematic_map) for ref in refs_by_entry.get(source_key, [])]
        srefs_by_entry[new_key] = [clone_ref(ref, thematic_map) for ref in srefs_by_entry.get(source_key, [])]

    alphabetical_subset_entries = entries_by_section[ALPHABETICAL_SUBSET]
    alphabetical_full_existing = entries_by_section[FULL_ALPHABETICAL]
    alphabetical_clones, alphabetical_map = build_clone_block(ALPHABETICAL_SUBSET, FULL_ALPHABETICAL)
    for source_key, new_key in alphabetical_map.items():
        refs_by_entry[new_key] = [clone_ref(ref, alphabetical_map) for ref in refs_by_entry.get(source_key, [])]
        srefs_by_entry[new_key] = [clone_ref(ref, alphabetical_map) for ref in srefs_by_entry.get(source_key, [])]

    emit_section(FULL_THEMATIC, thematic_clones + thematic_full_existing)
    emit_section(THEMATIC_SUBSET, thematic_subset_entries)
    emit_section(FULL_ALPHABETICAL, alphabetical_clones + alphabetical_full_existing)

    ordo_entries = []
    for entry in entries_by_section[ORDO_SECTION]:
        cloned = copy.deepcopy(entry)
        ordo_entries.append(cloned)
    emit_section(ORDO_SECTION, sort_entries(ordo_entries))

    alpha_subset_entries = []
    for entry in alphabetical_subset_entries:
        cloned = copy.deepcopy(entry)
        alpha_subset_entries.append(cloned)
    emit_section(ALPHABETICAL_SUBSET, sort_entries(alpha_subset_entries))

    notes = dedupe_notes(
        list(previous.get("notes", []))
        + list(fragments.get("notes", []))
        + [
            {
                "note_kind": "repair",
                "text": (
                    "Rebuilt the full thematic and alphabetical sections from assembled fragment subsets, "
                    "reassigned unique section_order values, and renumbered entry_order values to satisfy "
                    "SQLite uniqueness constraints while preserving the subset sections."
                ),
            }
        ]
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": previous["volume"],
        "sections": [section_index[key] for key in ordered_section_keys],
        "nodes": copy.deepcopy(fragments["nodes"]),
        "entries": new_entries,
        "refs": normalize_refs(new_refs),
        "scripture_refs": normalize_scripture_refs(new_srefs),
        "coverage": previous["coverage"],
        "notes": notes,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fragments", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--todo", type=Path)
    args = parser.parse_args()

    payload = repair_payload(args.fragments, args.previous)
    dump_json(args.output, payload)

    if args.todo:
        todo = load_json(args.todo) if args.todo.exists() else {"volume_id": "PL033"}
        todo["updated_at"] = now_iso()
        todo["current_focus"] = "Validate repaired payload import for PL033"
        completed = list(todo.get("completed", []))
        completed.append("rebuilt the payload from assembled fragments with unique section_order and entry_order values")
        todo["completed"] = completed
        todo["pending"] = [
            "run import_alphabetical_index_json.py against the repaired payload",
            "confirm the repaired payload imports cleanly",
        ]
        todo["blocked"] = []
        notes = list(todo.get("notes", []))
        notes.append("The full thematic section now includes cloned page-590 entries, and the full alphabetical section includes cloned page-588 entries.")
        todo["notes"] = notes
        dump_json(args.todo, todo)


if __name__ == "__main__":
    main()
