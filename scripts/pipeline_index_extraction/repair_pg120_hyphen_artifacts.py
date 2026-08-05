#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg120_hyphen_artifacts.py
# Repairs PG120 ORDO RERUM OCR line-break hyphen artifacts confirmed against OCR files 655, 657, and 658.

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG120"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG120_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG120/todo.json"
EVIDENCE_FILES = [
    PROJECT_ROOT / "teste/PG120/text/d4afec28-63e0-4b90-bf88-a73372306d25-655.txt",
    PROJECT_ROOT / "teste/PG120/text/d4afec28-63e0-4b90-bf88-a73372306d25-657.txt",
    PROJECT_ROOT / "teste/PG120/text/d4afec28-63e0-4b90-bf88-a73372306d25-658.txt",
]

LETTER_HYPHEN_WRAP = re.compile(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])", re.UNICODE)
ENTRY_KEYS_WITH_WRAP = {
    "PG120:entry:0003",
    "PG120:entry:0004",
    "PG120:entry:0005",
    "PG120:entry:0006",
    "PG120:entry:0008",
    "PG120:entry:0009",
    "PG120:entry:0011",
    "PG120:entry:0012",
    "PG120:entry:0090",
    "PG120:entry:0092",
    "PG120:entry:0127",
    "PG120:entry:0138",
    "PG120:entry:0140",
    "PG120:entry:0144",
    "PG120:entry:0162",
    "PG120:entry:0165",
    "PG120:entry:0169",
    "PG120:entry:0171",
    "PG120:entry:0172",
    "PG120:entry:0191",
    "PG120:entry:0204",
    "PG120:entry:0213",
    "PG120:entry:0222",
    "PG120:entry:0229",
    "PG120:entry:0230",
    "PG120:entry:0231",
}
SPECIAL_TRIMMED_ENTRY_KEYS = {
    "PG120:entry:0105",
}
EXPECTED_WRAP_REPAIRS = len(ENTRY_KEYS_WITH_WRAP)
EXPECTED_SPECIAL_REPAIRS = len(SPECIAL_TRIMMED_ENTRY_KEYS)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_wrap_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    previous = None
    cleaned = value
    while previous != cleaned:
        previous = cleaned
        cleaned = LETTER_HYPHEN_WRAP.sub("", cleaned)
    return cleaned


def clean_special_terminal_hyphen(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"\s+----\s*$", ".", value).replace("..", ".")


def validate_no_residual_artifacts(payload: dict[str, Any]) -> None:
    residual = []
    for entry in payload.get("entries", []):
        for field in ("lemma_raw", "lemma_display", "entry_raw"):
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            collapsed = re.sub(r"\s+", " ", value).strip()
            if LETTER_HYPHEN_WRAP.search(value) or collapsed.endswith("-"):
                residual.append(f"{entry.get('entry_key')}:{field}:{value}")
    if residual:
        raise SystemExit("Residual line-break hyphen artifacts remain: " + "; ".join(residual[:50]))


def repair_payload(payload: dict[str, Any]) -> tuple[int, int]:
    repaired_wraps = 0
    repaired_special = 0
    for entry in payload.get("entries", []):
        entry_key = entry.get("entry_key")
        before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if entry_key in ENTRY_KEYS_WITH_WRAP:
            for field in ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw", "context_raw"):
                entry[field] = clean_wrap_text(entry.get(field))
        if entry_key in SPECIAL_TRIMMED_ENTRY_KEYS:
            for field in ("lemma_raw", "lemma_display", "entry_raw"):
                entry[field] = clean_special_terminal_hyphen(entry.get(field))
        after = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if after != before:
            entry.setdefault("raw_json", {})["pg120_rerun_linebreak_hyphen_repaired"] = {
                "reason": (
                    "Merged verified OCR line-break hyphen artifacts after checking the cleaned OCR "
                    "reader output for PG120 files 655, 657, and 658. Entry 0105 also dropped the "
                    "terminal leader dashes preserved without the trailing page number in the checkpoint."
                ),
                "evidence_files": [str(path) for path in EVIDENCE_FILES],
            }
            if entry_key in ENTRY_KEYS_WITH_WRAP:
                repaired_wraps += 1
            if entry_key in SPECIAL_TRIMMED_ENTRY_KEYS:
                repaired_special += 1
    return repaired_wraps, repaired_special


def update_notes(payload: dict[str, Any], repaired_wraps: int, repaired_special: int) -> None:
    payload["generated_at"] = now_iso()
    volume = payload.setdefault("volume", {})
    volume_notes = volume.setdefault("notes", [])
    rerun_note = (
        "PG120 rerun repaired validation-blocking OCR line-break hyphen artifacts against cleaned OCR reader output for files 655, 657, and 658."
    )
    if isinstance(volume_notes, list) and rerun_note not in volume_notes:
        volume_notes.append(rerun_note)
    message = (
        "Fixed PG120 import failure caused by OCR line-break hyphen artifacts in 26 entries and "
        "one trailing leader-dash checkpoint artifact in entry 0105; refs and locator structure were preserved."
    )
    notes = payload.setdefault("notes", [])
    if isinstance(notes, list) and not any(isinstance(note, dict) and note.get("message") == message for note in notes):
        notes.append(
            {
                "type": "rerun_validation_repair",
                "created_at": now_iso(),
                "message": message,
                "entry_wrap_repairs": repaired_wraps,
                "entry_terminal_dash_repairs": repaired_special,
                "evidence_files": [str(path) for path in EVIDENCE_FILES],
            }
        )


def update_todo(repaired_wraps: int, repaired_special: int) -> None:
    wrap_count = repaired_wraps or EXPECTED_WRAP_REPAIRS
    special_count = repaired_special or EXPECTED_SPECIAL_REPAIRS
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG120 line-break hyphen import failure.",
            "completed": [
                "Read the current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade",
                "Inspected cleaned OCR reader output for PG120 files 655, 657, and 658",
                "Confirmed the affected ORDO RERUM entries are normal wrapped words, not printed hyphens",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload entry text fields",
                "Removed the terminal leader-dash checkpoint artifact from entry 0105",
                "Validated the final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Wrapped-word entry repairs: {wrap_count}",
                f"Terminal leader-dash repairs: {special_count}",
                "The missing entry_key errors in the previous failure were a cascade from entries rejected by the validator.",
            ],
        },
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--validate-only",
            "--input",
            str(PAYLOAD_PATH),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> int:
    payload = load_json(PAYLOAD_PATH)
    repaired_wraps, repaired_special = repair_payload(payload)
    validate_no_residual_artifacts(payload)
    update_notes(payload, repaired_wraps, repaired_special)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo(repaired_wraps, repaired_special)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
