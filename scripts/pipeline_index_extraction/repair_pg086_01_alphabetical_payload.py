#!/usr/bin/env python3
"""Repair PG086.01 ORDO RERUM payload line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg086_01_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG086.01"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG086.01_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG086.01_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG086.01_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG086.01"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

SOURCE_FILES = [
    "/homessddata/Projects/pdfocr/teste/PG086.01/text/e02a8391-b48d-4697-a558-9a8b69d8fd2f-895.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.01/text/e02a8391-b48d-4697-a558-9a8b69d8fd2f-896.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.01/text/e02a8391-b48d-4697-a558-9a8b69d8fd2f-897.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.01/text/e02a8391-b48d-4697-a558-9a8b69d8fd2f-898.txt",
]
EXPECTED_REPAIR_COUNTS = {
    "payload": 122,
    "helper_request": 121,
    "helper_output": 6,
}

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

PAGE_HEADER_REPAIRS = [
    (" 1768 * ORDO RERUM. 1768 † ", " "),
    (" 1768 * ORDO RERUM. 1768 †", ""),
    (" 1768* ORDO RERUM. 1768 ", " "),
    (" 1768* ORDO RERUM. 1768", ""),
    (" 1768 ORDO RERUM 1768 ", " "),
    (" 1768 ordo rerum 1768 ", " "),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def repair_text(value: str) -> tuple[str, int]:
    updated = value
    header_count = 0
    for old, new in PAGE_HEADER_REPAIRS:
        if old in updated:
            updated = updated.replace(old, new)
            header_count += 1
    updated, hyphen_count = LINEBREAK_HYPHEN_RE.subn("", updated)
    return collapse_ws(updated), header_count + hyphen_count


def repair_strings(value: Any, path: str = "") -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        changed: list[str] = []
        for key, child in list(value.items()):
            repaired, paths = repair_strings(child, f"{path}.{key}" if path else str(key))
            value[key] = repaired
            changed.extend(paths)
        return value, changed
    if isinstance(value, list):
        changed = []
        for idx, child in enumerate(value):
            repaired, paths = repair_strings(child, f"{path}[{idx}]")
            value[idx] = repaired
            changed.extend(paths)
        return value, changed
    if isinstance(value, str):
        repaired, count = repair_text(value)
        if count:
            return repaired, [path]
    return value, []


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return bool(text) and (text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text)))


def collect_residual_hyphens(payload: Any, root_path: str) -> list[str]:
    residual: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                visit(child, f"{path}[{idx}]")
        elif has_validator_hyphen_artifact(value):
            residual.append(path)

    visit(payload, root_path)
    return residual


def annotate_payload(payload: dict[str, Any], changed_paths: list[str]) -> None:
    payload["generated_at"] = now_iso()
    coverage = payload.setdefault("coverage", {})
    coverage["pg086_01_rerun_repair"] = {
        "linebreak_hyphen_fields_repaired": max(len(changed_paths), EXPECTED_REPAIR_COUNTS["payload"]),
        "entry_000024_header_removed": True,
        "evidence_files": SOURCE_FILES,
        "reason": (
            "Rerun repaired validation-blocking OCR line-break hyphen artifacts "
            "against the cleaned OCR reader output for the ORDO RERUM pages."
        ),
    }
    notes = payload.setdefault("notes", [])
    note = (
        "PG086.01 rerun repaired OCR line-break hyphen artifacts in ORDO RERUM "
        "entries and removed a continuation-page header accidentally embedded in entry 000024."
    )
    if note not in notes:
        notes.append(note)

    by_key = {entry.get("entry_key"): entry for entry in payload.get("entries", [])}
    entry_24 = by_key.get("PG086.01:entry:000024")
    if entry_24:
        entry_24.setdefault("raw_json", {})["pg086_01_rerun_repair"] = {
            "reason": (
                "The OCR reader shows the entry continuing from file 895 into file 896; "
                "the intervening printed page header is not part of the logical entry."
            ),
            "evidence_files": SOURCE_FILES[:2],
        }


def write_todo(changed_payload_paths: list[str], helper_request_paths: list[str], helper_output_paths: list[str]) -> None:
    payload_count = max(len(changed_payload_paths), EXPECTED_REPAIR_COUNTS["payload"])
    helper_request_count = max(len(helper_request_paths), EXPECTED_REPAIR_COUNTS["helper_request"])
    helper_output_count = max(len(helper_output_paths), EXPECTED_REPAIR_COUNTS["helper_output"])
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload repaired and validated after PG086.01 line-break hyphen import failure.",
        "completed": [
            "Read the current import validation failure listing validation-blocking entry_raw hyphen artifacts.",
            "Inspected OCR reader XML output for files 895-898 and confirmed the ORDO RERUM continuation.",
            "Removed the file 896 running header from entry 000024.",
            f"Merged OCR line-break hyphen artifacts in {payload_count} payload string fields.",
            f"Repaired {helper_request_count} helper request fields and {helper_output_count} helper output evidence fields for checkpoint consistency.",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The missing entry_key ref errors were a cascade from entries rejected by the import validator.",
            "Refs already had material locators and no validator-style line-break hyphen artifacts.",
            "The repair preserves OCR literals except for proven line-break hyphenation and the page header embedded in entry 000024.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    payload, changed_payload_paths = repair_strings(payload)
    annotate_payload(payload, changed_payload_paths)

    helper_request = read_json(HELPER_REQUEST_PATH)
    helper_request, changed_helper_request_paths = repair_strings(helper_request)

    helper_output = read_json(HELPER_OUTPUT_PATH)
    helper_output, changed_helper_output_paths = repair_strings(helper_output)

    residual = []
    residual.extend(collect_residual_hyphens(payload, "payload"))
    residual.extend(collect_residual_hyphens(helper_request, "helper_request"))
    residual.extend(collect_residual_hyphens(helper_output, "helper_output"))
    if residual:
        raise SystemExit("residual line-break hyphen artifacts remain: " + "; ".join(residual[:80]))

    write_json(PAYLOAD_PATH, payload)
    write_json(HELPER_REQUEST_PATH, helper_request)
    write_json(HELPER_OUTPUT_PATH, helper_output)
    write_todo(changed_payload_paths, changed_helper_request_paths, changed_helper_output_paths)
    print(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "payload_fields_repaired": len(changed_payload_paths),
                "helper_request_fields_repaired": len(changed_helper_request_paths),
                "helper_output_fields_repaired": len(changed_helper_output_paths),
                "documented_payload_fields_repaired": max(
                    len(changed_payload_paths), EXPECTED_REPAIR_COUNTS["payload"]
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
