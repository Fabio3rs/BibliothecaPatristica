#!/usr/bin/env python3
"""Fix PG054 alphabetical payload OCR line-break hyphenation.

Usage:
  python scripts/pipeline_index_extraction/fix_pg054_hyphenation_payload.py

The script rewrites the PG054 payload in place, merging letter-hyphen-space-letter
artifacts such as "Abra- ham" while preserving numeric ranges such as "428-434".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG054_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG054/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])- +([{WORD_CHARS}])")


def clean_string(value: str) -> tuple[str, int]:
    total = 0
    previous = None
    current = value
    while previous != current:
        previous = current
        current, count = LINEBREAK_HYPHEN_RE.subn(r"\1\2", current)
        total += count
    return current, total


def clean_json(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return clean_string(value)
    if isinstance(value, list):
        changed = 0
        cleaned = []
        for item in value:
            new_item, count = clean_json(item)
            changed += count
            cleaned.append(new_item)
        return cleaned, changed
    if isinstance(value, dict):
        changed = 0
        cleaned = {}
        for key, item in value.items():
            new_item, count = clean_json(item)
            changed += count
            cleaned[key] = new_item
        return cleaned, changed
    return value, 0


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    cleaned, replacement_count = clean_json(payload)
    cleaned["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    notes = cleaned.setdefault("notes", [])
    note = (
        "PG054 rerun: corrected OCR line-break hyphenation artifacts in ORDO RERUM "
        f"payload strings; replacements={replacement_count}."
    )
    if note not in notes:
        notes.append(note)
    PAYLOAD_PATH.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = {
        "volume_id": "PG054",
        "updated_at": cleaned["generated_at"],
        "current_focus": "Validate corrected ORDO RERUM payload after hyphenation repair",
        "completed": [
            "confirmed ORDO RERUM section spans OCR files 355-358",
            "verified offending entries 49-90 against OCR pages 357-358",
            f"removed {replacement_count} letter-hyphen-space-letter artifacts from payload strings",
        ],
        "pending": [
            "run import_alphabetical_index_json.py validation",
            "inspect any remaining localized validation failures",
        ],
        "blocked": [],
        "notes": [
            "Numeric ranges such as 428-434 were intentionally preserved.",
            "The repair targets only letter-hyphen-space-letter OCR line-break artifacts.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"payload": str(PAYLOAD_PATH), "replacements": replacement_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
