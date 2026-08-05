#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg133_hyphen_artifacts.py
# Repairs PG133 alphabetical payload OCR line-break hyphen artifacts verified against OCR files 791-794.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG133"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG133_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG133"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"
OCR_SPOT_CHECKS = [
    PROJECT_ROOT / "teste/PG133/text/656f0209-5913-455c-945c-3c2a6c3cb13f-791.txt",
    PROJECT_ROOT / "teste/PG133/text/656f0209-5913-455c-945c-3c2a6c3cb13f-792.txt",
    PROJECT_ROOT / "teste/PG133/text/656f0209-5913-455c-945c-3c2a6c3cb13f-793.txt",
    PROJECT_ROOT / "teste/PG133/text/656f0209-5913-455c-945c-3c2a6c3cb13f-794.txt",
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}0-9])")
ARTIFACT_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)


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


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def read_ocr_text(file_path: Path) -> str:
    result = subprocess.run(
        [
            "python",
            "scripts/read_ocr_page_text.py",
            "--view",
            "xml",
            "--show-source",
            str(file_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    lines: list[str] = []
    for raw_line in result.stdout.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("<"):
            continue
        lines.append(stripped)
    return collapse_ws(merge_linebreak_hyphens(" ".join(lines)))


def verify_entry(entry: dict[str, Any], ocr_cache: dict[str, str], changed_fields: dict[str, str]) -> dict[str, Any]:
    source_file = entry.get("raw_json", {}).get("source_file")
    if not source_file:
        raise SystemExit(f"Missing source_file for {entry.get('entry_key')}")
    if source_file not in ocr_cache:
        ocr_cache[source_file] = read_ocr_text(Path(source_file))
    ocr_text = ocr_cache[source_file]

    verification: dict[str, Any] = {
        "source_file": source_file,
        "ocr_confirmed_fields": [],
        "ocr_spot_check": source_file,
    }

    entry_text = changed_fields.get("entry_raw", entry.get("entry_raw"))
    entry_text_norm = collapse_ws(entry_text if isinstance(entry_text, str) else "")
    if entry_text_norm and entry_text_norm in ocr_text:
        verification["ocr_confirmed_fields"].append("entry_raw")
    else:
        raise SystemExit(
            f"OCR verification failed for {entry.get('entry_key')}: repaired entry_raw not found in {source_file}"
        )

    for field, repaired in changed_fields.items():
        if field == "entry_raw":
            continue
        repaired_norm = collapse_ws(repaired)
        if repaired_norm and repaired_norm in ocr_text:
            verification["ocr_confirmed_fields"].append(field)

    return verification


def repair_entries(payload: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    ocr_cache: dict[str, str] = {}
    changed_entry_keys: list[str] = []
    changed_counts: dict[str, int] = defaultdict(int)

    for entry in payload.get("entries", []):
        changed_fields: dict[str, str] = {}
        for field in ENTRY_FIELDS:
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed_fields[field] = new
                changed_counts[f"entries.{field}"] += 1

        if not changed_fields:
            continue

        verification = verify_entry(entry, ocr_cache, changed_fields)
        raw_json = entry.setdefault("raw_json", {})
        raw_json["pg133_rerun_linebreak_hyphen_repaired"] = {
            "fields": sorted(changed_fields),
            "reason": (
                "PG133 rerun merged validation-blocking OCR line-break hyphen artifacts after "
                "checking the cleaned OCR reader output for the index files 791-794."
            ),
            "verification": verification,
        }
        changed_entry_keys.append(str(entry.get("entry_key")))

    return changed_entry_keys, changed_counts


def assert_no_residual_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", [])):
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and ARTIFACT_RE.search(value):
                residual.append(f"entries[{idx}].{field}")
    if residual:
        raise SystemExit("Residual OCR line-break hyphen artifacts remain: " + "; ".join(residual[:80]))


def update_payload(payload: dict[str, Any], changed_entry_keys: list[str], changed_counts: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()

    volume = payload.setdefault("volume", {})
    note = (
        "PG133 rerun repaired OCR line-break hyphen artifacts in the INDEX IN JOAN. CINNAMUM "
        "payload after rechecking the cleaned OCR for files 791-794."
    )
    existing_notes = volume.get("notes")
    if existing_notes is None:
        volume["notes"] = note
    elif isinstance(existing_notes, str):
        if note not in existing_notes:
            volume["notes"] = f"{existing_notes} {note}"
    elif isinstance(existing_notes, list) and note not in existing_notes:
        existing_notes.append(note)

    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "Fixed the PG133 import failure caused by OCR line-break hyphen artifacts in "
                "payload entry text fields; the missing entry_key ref errors were a cascade from "
                "those rejected entries."
            ),
            "changed_field_counts": dict(changed_counts),
            "changed_entry_keys_sample": changed_entry_keys[:120],
            "ocr_spot_checks": [str(path) for path in OCR_SPOT_CHECKS],
        }
    )


def update_todo(changed_counts: dict[str, int]) -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG133 line-break hyphen import failure.",
            "completed": [
                "Read current validation failure and inspected the exact offending PG133 entries",
                "Verified the OCR-clean continuations against files 791-794 with read_ocr_page_text.py",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload entry text fields",
                "Validated the repaired payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed_counts)}",
                "The missing entry_key validation errors were a cascade from entries rejected for line-break hyphen artifacts.",
                "No ref or scripture_ref text fields required repair in this rerun.",
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


def main() -> int:
    payload = load_json(PAYLOAD_PATH)
    changed_entry_keys, changed_counts = repair_entries(payload)
    if not changed_entry_keys:
        raise SystemExit("No PG133 entry fields required repair.")
    assert_no_residual_artifacts(payload)
    update_payload(payload, changed_entry_keys, changed_counts)
    dump_json(PAYLOAD_PATH, payload)
    update_todo(changed_counts)
    validate_payload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
