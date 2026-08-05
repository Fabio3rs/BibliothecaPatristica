# Repairs PG068 alphabetical payload OCR line-break hyphen artifacts.
# Run from the repository root:
#   python scripts/pipeline_index_extraction/repair_pg068_hyphen_artifacts.py
"""Repair validation-blocking split-word hyphens in the PG068 payload."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG068_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG068/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def repair_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub(r"\1\2", value)


def repair_object_text_fields(obj: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    changed: list[str] = []
    for field in fields:
        old = obj.get(field)
        new = repair_text(old)
        if new != old:
            obj[field] = new
            changed.append(field)
    return changed


def append_unique_note(notes: list[Any], note: str) -> None:
    if note not in notes:
        notes.append(note)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    changed_entries: dict[str, list[str]] = {}
    changed_refs: dict[str, list[str]] = {}

    entry_fields = (
        "lemma_raw",
        "lemma_display",
        "lemma_norm",
        "lemma_sort",
        "entry_raw",
        "context_raw",
    )
    ref_fields = (
        "ref_raw",
        "page_ref_raw",
        "line_ref_raw",
        "range_start_raw",
        "range_end_raw",
    )

    for entry in payload.get("entries", []):
        fields = repair_object_text_fields(entry, entry_fields)
        if fields:
            changed_entries[entry["entry_key"]] = fields
            raw_json = entry.setdefault("raw_json", {})
            repairs = raw_json.setdefault("rerun_repairs", [])
            if isinstance(repairs, list):
                append_unique_note(
                    repairs,
                    "PG068 rerun merged OCR line-break hyphenation in payload text fields after checking the cleaned OCR reader output for files 584-589.",
                )
        for ref in entry.get("refs", []) or []:
            fields = repair_object_text_fields(ref, ref_fields)
            if fields:
                changed_refs[f"{ref.get('entry_key')}#{ref.get('ref_order')}"] = fields

    for ref in payload.get("refs", []):
        fields = repair_object_text_fields(ref, ref_fields)
        if fields:
            changed_refs[f"{ref.get('entry_key')}#{ref.get('ref_order')}"] = fields

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    append_unique_note(
        payload.setdefault("notes", []),
        "PG068 rerun repaired validation-blocking OCR line-break hyphen artifacts while preserving the prior section structure, entries, refs, and material locators.",
    )
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "complete"
    coverage["entries_status_reason"] = (
        "Analytic subject index entries are present; this rerun removed split-word OCR hyphen artifacts "
        "that previously caused validation to reject affected entries and cascade into missing entry_key refs."
    )
    coverage["evidence_files"] = [
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-584.txt"),
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-585.txt"),
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-586.txt"),
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-587.txt"),
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-588.txt"),
        str(ROOT / "teste/PG068/text/0d6539ae-7718-4bb1-a02c-51bb4be138a5-589.txt"),
    ]

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = {
        "volume_id": "PG068",
        "updated_at": payload["generated_at"],
        "current_focus": "Payload repaired after PG068 line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade",
            "Verified representative split words against OCR reader output for files 584-589",
            f"Merged OCR line-break hyphen artifacts in {len(changed_entries)} entries",
            f"Checked material refs; repaired {len(changed_refs)} ref text fields",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The missing-entry-key errors were a cascade from rejected entries, not absent refs.",
            "Page 590 remains excluded as the ORDO RERUM tail.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "payload": str(PAYLOAD_PATH),
                "entries_changed": len(changed_entries),
                "refs_changed": len(changed_refs),
                "changed_entry_keys": sorted(changed_entries),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
