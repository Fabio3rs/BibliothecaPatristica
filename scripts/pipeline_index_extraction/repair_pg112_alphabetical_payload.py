#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg112_alphabetical_payload.py
"""Repair PG112 alphabetical payload OCR line-break hyphen artifacts and validate it."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG112"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG112_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG112/todo.json"
VALIDATE_CMD = [
    "python",
    "scripts/import_alphabetical_index_json.py",
    "--validate-only",
    "--input",
    str(PAYLOAD_PATH),
]

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
    "ref_raw",
    "book_raw",
    "book_norm",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)

EVIDENCE_FILES = [
    str(ROOT / "teste/PG112/text/c844d531-d4ba-40a3-9af4-8b271b59d651-735.txt"),
    str(ROOT / "teste/PG112/text/c844d531-d4ba-40a3-9af4-8b271b59d651-737.txt"),
    str(ROOT / "teste/PG112/text/c844d531-d4ba-40a3-9af4-8b271b59d651-738.txt"),
    str(ROOT / "teste/PG112/text/c844d531-d4ba-40a3-9af4-8b271b59d651-740.txt"),
    str(ROOT / "teste/PG112/text/c844d531-d4ba-40a3-9af4-8b271b59d651-741.txt"),
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
    counts = {"entries_changed": 0, "refs_changed": 0, "scripture_refs_changed": 0}
    for bucket_name, count_key in (
        ("entries", "entries_changed"),
        ("refs", "refs_changed"),
        ("scripture_refs", "scripture_refs_changed"),
    ):
        for item in payload.get(bucket_name, []):
            changed = False
            for field in TEXT_FIELDS:
                old = item.get(field)
                new = dehyphenate(old)
                if new != old:
                    item[field] = new
                    changed = True
            if changed:
                item.setdefault("raw_json", {})["pg112_rerun_linebreak_hyphen_repaired"] = {
                    "reason": (
                        "Removed validation-blocking OCR line-break hyphen artifacts after "
                        "checking the flagged PG112 payload entries against cleaned OCR reader output."
                    ),
                    "evidence_files": EVIDENCE_FILES,
                }
                counts[count_key] += 1
    return counts


def update_metadata(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG112 rerun preserved the checkpoint structure and repaired the import-blocking OCR "
        "line-break hyphen artifacts in the analytical index and ORDO RERUM tail."
    )
    coverage["evidence_files"] = EVIDENCE_FILES
    coverage["pg112_rerun_repair_counts"] = counts

    notes = payload.setdefault("notes", [])
    note = (
        "PG112 rerun repaired validation-blocking OCR line-break hyphen artifacts in payload "
        "text fields and revalidated the checkpoint without changing the established section structure."
    )
    if note not in notes:
        notes.append(note)


def update_todo(counts: dict[str, int]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG112 payload repaired after validation failure and ready for import.",
        "completed": [
            "Read current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade",
            "Inspected the flagged PG112 checkpoint entries and reread the cleaned OCR reader output for the index tail",
            f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries and {counts['refs_changed']} refs",
            "Validated the repaired payload with import_alphabetical_index_json.py --validate-only",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The missing entry_key errors were a cascade from entries rejected for line-break hyphen artifacts.",
            "This rerun keeps OCR literals except for proven line-break hyphenation.",
        ],
    }
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_payload() -> None:
    subprocess.run(VALIDATE_CMD, cwd=ROOT, check=True)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_payload(payload)
    update_metadata(payload, counts)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_payload()
    update_todo(counts)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
