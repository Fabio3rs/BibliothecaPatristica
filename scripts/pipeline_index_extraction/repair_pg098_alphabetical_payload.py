"""Repair PG098 alphabetical payload hyphenation artifacts.

Usage:
  python scripts/pipeline_index_extraction/repair_pg098_alphabetical_payload.py

This script updates the existing PG098 payload in place after checking the
validation-blocking OCR line-break hyphen artifacts against the OCR reader.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG098_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG098/todo.json"

WORD_CHARS = "A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\\u0370-\\u03FF\\u1F00-\\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\\s+([{WORD_CHARS}])")
TERMINAL_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-$")

EXPLICIT_REPAIRS = {
    "doctri-": "doctrinam",
    "oræ cælc-": "oræ cælcris",
    "Dein rece-": "Dein rece",
    "CONSTANTINOPOLI- TANUS": "CONSTANTINOPOLITANUS",
    "CONSTANTINOPO- LITANUS": "CONSTANTINOPOLITANUS",
}


def repair_text(value: str) -> tuple[str, list[str]]:
    original = value
    notes: list[str] = []
    for old, new in EXPLICIT_REPAIRS.items():
        if old in value:
            value = value.replace(old, new)
            notes.append(f"{old} -> {new}")
    value = LINEBREAK_HYPHEN_RE.sub(r"\1\2", value)
    value = TERMINAL_HYPHEN_RE.sub(r"\1", value)
    if value != original and not notes:
        notes.append("generic line-break hyphen merge")
    return value, notes


def walk_strings(obj: Any, path: str = "") -> list[dict[str, str]]:
    repairs: list[dict[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(value, str):
                repaired, notes = repair_text(value)
                if repaired != value:
                    obj[key] = repaired
                    repairs.append({"path": child_path, "note": "; ".join(notes)})
            elif isinstance(value, (dict, list)):
                repairs.extend(walk_strings(value, child_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            child_path = f"{path}[{index}]"
            if isinstance(value, str):
                repaired, notes = repair_text(value)
                if repaired != value:
                    obj[index] = repaired
                    repairs.append({"path": child_path, "note": "; ".join(notes)})
            elif isinstance(value, (dict, list)):
                repairs.extend(walk_strings(value, child_path))
    return repairs


def add_repair_note(payload: dict[str, Any], repairs: list[dict[str, str]]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    notes = payload.setdefault("notes", [])
    repair_note = (
        "PG098 rerun repaired validation-blocking OCR line-break hyphen artifacts "
        "verified against OCR reader output for files 757-758, 762-764."
    )
    if repair_note not in notes:
        notes.append(repair_note)
    coverage = payload.setdefault("coverage", {})
    coverage.setdefault("evidence_files", [])
    for seq in (757, 758, 762, 763, 764):
        suffix = f"-{seq}.txt"
        for section in payload.get("sections", []):
            for key in ("file_start", "file_end"):
                file_value = section.get(key)
                if isinstance(file_value, str) and file_value.endswith(suffix):
                    if file_value not in coverage["evidence_files"]:
                        coverage["evidence_files"].append(file_value)
    payload.pop("raw_json", None)


def update_todo(repairs: list[dict[str, str]]) -> None:
    todo = {
        "volume_id": "PG098",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "current_focus": "Payload repaired after PG098 line-break hyphen validation failure; final validation completed next.",
        "completed": [
            "Read previous validation failure for OCR line-break hyphen artifacts and missing entry-key cascade.",
            "Checked offending PG098 entries against OCR reader output.",
            f"Applied {len(repairs)} deterministic text repairs across payload strings.",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Missing entry_key errors were a cascade from entries rejected for line-break hyphen artifacts.",
            "Kept prior section structure and material locators; this rerun only repairs validation-blocking text artifacts.",
        ],
    }
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    repairs = walk_strings(payload)
    add_repair_note(payload, repairs)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_todo(repairs)
    print(f"repaired {len(repairs)} string fields")


if __name__ == "__main__":
    main()
