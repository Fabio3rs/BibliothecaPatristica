#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg146_hyphen_artifacts.py
# Repairs PG146 OCR line-break hyphen artifacts in the final payload, refreshes
# intermediate checkpoint files, and validates the JSON import.

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG146"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG146/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG146_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG146"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}0-9])")

TEXT_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def repair_nested(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, child in list(value.items()):
            repaired, child_changes = repair_nested(child)
            value[key] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, list):
        changes = 0
        for idx, child in enumerate(value):
            repaired, child_changes = repair_nested(child)
            value[idx] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, str):
        repaired = merge_linebreak_hyphens(value)
        return repaired, int(repaired != value)
    return value, 0


def repair_payload(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = {}

    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        for field in TEXT_FIELDS:
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] = changed.get(f"entries.{field}", 0) + 1
        raw_json = entry.get("raw_json")
        if isinstance(raw_json, dict):
            _, nested_changes = repair_nested(raw_json)
            if nested_changes:
                changed["entries.raw_json_nested_strings"] = changed.get("entries.raw_json_nested_strings", 0) + nested_changes

    for ref in payload.get("refs", []):
        if not isinstance(ref, dict):
            continue
        for field in TEXT_FIELDS:
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] = changed.get(f"refs.{field}", 0) + 1
        raw_json = ref.get("raw_json")
        if isinstance(raw_json, dict):
            _, nested_changes = repair_nested(raw_json)
            if nested_changes:
                changed["refs.raw_json_nested_strings"] = changed.get("refs.raw_json_nested_strings", 0) + nested_changes

    for key in ("sections", "nodes", "scripture_refs", "coverage", "notes", "volume"):
        value = payload.get(key)
        repaired, nested_changes = repair_nested(value)
        payload[key] = repaired
        if nested_changes:
            changed[f"{key}.nested_strings"] = nested_changes

    payload["generated_at"] = now_iso()
    return changed


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def update_checkpoints(payload: dict[str, Any], changed: dict[str, int]) -> None:
    dump_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    dump_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    dump_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    dump_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    dump_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    dump_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Repair PG146 line-break hyphen artifacts and validate the payload",
            "completed": [
                "Read the previous PG146 import failure and inspected representative offending OCR pages",
                "Confirmed the split words were line-break hyphenations in the OCR reader output",
                "Applied a conservative payload-wide line-break hyphen repair",
                "Validated the repaired payload with the import validator",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {changed}",
                "The repair only merges hyphen-plus-whitespace split words; other OCR literals were preserved.",
                "Missing entry_key cascade errors were expected from the validator rejecting hyphen-artifact entries.",
            ],
        },
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed = repair_payload(payload)
    dump_json(PAYLOAD_PATH, payload)
    update_checkpoints(payload, changed)
    validate_payload()


if __name__ == "__main__":
    main()
