#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg077_hyphen_artifacts.py
# Repairs PG077 alphabetical payload OCR line-break hyphen artifacts and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG077"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG077/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG077_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG077"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)
REF_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
EVIDENCE_FILES = [
    SOURCE_ROOT / "9dadd858-693b-48de-b612-299210f24efe-760.txt",
    SOURCE_ROOT / "9dadd858-693b-48de-b612-299210f24efe-761.txt",
    SOURCE_ROOT / "9dadd858-693b-48de-b612-299210f24efe-762.txt",
    SOURCE_ROOT / "9dadd858-693b-48de-b612-299210f24efe-763.txt",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def repair_fields(obj: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    changed_fields: list[str] = []
    for field in fields:
        old = obj.get(field)
        new = merge_linebreak_hyphens(old)
        if new != old:
            obj[field] = new
            changed_fields.append(field)
    return changed_fields


def repair_nested_strings(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, child in list(value.items()):
            repaired, child_changes = repair_nested_strings(child)
            value[key] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, list):
        changes = 0
        for idx, child in enumerate(value):
            repaired, child_changes = repair_nested_strings(child)
            value[idx] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, str):
        repaired = merge_linebreak_hyphens(value)
        return repaired, int(repaired != value)
    return value, 0


def repair_entries(entries: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_keys: list[str] = []
    for entry in entries:
        changed_fields = repair_fields(entry, ENTRY_FIELDS)
        raw_json = entry.get("raw_json")
        nested_changes = 0
        if isinstance(raw_json, dict):
            _, nested_changes = repair_nested_strings(raw_json)
            if nested_changes:
                changed["entries.raw_json_nested_strings"] += nested_changes
        if not changed_fields and not nested_changes:
            continue
        entry_key = str(entry.get("entry_key") or "<missing>")
        changed_keys.append(entry_key)
        for field in changed_fields:
            changed[f"entries.{field}"] += 1
        entry.setdefault("raw_json", {})["pg077_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "nested_raw_json_strings_changed": nested_changes,
            "reason": (
                "Validation-blocking OCR line-break hyphenation was merged after checking "
                "the cleaned OCR reader output for PG077 index files 760-763."
            ),
        }
    return changed_keys


def repair_refs(refs: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_refs: list[str] = []
    for ref in refs:
        changed_fields = repair_fields(ref, REF_FIELDS)
        if not changed_fields:
            continue
        ref_id = f"{ref.get('entry_key')}#{ref.get('ref_order')}"
        changed_refs.append(ref_id)
        for field in changed_fields:
            changed[f"refs.{field}"] += 1
        ref.setdefault("raw_json", {})["pg077_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "reason": "Validation-blocking OCR line-break hyphenation was merged in reference text fields.",
        }
    return changed_refs


def assert_no_validator_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []

    def scan(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                scan(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                scan(child, f"{path}[{idx}]")
        elif isinstance(value, str) and (
            LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)
        ):
            residual.append(path)

    scan(payload, "")
    if residual:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual[:50]}")


def update_payload_notes(payload: dict[str, Any], changed: dict[str, int], changed_entry_keys: list[str]) -> None:
    payload["generated_at"] = now_iso()
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG077 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload; "
        "OCR file suffixes, printed pages, and cited references remain separate."
    )
    if volume_note not in volume_notes:
        volume_notes.append(volume_note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "Fixed PG077 import failure caused by OCR line-break hyphen artifacts in entries; "
                "the reported missing entry_key refs were a validation cascade from rejected entries."
            ),
            "changed_field_counts": dict(changed),
            "changed_entry_keys": changed_entry_keys,
            "ocr_spot_checks": [str(path) for path in EVIDENCE_FILES],
        }
    )
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the visible INDEX ANALYTICUS blocks from OCR files 760-763. "
            "The non-alphabetical ORDO RERUM / INDEX RERUM material later in the tail was excluded. "
            "This rerun removed validation-blocking split-word hyphen artifacts while preserving prior material locators."
        ),
        "evidence_files": [str(path) for path in EVIDENCE_FILES],
    }


def update_intermediates(payload: dict[str, Any], changed: dict[str, int]) -> None:
    for name in ("entries", "refs", "coverage", "notes"):
        dump_json(INTERMEDIATE_DIR / f"{name}.json", payload[name])
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG077 line-break hyphen import failure.",
            "completed": [
                "Read alphabetical-index skill contract and output format",
                "Reviewed import validation rule for OCR line-break hyphen artifacts",
                "Inspected PG077 index OCR reader output for files 760-763",
                "Verified the reported entries against the cleaned OCR reader output",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload text fields",
                "Synchronized final payload and intermediate fragments",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed)}",
                "Refs pointed to existing checkpoint entry keys; missing-entry-key errors were caused by rejected hyphenated entries.",
                "No scripture_refs are present for PG077.",
            ],
        },
    )


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


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed: dict[str, int] = defaultdict(int)
    changed_entry_keys = repair_entries(payload.get("entries", []), changed)
    changed_refs = repair_refs(payload.get("refs", []), changed)
    assert_no_validator_hyphen_artifacts(payload)
    update_payload_notes(payload, changed, changed_entry_keys)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_intermediates(payload, changed)
    print(
        json.dumps(
            {
                "status": "ok",
                "changed_field_counts": dict(changed),
                "entries_changed": len(changed_entry_keys),
                "refs_changed": len(changed_refs),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
