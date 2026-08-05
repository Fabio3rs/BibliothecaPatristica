#!/usr/bin/env python3
"""Repair PG147 alphabetical payload hyphen-wrap artifacts.

Usage:
  python scripts/pipeline_index_extraction/repair_pg147_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG147_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG147/todo.json"

SPLIT_WORD_RE = re.compile(r"(?<=\w)-\s+(?=\w)")


def clean_text(value: str) -> tuple[str, bool]:
    cleaned = SPLIT_WORD_RE.sub("", value)
    return cleaned, cleaned != value


def walk(node):
    changed = 0
    if isinstance(node, str):
        return clean_text(node)
    if isinstance(node, list):
        new_list = []
        for item in node:
            cleaned_item, item_changed = walk(item)
            new_list.append(cleaned_item)
            changed += int(item_changed)
        return new_list, bool(changed)
    if isinstance(node, dict):
        new_dict = {}
        for key, value in node.items():
            cleaned_value, item_changed = walk(value)
            new_dict[key] = cleaned_value
            changed += int(item_changed)
        return new_dict, bool(changed)
    return node, False


def main() -> None:
    data = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    cleaned, _ = walk(data)
    PAYLOAD_PATH.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    todo = json.loads(TODO_PATH.read_text(encoding="utf-8"))
    todo["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    todo["current_focus"] = "Validate repaired PG147 alphabetical payload"
    todo["completed"] = todo.get("completed", []) + ["Repaired line-break hyphen artifacts in the final payload"]
    pending = [item for item in todo.get("pending", []) if "assemble final payload" not in item.lower()]
    pending.insert(0, "Run import_alphabetical_index_json.py --validate-only")
    todo["pending"] = pending
    todo.setdefault("blocked", [])
    notes = todo.get("notes", [])
    notes.append("Applied conservative split-word cleanup to OCR-derived text fields in the final payload.")
    todo["notes"] = notes
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
