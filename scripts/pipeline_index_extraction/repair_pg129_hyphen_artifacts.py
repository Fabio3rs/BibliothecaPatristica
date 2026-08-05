#!/usr/bin/env python3
"""Repair PG129 ORDO RERUM hyphen artifacts and sync intermediate payload files.

Usage:
    python scripts/pipeline_index_extraction/repair_pg129_hyphen_artifacts.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG129_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG129"

ENTRY_KEY = "PG129:entry:011"
FIXED_LEMMA = "Lectiones varias utriusque codicis Mosquensis ad quatuor Evangelia."
FIXED_ENTRY = "Lectiones varias utriusque codicis Mosquensis ad quatuor Evangelia. 75"
FIXED_HEADER_EXCERPT = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def repair_entry(entry: dict) -> None:
    if entry.get("entry_key") != ENTRY_KEY:
        return
    entry["lemma_raw"] = FIXED_LEMMA
    entry["lemma_display"] = FIXED_LEMMA
    entry["lemma_norm"] = "lectiones varias utriusque codicis mosquensis ad quatuor evangelia"
    entry["lemma_sort"] = "lectiones varias utriusque codicis mosquensis ad quatuor evangelia"
    entry["entry_raw"] = FIXED_ENTRY
    raw = entry.setdefault("raw_json", {})
    raw["line_count"] = 1
    raw["ocr_hyphenation_note"] = (
        "Merged the spurious OCR line-break hyphen in 'quatuor' after confirming the "
        "reader output for file 760."
    )


def repair_section(section: dict) -> None:
    raw = section.setdefault("raw_json", {})
    if raw.get("header_excerpt"):
        raw["header_excerpt"] = FIXED_HEADER_EXCERPT


def repair_payload() -> None:
    payload = load_json(PAYLOAD_PATH)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for section in payload.get("sections", []):
        repair_section(section)
    for entry in payload.get("entries", []):
        repair_entry(entry)
    write_json(PAYLOAD_PATH, payload)


def repair_intermediate() -> None:
    sections_path = INTERMEDIATE_DIR / "sections.json"
    entries_path = INTERMEDIATE_DIR / "entries.json"
    todo_path = INTERMEDIATE_DIR / "todo.json"

    sections = load_json(sections_path)
    for section in sections:
        repair_section(section)
    write_json(sections_path, sections)

    entries = load_json(entries_path)
    for entry in entries:
        repair_entry(entry)
    write_json(entries_path, entries)

    todo = load_json(todo_path)
    todo["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    todo["current_focus"] = "Validate repaired PG129 ORDO RERUM payload after hyphen cleanup"
    todo["completed"] = [
        "tail OCR parsed",
        "helper request built and resolved",
        "payload assembled",
        "confirmed that 'quatuor' is unhyphenated in OCR reader output for file 760",
    ]
    todo["pending"] = ["Run import_alphabetical_index_json.py --validate-only"]
    todo["blocked"] = []
    todo["notes"] = [
        "The volume closes with a single ORDO RERUM table.",
        "OCR file suffixes were not treated as editorial page numbers.",
        "Removed the false line-break hyphen from entry PG129:entry:011 and trimmed the isolated header dash from header_excerpt.",
    ]
    write_json(todo_path, todo)


def main() -> None:
    repair_payload()
    repair_intermediate()


if __name__ == "__main__":
    main()
