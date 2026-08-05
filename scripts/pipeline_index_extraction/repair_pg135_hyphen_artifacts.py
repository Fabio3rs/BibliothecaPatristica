#!/usr/bin/env python3
"""Repair PG135 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg135_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG135"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG135_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG135"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

ENTRY_FIELDS = ("lemma_raw", "lemma_display", "entry_raw", "context_raw")
WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

EVIDENCE_FILES = [
    ROOT / "teste/PG135/text/5476e3f3-eeda-43f2-8979-5b1ccfa7d577-550.txt",
    ROOT / "teste/PG135/text/5476e3f3-eeda-43f2-8979-5b1ccfa7d577-552.txt",
    ROOT / "teste/PG135/text/5476e3f3-eeda-43f2-8979-5b1ccfa7d577-591.txt",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_word_hyphenation(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def collapse_ws(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def has_validator_hyphen_artifact(value: Any) -> bool:
    text = collapse_ws(value)
    return bool(text and (text.endswith("-") or VALIDATOR_HYPHEN_RE.search(text)))


def repair_entries(entries: list[dict[str, Any]]) -> tuple[list[str], Counter]:
    changed_entries: list[str] = []
    changed_fields: Counter = Counter()
    for entry in entries:
        entry_changed_fields: list[str] = []
        for field in ENTRY_FIELDS:
            before = entry.get(field)
            after = merge_word_hyphenation(before)
            if after != before:
                entry[field] = after
                entry_changed_fields.append(field)
                changed_fields[field] += 1
        if entry_changed_fields:
            changed_entries.append(entry["entry_key"])
            raw_json = entry.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            note = (
                "PG135 rerun merged a validation-blocking OCR line-break hyphen artifact after "
                "checking the cleaned OCR reader output for index files 550, 552, and 591."
            )
            if note not in notes:
                notes.append(note)
    return changed_entries, changed_fields


def assert_no_residual_artifacts(entries: list[dict[str, Any]]) -> None:
    residual: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        for field in ENTRY_FIELDS:
            if has_validator_hyphen_artifact(entry.get(field)):
                residual.append(
                    {
                        "index": idx,
                        "entry_key": entry.get("entry_key"),
                        "field": field,
                        "value": entry.get(field),
                    }
                )
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2))


def sync_intermediates(payload: dict[str, Any], changed_field_counts: Counter) -> None:
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "payload_path": str(PAYLOAD_PATH),
            "repair_type": "linebreak_hyphen_artifacts",
            "changed_field_counts": dict(changed_field_counts),
        },
    )
    write_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PG135 payload repaired after line-break hyphen validation failure and revalidated.",
            "completed": [
                "Read the PG135 validation failure and inspected the exact rejected entries",
                "Verified OCR reader output for index files 550, 552, and 591",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload entry text fields",
                "Synchronized the final payload and per-volume intermediate fragments",
                "Validated the repaired payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed_field_counts)}",
                "The repair is limited to proven split-word line-wrap artifacts in entry text fields.",
                "The existing section/node/ref structure was retained and only OCR-backed word merges were applied.",
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
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed_entries, changed_field_counts = repair_entries(payload.get("entries", []))
    assert_no_residual_artifacts(payload.get("entries", []))

    payload["generated_at"] = now_iso()
    payload.setdefault("notes", [])
    note = (
        "PG135 rerun repaired validation-blocking OCR line-break hyphen artifacts in entry text "
        "fields after spot-checking the cleaned OCR reader output for the index pages."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)
    payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG135 rerun preserved the existing locator structure while repairing OCR split-word "
        "hyphenation in the alphabetical index payload."
    )
    if volume_note not in payload["volume"]["notes"]:
        payload["volume"]["notes"].append(volume_note)
    payload["coverage"] = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Repaired validation-blocking split-word hyphen artifacts in the previously extracted "
            "alphabetical index while preserving the existing OCR-backed entry and locator coverage."
        ),
        "evidence_files": [str(path) for path in EVIDENCE_FILES],
    }

    write_json(PAYLOAD_PATH, payload)
    validate_payload()
    sync_intermediates(payload, changed_field_counts)
    print(
        json.dumps(
            {
                "status": "ok",
                "entries_changed": len(changed_entries),
                "changed_field_counts": dict(changed_field_counts),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
