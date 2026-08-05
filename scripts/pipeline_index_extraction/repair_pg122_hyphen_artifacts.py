# Usage: python scripts/pipeline_index_extraction/repair_pg122_hyphen_artifacts.py
# Repairs PG122 payload OCR line-break hyphen artifacts confirmed against cleaned OCR reader output.
"""Repair PG122 alphabetical payload after validation flags OCR line-break hyphen artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG122"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG122_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG122/todo.json"

ENTRY_KEYS_TO_REPAIR = {
    "PG122:entry:0001",
    "PG122:entry:0002",
    "PG122:entry:0003",
    "PG122:entry:0005",
    "PG122:entry:0012",
    "PG122:entry:0013",
    "PG122:entry:0014",
    "PG122:entry:0015",
    "PG122:entry:0016",
    "PG122:entry:0017",
    "PG122:entry:0020",
    "PG122:entry:0021",
    "PG122:entry:0022",
    "PG122:entry:0027",
    "PG122:entry:0028",
    "PG122:entry:0029",
    "PG122:entry:0030",
    "PG122:entry:0158",
    "PG122:entry:0159",
    "PG122:entry:0161",
    "PG122:entry:0173",
}

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")

TEXT_FIELDS = (
    "entry_raw",
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "context_raw",
)

EVIDENCE_FILES = [
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-774.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-775.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-777.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-778.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-779.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-780.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-781.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-784.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-789.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-790.txt"),
    str(ROOT / "teste/PG122/text/39af1f43-4060-4d98-83b7-09d480bb3fca-792.txt"),
]


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            break
        updated = merged
    return TRAILING_WORD_HYPHEN_RE.sub(r"\1", updated)


def repair_payload(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "fields_changed": 0}
    seen = set()

    for entry in payload.get("entries", []):
        entry_key = entry.get("entry_key")
        if entry_key not in ENTRY_KEYS_TO_REPAIR:
            continue
        seen.add(entry_key)
        changed_fields = []
        for field in TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed_fields.append(field)
                counts["fields_changed"] += 1
        if changed_fields:
            entry.setdefault("raw_json", {})["pg122_rerun_linebreak_hyphen_repaired"] = {
                "reason": (
                    "Removed validation-blocking OCR line-break hyphen artifacts after checking "
                    "the flagged PG122 payload entries against cleaned OCR reader output."
                ),
                "changed_fields": changed_fields,
                "evidence_files": EVIDENCE_FILES,
            }
            counts["entries_changed"] += 1

    missing = sorted(ENTRY_KEYS_TO_REPAIR - seen)
    if missing:
        raise SystemExit(f"Missing targeted entries in payload: {missing}")
    return counts


def assert_relationships(payload: dict[str, Any]) -> None:
    section_keys = {section["section_key"] for section in payload.get("sections", [])}
    entry_keys = {entry["entry_key"] for entry in payload.get("entries", [])}
    missing_section_links = [
        entry["entry_key"]
        for entry in payload.get("entries", [])
        if entry.get("section_key") not in section_keys
    ]
    missing_entry_links = [
        ref["entry_key"] for ref in payload.get("refs", []) if ref.get("entry_key") not in entry_keys
    ]
    if missing_section_links:
        raise SystemExit(f"Entries reference missing sections: {missing_section_links[:20]}")
    if missing_entry_links:
        raise SystemExit(f"Refs reference missing entries: {missing_entry_links[:20]}")


def update_metadata(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG122 rerun preserved the checkpoint structure and repaired the import-blocking OCR "
        "line-break hyphen artifacts confirmed against the cleaned OCR reader output for the "
        "Cedrenus index tail and ORDO RERUM closure pages."
    )
    coverage["evidence_files"] = EVIDENCE_FILES
    coverage["pg122_rerun_repair_counts"] = counts

    notes = payload.setdefault("notes", [])
    note = (
        "PG122 rerun repaired validation-blocking OCR line-break hyphen artifacts in 21 targeted "
        "entries after checking the cleaned OCR reader output for files 774-781, 784, 789-790, "
        "and 792; the missing entry-key validation errors were a cascade from those rejected entries."
    )
    if note not in notes:
        notes.append(note)


def update_todo(counts: dict[str, int]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG122 hyphen-artifact rerun repaired; validate final payload.",
        "completed": [
            "Read the current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade",
            "Rechecked the flagged PG122 entries against cleaned OCR reader output for the affected Cedrenus and ORDO RERUM files",
            f"Merged confirmed OCR line-break hyphen artifacts in {counts['entries_changed']} entries across {counts['fields_changed']} text fields",
            "Rechecked section/entry/ref relationships after the targeted repairs",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only --print-summary"
        ],
        "blocked": [],
        "notes": [
            "Repairs are limited to the exact entry_key set listed by the prior validation failure.",
            "No new locator decisions were introduced in refs; the missing entry-key errors were downstream of the invalid entries.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_payload(payload)
    assert_relationships(payload)
    update_metadata(payload, counts)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_todo(counts)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
