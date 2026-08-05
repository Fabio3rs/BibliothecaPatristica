#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg034_hyphen_artifacts.py
# Repairs PG034 alphabetical payload OCR line-break hyphen artifacts and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG034_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG034/todo.json"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG034/text"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def normalize_payload_text(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    for entry in payload.get("entries", []):
        changed_fields: list[str] = []
        for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] += 1
                changed_fields.append(field)
        if changed_fields:
            entry.setdefault("raw_json", {})["pg034_rerun_linebreak_hyphen_repaired"] = {
                "fields": changed_fields,
                "reason": "Validation-blocking OCR line-break hyphenation was merged by removing the hyphen before the continued word.",
            }

    for ref in payload.get("refs", []):
        changed_fields = []
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] += 1
                changed_fields.append(field)
        if changed_fields:
            ref.setdefault("raw_json", {})["pg034_rerun_linebreak_hyphen_repaired"] = {
                "fields": changed_fields,
                "reason": "Validation-blocking OCR line-break hyphenation was merged by removing the hyphen before the continued word.",
            }
    return dict(changed)


def assert_no_validator_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", [])):
        for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
            value = entry.get(field)
            if isinstance(value, str) and LINEBREAK_HYPHEN_RE.search(value):
                residual.append(f"entries[{idx}].{field}")
    for idx, ref in enumerate(payload.get("refs", [])):
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            value = ref.get(field)
            if isinstance(value, str) and LINEBREAK_HYPHEN_RE.search(value):
                residual.append(f"refs[{idx}].{field}")
    if residual:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual[:50]}")


def update_notes(payload: dict[str, Any], changed: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG034 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload; "
        "OCR file suffixes, printed pages, and cited references remain separate."
    )
    if volume_note not in payload["volume"]["notes"]:
        payload["volume"]["notes"].append(volume_note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation",
            "created_at": now_iso(),
            "message": "Fixed PG034 import failure caused by validation-blocking OCR line-break hyphen artifacts in entries.",
            "changed_field_counts": changed,
            "ocr_spot_checks": [
                str(SOURCE_ROOT / "b9be3dcc-66d9-4b57-8661-12917ac7dbcd-655.txt")
            ],
        }
    )


def update_todo(changed: dict[str, int]) -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": "PG034",
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG034 line-break hyphen import failure.",
            "completed": [
                "Read output-format and prompt-contract instructions",
                "Reviewed import validation rule for OCR line-break hyphen artifacts",
                "Spot-checked cleaned OCR reader output for the PG034 index window",
                "Merged validation-blocking OCR line-break hyphen artifacts in entry text fields",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {changed}",
                "Existing refs were preserved; no refs contained validator-style hyphen artifacts.",
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
    changed = normalize_payload_text(payload)
    assert_no_validator_hyphen_artifacts(payload)
    update_notes(payload, changed)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo(changed)
    print(json.dumps({"status": "ok", "changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
