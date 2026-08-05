# Usage: python scripts/pipeline_index_extraction/repair_pg065_alphabetical_payload.py
# Repairs PG065 ORDO RERUM payload OCR line-break hyphen artifacts and refreshes rerun notes.
"""Repair PG065 alphabetical payload after import validation flags hyphen artifacts."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG065"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG065_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG065/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")
SORT_STRIP_RE = re.compile(r"[^0-9a-zα-ωἀ-῾]+")

ENTRY_TEXT_FIELDS = ("entry_raw", "lemma_raw", "lemma_display", "context_raw")
REF_TEXT_FIELDS = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")
EVIDENCE_FILES = [
    str(ROOT / "teste/PG065/text/3f03af40-2b05-4a31-89c4-f844d8c99474-640.txt"),
    str(ROOT / "teste/PG065/text/3f03af40-2b05-4a31-89c4-f844d8c99474-641.txt"),
    str(ROOT / "teste/PG065/text/3f03af40-2b05-4a31-89c4-f844d8c99474-642.txt"),
    str(ROOT / "teste/PG065/text/3f03af40-2b05-4a31-89c4-f844d8c99474-648.txt"),
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


def sort_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value.lower().replace("\xa0", " "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return SORT_STRIP_RE.sub(" ", text).strip() or None


def repair_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "refs_changed": 0}

    for entry in payload.get("entries", []):
        changed = False
        for field in ENTRY_TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed = True
        if changed:
            if entry.get("lemma_raw") is not None:
                entry["lemma_norm"] = sort_norm(entry.get("lemma_raw"))
                entry["lemma_sort"] = entry["lemma_norm"]
            entry.setdefault("raw_json", {})["pg065_rerun_linebreak_hyphen_repaired"] = {
                "reason": (
                    "Merged OCR line-break hyphen artifacts after checking cleaned OCR reader "
                    "output for PG065 ORDO RERUM and Vita S. Porphyrii section index pages."
                ),
                "evidence_files": EVIDENCE_FILES,
            }
            counts["entries_changed"] += 1

    for ref in payload.get("refs", []):
        changed = False
        for field in REF_TEXT_FIELDS:
            old = ref.get(field)
            new = dehyphenate(old)
            if new != old:
                ref[field] = new
                changed = True
        if changed:
            ref.setdefault("raw_json", {})["pg065_rerun_linebreak_hyphen_repaired"] = {
                "reason": "Merged OCR line-break hyphen artifacts in material reference text fields.",
            }
            counts["refs_changed"] += 1

    return counts


def assert_relationships(payload: dict[str, Any]) -> None:
    section_keys = {section["section_key"] for section in payload.get("sections", [])}
    entry_keys = {entry["entry_key"] for entry in payload.get("entries", [])}
    bad_entries = [entry["entry_key"] for entry in payload.get("entries", []) if entry.get("section_key") not in section_keys]
    bad_refs = [ref["entry_key"] for ref in payload.get("refs", []) if ref.get("entry_key") not in entry_keys]
    if bad_entries:
        raise SystemExit(f"entries reference missing sections: {bad_entries[:20]}")
    if bad_refs:
        raise SystemExit(f"refs reference missing entries: {bad_refs[:20]}")


def update_payload_metadata(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG065 contains the Vita S. Porphyrii section index and the closing ORDO RERUM contents "
        "table. This rerun preserves the checkpoint structure after OCR inspection and repairs "
        "validation-blocking line-break hyphen artifacts."
    )
    coverage["evidence_files"] = EVIDENCE_FILES
    coverage["pg065_rerun_repair_counts"] = counts

    notes = payload.setdefault("notes", [])
    note = (
        "PG065 rerun repaired OCR line-break hyphen artifacts in entry/ref text fields against "
        "the cleaned OCR reader output for files 640-648 and rechecked entry/ref relationships."
    )
    if note not in notes:
        notes.append(note)


def update_todo(counts: dict[str, int]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG065 payload repaired after validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
            "Inspected OCR reader output for files 640-648",
            f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries and {counts['refs_changed']} refs",
            "Rechecked section/entry/ref relationship keys before validation",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "PG065 tail is an ORDO RERUM contents table plus Vita S. Porphyrii section index, not a separate alphabetical index.",
            "Repairs use the same letter-hyphen-whitespace-letter pattern enforced by the importer.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    counts = repair_text_fields(payload)
    assert_relationships(payload)
    update_payload_metadata(payload, counts)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    update_todo(counts)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
