# Usage: python scripts/pipeline_index_extraction/repair_pg012_alphabetical_payload.py
# Repairs PG012 alphabetical payload entries split by OCR line-break hyphenation.
"""Repair PG012 split entries and renumber dependent entry keys."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG012"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG012_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG012"
ENTRIES_PATH = INTERMEDIATE_DIR / "entries.json"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

MERGE_SPECS = [
    ("PG012:entry:01:0051", "PG012:entry:01:0052", "PG012 OCR file 845: AEgy- + ptus"),
    ("PG012:entry:01:0092", "PG012:entry:01:0093", "PG012 OCR files 845-846: peccave- + rint"),
    ("PG012:entry:01:0783", "PG012:entry:01:0784", "PG012 OCR file 850: re- + gionem"),
    ("PG012:entry:01:0857", "PG012:entry:01:0858", "PG012 OCR files 850-851: cur- + poribus"),
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")
NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def merge_hyphenated(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if TRAILING_WORD_HYPHEN_RE.search(left):
        return TRAILING_WORD_HYPHEN_RE.sub(r"\1", left) + right
    return f"{left} {right}".strip()


def lemma_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.casefold().replace("æ", "ae").replace("Æ", "ae")
    text = NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def repair_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    by_key = {entry["entry_key"]: idx for idx, entry in enumerate(entries)}
    removed: set[str] = set()
    merge_notes: list[dict[str, Any]] = []

    for left_key, right_key, evidence in MERGE_SPECS:
        if left_key not in by_key or right_key not in by_key:
            continue
        left = entries[by_key[left_key]]
        right = entries[by_key[right_key]]
        if not TRAILING_WORD_HYPHEN_RE.search(str(left.get("entry_raw") or "").rstrip()):
            continue
        merged_raw = merge_hyphenated(str(left.get("entry_raw") or ""), str(right.get("entry_raw") or ""))

        for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_sort"):
            if left.get(field) is not None:
                left[field] = merged_raw
        left["lemma_norm"] = lemma_norm(merged_raw)
        left["context_raw"] = None
        left["inferred_printed_page"] = left.get("inferred_printed_page") or right.get("inferred_printed_page")
        left["target_file_best"] = left.get("target_file_best") or right.get("target_file_best")
        left["confidence"] = min(float(left.get("confidence") or 0.7), float(right.get("confidence") or 0.7), 0.88)

        raw_json = left.setdefault("raw_json", {})
        raw_json["pg012_rerun_linebreak_hyphen_repaired"] = True
        raw_json["pg012_rerun_merged_from_entry_key"] = right_key
        raw_json["pg012_rerun_merge_evidence"] = evidence
        raw_json["query_names"] = [merged_raw]
        raw_json["merged_continuation_source_file"] = right.get("raw_json", {}).get("source_file")

        removed.add(right_key)
        merge_notes.append(
            {
                "kept_entry_key": left_key,
                "removed_entry_key": right_key,
                "entry_raw": merged_raw,
                "evidence": evidence,
            }
        )

    repaired = [entry for entry in entries if entry["entry_key"] not in removed]
    key_map: dict[str, str] = {}
    for order, entry in enumerate(repaired, start=1):
        old_key = entry["entry_key"]
        new_key = f"PG012:entry:01:{order:04d}"
        key_map[old_key] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = order

    for old_key in removed:
        key_map[old_key] = ""

    return repaired, key_map, {"merged": merge_notes, "removed_entry_count": len(removed)}


def remap_refs(refs: list[dict[str, Any]], key_map: dict[str, str]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for ref in refs:
        new_key = key_map.get(ref["entry_key"], ref["entry_key"])
        if not new_key:
            continue
        ref["entry_key"] = new_key
        sig = (new_key, int(ref.get("ref_order") or 0), str(ref.get("ref_raw")))
        if sig in seen:
            continue
        seen.add(sig)
        kept.append(ref)
    return kept


def update_payload(payload: dict[str, Any], repair_info: dict[str, Any]) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG012 INDEX ANALYTICUS and ORDO RERUM entries were reused from the verified checkpoint; "
        "this rerun repaired four OCR line-break hyphen splits against files 845, 846, 850, and 851."
    )
    coverage["evidence_files"] = [
        str(ROOT / "teste/PG012/text/b1b3903f-d7e4-4e85-b584-58b718e0bdf8-845.txt"),
        str(ROOT / "teste/PG012/text/b1b3903f-d7e4-4e85-b584-58b718e0bdf8-846.txt"),
        str(ROOT / "teste/PG012/text/b1b3903f-d7e4-4e85-b584-58b718e0bdf8-850.txt"),
        str(ROOT / "teste/PG012/text/b1b3903f-d7e4-4e85-b584-58b718e0bdf8-851.txt"),
    ]
    coverage["pg012_rerun_repair"] = repair_info
    notes = payload.setdefault("notes", [])
    note = (
        "PG012 rerun repaired the validation-blocking OCR line-break hyphen artifacts by merging "
        "the verified continuation fragments and renumbering entry keys consistently."
    )
    if note not in notes:
        notes.append(note)


def update_todo(repair_info: dict[str, Any]) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG012 payload repaired after line-break hyphen validation failure; ready for import validation.",
        "completed": [
            "Read prior validation failure for entries 51, 92, 783, and 857",
            "Verified the split words against OCR files 845, 846, 850, and 851",
            f"Merged {repair_info['removed_entry_count']} continuation fragments and renumbered entries",
            "Updated final payload and entries checkpoint",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only",
        ],
        "blocked": [],
        "notes": [
            "The repaired pairs are AEgyptus, peccaverint, regionem, and corporibus.",
            "Continuation entries were removed because each represented only the second half of a split word.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    entries, key_map, repair_info = repair_entries(payload["entries"])
    payload["entries"] = entries
    payload["refs"] = remap_refs(payload.get("refs", []), key_map)
    update_payload(payload, repair_info)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    if ENTRIES_PATH.exists():
        checkpoint_entries = json.loads(ENTRIES_PATH.read_text())
        repaired_entries, _, _ = repair_entries(checkpoint_entries)
        ENTRIES_PATH.write_text(json.dumps(repaired_entries, ensure_ascii=False, indent=2) + "\n")

    update_todo(repair_info)
    print(json.dumps(repair_info, ensure_ascii=False))


if __name__ == "__main__":
    main()
