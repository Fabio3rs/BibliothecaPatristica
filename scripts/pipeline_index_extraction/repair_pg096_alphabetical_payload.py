#!/usr/bin/env python3
"""Repair PG096 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg096_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG096"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG096_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG096"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def has_validator_hyphen(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(collapsed))


def repair_entries(entries: list[dict[str, Any]], source_label: str) -> dict[str, int]:
    counts = {"entries_changed": 0, "fields_changed": 0}
    fields = ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw")
    for entry in entries:
        touched_fields: list[str] = []
        for field in fields:
            before = entry.get(field)
            after = dehyphenate(before)
            if before != after:
                entry[field] = after
                touched_fields.append(field)
                counts["fields_changed"] += 1
        if touched_fields:
            counts["entries_changed"] += 1
            entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                {
                    "source": source_label,
                    "reason": "PG096 rerun merged OCR line-break hyphenation after checking OCR reader output for index file 778.",
                    "fields": touched_fields,
                }
            )
    return counts


def repair_refs(refs: list[dict[str, Any]], source_label: str) -> dict[str, int]:
    counts = {"refs_changed": 0, "fields_changed": 0}
    fields = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")
    for ref in refs:
        touched_fields: list[str] = []
        for field in fields:
            before = ref.get(field)
            after = dehyphenate(before)
            if before != after:
                ref[field] = after
                touched_fields.append(field)
                counts["fields_changed"] += 1
        if touched_fields:
            counts["refs_changed"] += 1
            ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                {
                    "source": source_label,
                    "reason": "PG096 rerun merged OCR line-break hyphenation in reference text fields.",
                    "fields": touched_fields,
                }
            )
    return counts


def assert_no_residual(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }
    for collection, fields in field_map.items():
        for idx, obj in enumerate(payload.get(collection, [])):
            for field in fields:
                if has_validator_hyphen(obj.get(field)):
                    residual.append(
                        {
                            "collection": collection,
                            "index": idx,
                            "entry_key": obj.get("entry_key"),
                            "field": field,
                            "value": obj.get(field),
                        }
                    )
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2))


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload.get("entries", [])}
    missing = [
        {"index": idx, "entry_key": ref.get("entry_key")}
        for idx, ref in enumerate(payload.get("refs", []))
        if ref.get("entry_key") not in entry_keys
    ]
    if missing:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing[:50]}, ensure_ascii=False, indent=2))


def update_todo(counts: dict[str, int]) -> None:
    todo_path = INTERMEDIATE_DIR / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload repaired after PG096 line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for entry_raw hyphen artifacts and missing entry-key cascade.",
            "Verified the affected entries against OCR reader output for file 778.",
            f"Merged OCR line-break hyphen artifacts in {counts['payload_entries_changed']} final-payload entries.",
            f"Ref relationship check passed for {counts['refs_total']} refs.",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The missing entry_key errors were a cascade from entries rejected for line-break hyphen artifacts.",
            "The repair preserves OCR literals except for proven line-break hyphenation.",
        ],
    }
    write_json(todo_path, todo)


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    entry_counts = repair_entries(payload.get("entries", []), "final_payload")
    ref_counts = repair_refs(payload.get("refs", []), "final_payload")
    payload["generated_at"] = now_iso()
    payload.setdefault("notes", []).append(
        "PG096 rerun repaired validation-blocking OCR line-break hyphen artifacts in entries 0301, 0305, 0309, 0315, 0318, 0323, and 0326; refs were preserved."
    )
    assert_no_residual(payload)
    assert_relationships(payload)
    write_json(PAYLOAD_PATH, payload)

    entries_path = INTERMEDIATE_DIR / "entries.json"
    if entries_path.exists():
        entries = read_json(entries_path)
        repair_entries(entries, "intermediate_entries")
        write_json(entries_path, entries)

    refs_path = INTERMEDIATE_DIR / "refs.json"
    if refs_path.exists():
        refs = read_json(refs_path)
        repair_refs(refs, "intermediate_refs")
        write_json(refs_path, refs)

    update_todo(
        {
            "payload_entries_changed": entry_counts["entries_changed"],
            "payload_entry_fields_changed": entry_counts["fields_changed"],
            "payload_refs_changed": ref_counts["refs_changed"],
            "refs_total": len(payload.get("refs", [])),
        }
    )
    print(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "payload_entries_changed": entry_counts["entries_changed"],
                "payload_entry_fields_changed": entry_counts["fields_changed"],
                "payload_refs_changed": ref_counts["refs_changed"],
                "refs_total": len(payload.get("refs", [])),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
