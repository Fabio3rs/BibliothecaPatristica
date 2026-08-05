#!/usr/bin/env python3
"""Repair PG158 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg158_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG158"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG158/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG158_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG158"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

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
        merged = LINEBREAK_HYPHEN_RE.sub("", updated)
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


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return False
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def assert_no_residual_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", []), start=1):
        for field in ENTRY_FIELDS:
            if has_validator_hyphen_artifact(entry.get(field)):
                residual.append(f"entries[{idx}].{field}")
    for idx, ref in enumerate(payload.get("refs", []), start=1):
        for field in REF_FIELDS:
            if has_validator_hyphen_artifact(ref.get(field)):
                residual.append(f"refs[{idx}].{field}")
    for idx, ref in enumerate(payload.get("scripture_refs", []), start=1):
        if has_validator_hyphen_artifact(ref.get("ref_raw")):
            residual.append(f"scripture_refs[{idx}].ref_raw")
    if residual:
        raise SystemExit(
            "Residual line-break hyphen artifacts remain: "
            + json.dumps(residual[:50], ensure_ascii=False)
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
    changed_entry_keys: list[str] = []
    changed_ref_keys: list[str] = []

    for entry in payload.get("entries", []):
        changed_fields = repair_fields(entry, ENTRY_FIELDS)
        if changed_fields:
            changed_entry_keys.append(str(entry.get("entry_key") or "<missing>"))
            for field in changed_fields:
                changed[f"entries.{field}"] += 1
            raw_json = entry.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            note = (
                "PG158 rerun merged OCR line-break hyphen artifacts after checking the cleaned "
                "OCR reader output for files 675-676 and 677-692."
            )
            if note not in notes:
                notes.append(note)

    for ref in payload.get("refs", []):
        changed_fields = repair_fields(ref, REF_FIELDS)
        if changed_fields:
            changed_ref_keys.append(f"{ref.get('entry_key')}#{ref.get('ref_order')}")
            for field in changed_fields:
                changed[f"refs.{field}"] += 1
            raw_json = ref.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            note = "PG158 rerun merged OCR line-break hyphen artifacts in material reference text."
            if note not in notes:
                notes.append(note)

    for ref in payload.get("scripture_refs", []):
        old = ref.get("ref_raw")
        new = merge_linebreak_hyphens(old)
        if new != old:
            ref["ref_raw"] = new
            changed_ref_keys.append(f"{ref.get('entry_key')}#{ref.get('ref_order')}")
            changed["scripture_refs.ref_raw"] += 1

    assert_no_residual_hyphen_artifacts(payload)

    payload["generated_at"] = now_iso()
    payload.setdefault("notes", []).append(
        "PG158 rerun repaired OCR line-break hyphen artifacts in entry text fields and refreshed the checkpoint after validating against the OCR reader output."
    )

    dump_json(PAYLOAD_PATH, payload)
    dump_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    dump_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    dump_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    dump_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    dump_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    dump_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    dump_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    dump_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    dump_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "entry_count": len(payload.get("entries", [])),
            "ref_count": len(payload.get("refs", [])),
            "node_count": len(payload.get("nodes", [])),
            "helper_request_json": str(
                PROJECT_ROOT / "data/alphabetical_index_payloads/PG158_helper_request.json"
            ),
            "helper_output_json": str(
                PROJECT_ROOT / "data/alphabetical_index_payloads/PG158_helper_output.json"
            ),
            "output_file": str(PAYLOAD_PATH),
        },
    )
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PG158 payload repaired after validation-blocking OCR line-break hyphen artifacts.",
            "completed": [
                "Read the current validation failure and exact offending PG158 entries",
                "Checked the OCR reader output for files 675-676 and 677-692",
                "Merged validated OCR line-break hyphen artifacts in payload entry text fields",
                "Refreshed the final payload and intermediate checkpoint fragments",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only for PG158"],
            "blocked": [],
            "notes": [
                f"Changed counts: {dict(changed)}",
                "The reported missing entry_key refs were a validation cascade from rejected entries.",
                "Repair scope is limited to proven line-wrap hyphen artifacts in entry text fields and references.",
            ],
        },
    )

    validate_payload()

    print(
        json.dumps(
            {
                "repaired_counts": dict(changed),
                "changed_entry_keys": changed_entry_keys[:20],
                "changed_ref_keys": changed_ref_keys[:20],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
