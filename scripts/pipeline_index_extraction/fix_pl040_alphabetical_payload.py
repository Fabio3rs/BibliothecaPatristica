#!/usr/bin/env python3
"""Repair PL040 alphabetical payload ordering from assembled_fragments.json.

Usage:
    python scripts/pipeline_index_extraction/fix_pl040_alphabetical_payload.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PL040_alphabetical_indices.json"
FRAGMENTS_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL040/assembled_fragments.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL040/todo.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_note(note: object) -> str:
    if isinstance(note, str):
        return note
    if isinstance(note, dict):
        return json.dumps(note, ensure_ascii=False, sort_keys=True)
    return str(note)


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    fragments = load_json(FRAGMENTS_PATH)
    stable = fragments["data"]

    for key in ("sections", "nodes", "entries", "refs", "scripture_refs"):
        if len(payload[key]) != len(stable[key]):
            raise SystemExit(f"Count mismatch for {key}: payload={len(payload[key])} fragments={len(stable[key])}")

    payload_entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    stable_entry_keys = {entry["entry_key"] for entry in stable["entries"]}
    if payload_entry_keys != stable_entry_keys:
        raise SystemExit("Payload and assembled fragments disagree on entry_key membership.")

    payload_section_keys = {section["section_key"] for section in payload["sections"]}
    stable_section_keys = {section["section_key"] for section in stable["sections"]}
    if payload_section_keys != stable_section_keys:
        raise SystemExit("Payload and assembled fragments disagree on section_key membership.")

    rebuilt = deepcopy(payload)
    rebuilt["generated_at"] = now_iso()
    rebuilt["sections"] = deepcopy(stable["sections"])
    rebuilt["nodes"] = deepcopy(stable["nodes"])
    rebuilt["refs"] = deepcopy(stable["refs"])
    rebuilt["scripture_refs"] = deepcopy(stable["scripture_refs"])

    stable_notes = deepcopy(stable["notes"])
    current_notes = payload.get("notes", [])
    stable_note_keys = {normalize_note(note) for note in stable_notes}
    for note in current_notes:
        if normalize_note(note) not in stable_note_keys:
            stable_notes.append(note)
    rebuilt["notes"] = stable_notes

    entries = deepcopy(stable["entries"])
    next_order_by_section: defaultdict[str, int] = defaultdict(int)
    for entry in entries:
        next_order_by_section[entry["section_key"]] += 1
        entry["entry_order"] = next_order_by_section[entry["section_key"]]
    rebuilt["entries"] = entries

    duplicates = [
        (section_key, order)
        for (section_key, order), count in Counter(
            (entry["section_key"], entry["entry_order"]) for entry in rebuilt["entries"]
        ).items()
        if count > 1
    ]
    if duplicates:
        raise SystemExit(f"Duplicate (section_key, entry_order) pairs remain: {duplicates[:10]}")

    PAYLOAD_PATH.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = load_json(TODO_PATH)
    completed = list(todo.get("completed", []))
    pending = [item for item in todo.get("pending", []) if item != "rebuild refs.json"]
    for item in (
        "repaired PL040 entry_order globally per section from assembled_fragments",
        "validated stable object reuse from assembled_fragments for sections/entries/refs",
    ):
        if item not in completed:
            completed.append(item)
    if "run import_alphabetical_index_json.py --validate-only" not in pending:
        pending.append("run import_alphabetical_index_json.py --validate-only")
    todo.update(
        {
            "updated_at": now_iso(),
            "current_focus": "Validate repaired PL040 payload after global entry_order renumbering",
            "completed": completed,
            "pending": pending,
            "blocked": todo.get("blocked", []),
            "notes": list(todo.get("notes", []))
            + [
                "Global entry_order now follows assembled_fragments list order per section.",
                "Stable entry_key/ref relationships were preserved; only section-scoped entry ordering changed.",
            ],
        }
    )
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
