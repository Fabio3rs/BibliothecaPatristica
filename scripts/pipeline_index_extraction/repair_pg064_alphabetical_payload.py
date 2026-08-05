# Usage: python scripts/pipeline_index_extraction/repair_pg064_alphabetical_payload.py
# Repairs PG064 alphabetical payload OCR line-break hyphen artifacts and one page-boundary split entry.
"""Repair PG064 alphabetical payload after import validation flags hyphen artifacts."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG064_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG064/todo.json"
VOLUME_ID = "PG064"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")
SORT_STRIP_RE = re.compile(r"[^0-9a-zα-ωἀ-῾]+")

ENTRY_TEXT_FIELDS = ("entry_raw", "lemma_raw", "lemma_display", "context_raw")
REF_TEXT_FIELDS = ("ref_raw",)
EVIDENCE_FILES = [
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-722.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-723.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-724.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-725.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-726.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-781.txt"),
    str(ROOT / "teste/PG064/text/6f20648f-fa9e-423c-88e4-4d58591fc22b-782.txt"),
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


def entry_by_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    for entry in payload.get("entries", []):
        if entry.get("entry_key") == key:
            return entry
    raise KeyError(key)


def repair_cutis_page_boundary_split(payload: dict[str, Any]) -> bool:
    left_key = "PG064_index_rerum_e0255"
    right_key = "PG064_index_rerum_e0256"
    left = entry_by_key(payload, left_key)
    right = entry_by_key(payload, right_key)
    if left.get("entry_raw") != "Cutis quare non qi-":
        return False
    if right.get("entry_raw") != "quit pilos abundant masculis, 130":
        return False

    merged = "Cutis quare non qiquit pilos abundant masculis, 130"
    for field in ("entry_raw", "lemma_raw", "lemma_display"):
        left[field] = merged
    left["lemma_norm"] = sort_norm(merged)
    left["lemma_sort"] = left["lemma_norm"]
    left["inferred_printed_page"] = 130
    left["confidence"] = min(float(left.get("confidence") or 0.85), 0.85)
    left.setdefault("raw_json", {})["pg064_rerun_page_boundary_hyphen_repair"] = {
        "merged_entry_key": right_key,
        "reason": (
            "OCR file 723 ends with 'Cutis quare non qi-' and file 724 starts "
            "'quit pilos abundant masculis, 130'; the split word was merged while preserving OCR letters."
        ),
        "evidence_files": EVIDENCE_FILES[1:3],
    }

    for ref in payload.get("refs", []):
        if ref.get("entry_key") == right_key:
            ref["entry_key"] = left_key
            ref["ref_order"] = 1
            ref.setdefault("raw_json", {})["pg064_rerun_page_boundary_hyphen_repair"] = {
                "previous_entry_key": right_key,
                "reason": "Reference belongs to the merged Cutis entry split across files 723-724.",
            }

    payload["entries"] = [entry for entry in payload.get("entries", []) if entry.get("entry_key") != right_key]
    return True


def repair_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "refs_changed": 0, "page_boundary_entries_merged": 0}
    if repair_cutis_page_boundary_split(payload):
        counts["page_boundary_entries_merged"] = 1

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
            entry.setdefault("raw_json", {})["pg064_rerun_linebreak_hyphen_repaired"] = {
                "reason": "Merged OCR line-break hyphen artifacts after checking PG064 index OCR reader output.",
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
            ref.setdefault("raw_json", {})["pg064_rerun_linebreak_hyphen_repaired"] = {
                "reason": "Merged OCR line-break hyphen artifacts in material reference text fields.",
            }
            counts["refs_changed"] += 1
    return counts


def update_payload_metadata(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG064 INDEX RERUM and ORDO RERUM entries were extracted from OCR files 722-728 "
        "and 781-782; this rerun repairs validation-blocking OCR line-break hyphen artifacts."
    )
    coverage["evidence_files"] = EVIDENCE_FILES
    coverage["pg064_rerun_repair_counts"] = counts

    notes = payload.setdefault("notes", [])
    note = (
        "PG064 rerun repaired OCR line-break hyphen artifacts in entry text fields, merged the "
        "Cutis page-boundary split across files 723-724, and preserved existing material locators."
    )
    if note not in notes:
        notes.append(note)


def update_todo(counts: dict[str, int]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG064 payload repaired after validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
            "Inspected OCR reader output for INDEX RERUM files 722-728 and ORDO RERUM files 781-782",
            "Merged the Cutis page-boundary split across files 723-724",
            f"Changed {counts['entries_changed']} entries and {counts['refs_changed']} refs",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Repairs use the same letter-hyphen-whitespace-letter pattern enforced by the importer.",
            "The Cutis repair preserves the OCR letters as 'qiquit' rather than editorially emending the word.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    counts = repair_text_fields(payload)
    update_payload_metadata(payload, counts)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    update_todo(counts)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
