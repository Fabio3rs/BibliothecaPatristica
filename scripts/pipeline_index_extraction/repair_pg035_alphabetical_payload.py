# Usage: python scripts/pipeline_index_extraction/repair_pg035_alphabetical_payload.py
# Repairs PG035 ORDO RERUM payload OCR line-break hyphen artifacts after validation.
"""Repair PG035 alphabetical payload text fields and refresh checkpoint notes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG035"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG035_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG035/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")

ENTRY_TEXT_FIELDS = ("entry_raw", "lemma_raw", "lemma_display", "context_raw")
REF_TEXT_FIELDS = ("ref_raw",)


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
    counts = {
        "entries_changed": 0,
        "refs_changed": 0,
    }
    evidence_files = {
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-633.txt"),
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-634.txt"),
    }

    for entry in payload.get("entries", []):
        changed_fields: list[str] = []
        for field in ENTRY_TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed_fields.append(field)
        if changed_fields:
            entry.setdefault("raw_json", {})["pg035_rerun_linebreak_hyphen_repair"] = {
                "fields": changed_fields,
                "reason": "Merged OCR line-break hyphenation in ORDO RERUM entries after checking cleaned OCR reader output.",
                "evidence_files": sorted(evidence_files),
            }
            counts["entries_changed"] += 1

    for ref in payload.get("refs", []):
        changed_fields = []
        for field in REF_TEXT_FIELDS:
            old = ref.get(field)
            new = dehyphenate(old)
            if new != old:
                ref[field] = new
                changed_fields.append(field)
        if changed_fields:
            ref.setdefault("raw_json", {})["pg035_rerun_linebreak_hyphen_repair"] = {
                "fields": changed_fields,
                "reason": "Merged OCR line-break hyphenation in material reference text fields.",
            }
            counts["refs_changed"] += 1

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "ORDO RERUM entries were recovered from OCR files 631-634; this rerun removes "
        "validation-blocking OCR line-break hyphen artifacts while preserving prior material locators."
    )
    coverage["evidence_files"] = [
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-631.txt"),
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-632.txt"),
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-633.txt"),
        str(ROOT / "teste/PG035/text/946ae4e5-d9e6-47f3-adc6-efab65a58904-634.txt"),
    ]
    coverage["pg035_rerun_repair_counts"] = counts

    notes = payload.setdefault("notes", [])
    note = (
        "PG035 rerun repaired OCR line-break hyphen artifacts in ORDO RERUM text fields "
        "after checking the cleaned OCR reader output for files 633-634."
    )
    if note not in notes:
        notes.append(note)
    return counts


def update_todo(counts: dict[str, int]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG035 payload repaired after line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure and inspected the flagged PG035 entry objects.",
            "Checked ORDO RERUM OCR files 632-633 through scripts/read_ocr_page_text.py.",
            f"Applied deterministic line-break hyphen repairs to {counts['entries_changed']} entries and {counts['refs_changed']} refs.",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only.",
        ],
        "blocked": [],
        "notes": [
            "Repairs use the same letter-hyphen-whitespace-letter pattern enforced by the importer.",
            "Existing section structure, entry keys, refs, and helper-backed target files were preserved.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    counts = repair_payload(payload)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    update_todo(counts)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
