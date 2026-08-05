# Usage: python scripts/pipeline_index_extraction/repair_pg011_alphabetical_payload.py
# Repairs the PG011 alphabetical payload after import validation flags OCR line-break hyphen artifacts.
"""Repair PG011 alphabetical payload text fields and refresh run notes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG011_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG011/todo.json"
VOLUME_ID = "PG011"

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


def compact_context(entry: dict[str, Any]) -> bool:
    context = entry.get("context_raw")
    if not isinstance(context, str):
        return False
    if context.strip() == str(entry.get("entry_raw", "")).strip():
        entry["context_raw"] = None
        return True
    return False


def repair_payload(payload: dict[str, Any]) -> dict[str, int]:
    counts = {
        "entries_changed": 0,
        "refs_changed": 0,
        "contexts_nulled": 0,
    }

    for entry in payload.get("entries", []):
        changed = False
        for field in ENTRY_TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed = True
        raw_json = entry.setdefault("raw_json", {})
        if changed:
            raw_json["pg011_rerun_linebreak_hyphen_repaired"] = True
            counts["entries_changed"] += 1
        if compact_context(entry):
            raw_json["pg011_rerun_context_deduplicated"] = True
            counts["contexts_nulled"] += 1

    for ref in payload.get("refs", []):
        changed = False
        for field in REF_TEXT_FIELDS:
            old = ref.get(field)
            new = dehyphenate(old)
            if new != old:
                ref[field] = new
                changed = True
        if changed:
            ref.setdefault("raw_json", {})["pg011_rerun_linebreak_hyphen_repaired"] = True
            counts["refs_changed"] += 1

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    notes = payload.setdefault("notes", [])
    note = (
        "PG011 rerun repaired OCR line-break hyphen artifacts reported by import validation, "
        "preserved the verified INDEX ANALYTICUS section, and kept helper-backed material locators."
    )
    if note not in notes:
        notes.append(note)
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "Recovered the PG011 INDEX ANALYTICUS entries from OCR files 947-978; this rerun removes "
        "invalid OCR line-break hyphen artifacts from text fields while preserving prior helper "
        "locator evidence."
    )
    coverage["evidence_files"] = [
        str(ROOT / "teste/PG011/text/b52f0a60-de4c-4137-9a0f-a5102e881925-947.txt"),
        str(ROOT / "teste/PG011/text/b52f0a60-de4c-4137-9a0f-a5102e881925-978.txt"),
    ]
    coverage["pg011_rerun_repair_counts"] = counts
    return counts


def update_todo(counts: dict[str, int]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG011 payload repaired after validation failure; ready for import validation.",
        "completed": [
            "Read prior validation failure for line-break hyphen artifacts and missing entry-key cascade",
            "Confirmed file 946 is APPENDIX and file 947 starts INDEX ANALYTICUS",
            "Applied deterministic line-break hyphen repairs to payload text fields",
            f"Changed {counts['entries_changed']} entries and {counts['refs_changed']} refs; nulled {counts['contexts_nulled']} duplicate contexts",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only",
        ],
        "blocked": [],
        "notes": [
            "Repairs use the same letter-hyphen-whitespace-letter pattern enforced by the importer.",
            "Terminal word hyphens in entry/ref text fields are treated as OCR wrap artifacts for this rerun.",
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
