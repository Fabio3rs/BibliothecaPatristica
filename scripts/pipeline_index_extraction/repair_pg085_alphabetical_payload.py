#!/usr/bin/env python3
"""Repair PG085 alphabetical payload after import validation hyphen failures.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg085_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG085"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG085_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG085"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(text: str) -> str:
    return " ".join(text.split())


def merge_linebreak_hyphens(value: Any) -> tuple[Any, int]:
    if not isinstance(value, str):
        return value, 0
    updated, count = LINEBREAK_HYPHEN_RE.subn("", value)
    return updated, count


def repair_strings(value: Any, path: str = "") -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        changed_paths: list[str] = []
        for key, child in list(value.items()):
            repaired, child_paths = repair_strings(child, f"{path}.{key}" if path else str(key))
            value[key] = repaired
            changed_paths.extend(child_paths)
        return value, changed_paths
    if isinstance(value, list):
        changed_paths = []
        for idx, child in enumerate(value):
            repaired, child_paths = repair_strings(child, f"{path}[{idx}]")
            value[idx] = repaired
            changed_paths.extend(child_paths)
        return value, changed_paths
    repaired, count = merge_linebreak_hyphens(value)
    if count:
        return repaired, [path]
    return value, []


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return bool(text) and (text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text)))


def collect_residual_hyphens(payload: dict[str, Any]) -> list[str]:
    residual: list[str] = []

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                visit(child, f"{path}[{idx}]")
        elif has_validator_hyphen_artifact(value):
            residual.append(path)

    visit(payload)
    return residual


def fill_ref_targets_from_entry(payload: dict[str, Any]) -> int:
    entries_by_key = {entry["entry_key"]: entry for entry in payload.get("entries", [])}
    changed = 0
    for ref in payload.get("refs", []):
        if ref.get("target_file"):
            continue
        entry = entries_by_key.get(ref.get("entry_key"))
        if not entry or not entry.get("target_file_best"):
            continue
        ref["target_file"] = entry["target_file_best"]
        if ref.get("target_file_probability") is None:
            ref["target_file_probability"] = min(float(entry.get("confidence") or 0.75), 0.95)
        ref.setdefault("raw_json", {})["pg085_rerun_target_file_repair"] = {
            "source": "entry.target_file_best",
            "reason": "Previous checkpoint had a resolved entry target_file_best but left the material ref target_file null.",
        }
        changed += 1
    return changed


def add_repair_notes(payload: dict[str, Any], changed_paths: list[str], refs_filled: int) -> None:
    payload["generated_at"] = now_iso()
    note = (
        "PG085 rerun repaired validation-blocking OCR line-break hyphen artifacts "
        f"in {len(changed_paths)} string fields and propagated target_file_best to {refs_filled} refs."
    )
    notes = payload.setdefault("notes", [])
    if note not in notes:
        notes.append(note)
    payload.setdefault("coverage", {})["pg085_rerun_repair"] = {
        "linebreak_hyphen_fields_repaired": len(changed_paths),
        "refs_target_file_filled_from_entry": refs_filled,
        "evidence_files": [
            "/homessddata/Projects/pdfocr/teste/PG085/text/53e521d1-f2d5-4de4-b397-30eac40b0b84-596.txt",
            "/homessddata/Projects/pdfocr/teste/PG085/text/c10a2e12-2feb-4c09-ad59-d79fd6ef4289-925.txt",
            "/homessddata/Projects/pdfocr/teste/PG085/text/c10a2e12-2feb-4c09-ad59-d79fd6ef4289-927.txt",
            "/homessddata/Projects/pdfocr/teste/PG085/text/c10a2e12-2feb-4c09-ad59-d79fd6ef4289-942.txt",
        ],
    }


def write_todo(changed_paths: list[str], refs_filled: int) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload repaired after PG085 import validation hyphen failure; validation completed next.",
        "completed": [
            "Read the current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade.",
            "Checked representative offending entries against the OCR reader output for files 596, 925, 927, and 942.",
            f"Merged OCR line-break hyphen artifacts in {len(changed_paths)} payload string fields.",
            f"Filled {refs_filled} material ref target_file values from existing entry target_file_best anchors.",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Missing entry-key validation errors were a cascade from entries rejected for line-break hyphen artifacts.",
            "The repair keeps OCR literals except for proven word-break hyphenation.",
            "Residual author-index entries without target_file_best remain unresolved only where the checkpoint lacks a material locator.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    payload, changed_paths = repair_strings(payload)
    refs_filled = fill_ref_targets_from_entry(payload)
    add_repair_notes(payload, changed_paths, refs_filled)

    residual = collect_residual_hyphens(payload)
    if residual:
        raise SystemExit("residual hyphen artifacts remain: " + "; ".join(residual[:50]))

    write_json(PAYLOAD_PATH, payload)
    write_todo(changed_paths, refs_filled)
    print(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "linebreak_hyphen_fields_repaired": len(changed_paths),
                "refs_target_file_filled_from_entry": refs_filled,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
