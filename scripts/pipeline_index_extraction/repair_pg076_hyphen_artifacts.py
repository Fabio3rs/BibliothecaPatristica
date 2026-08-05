#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg076_hyphen_artifacts.py
# Repairs PG076 alphabetical payload OCR line-break hyphen artifacts and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG076"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG076/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG076_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG076"
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
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-741.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-742.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-743.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-744.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-745.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-746.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-747.txt",
    SOURCE_ROOT / "e3a03f05-8c07-4114-94fd-66f407d38567-748.txt",
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


def repair_entries(entries: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_keys: list[str] = []
    for entry in entries:
        changed_fields = repair_fields(entry, ENTRY_FIELDS)
        if not changed_fields:
            continue
        entry_key = str(entry.get("entry_key") or "<missing>")
        changed_keys.append(entry_key)
        for field in changed_fields:
            changed[f"entries.{field}"] += 1
        entry.setdefault("raw_json", {})["pg076_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "reason": (
                "Validation-blocking OCR line-break hyphenation was merged after checking "
                "the cleaned OCR reader output for the PG076 index window."
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
        ref.setdefault("raw_json", {})["pg076_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "reason": "Validation-blocking OCR line-break hyphenation was merged in reference text fields.",
        }
    return changed_refs


def assert_no_validator_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", []), start=1):
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and (
                LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)
            ):
                residual.append(f"entries[{idx}].{field}")
    for idx, ref in enumerate(payload.get("refs", []), start=1):
        for field in REF_FIELDS:
            value = ref.get(field)
            if isinstance(value, str) and (
                LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)
            ):
                residual.append(f"refs[{idx}].{field}")
    if residual:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual[:50]}")


def update_payload_notes(payload: dict[str, Any], changed: dict[str, int], changed_entry_keys: list[str]) -> None:
    payload["generated_at"] = now_iso()
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG076 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload; "
        "OCR file suffixes, printed pages, and cited references remain separate."
    )
    if volume_note not in volume_notes:
        volume_notes.append(volume_note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "Fixed PG076 import failure caused by OCR line-break hyphen artifacts in entries; "
                "the reported missing entry_key refs were a validation cascade from rejected entries."
            ),
            "changed_field_counts": dict(changed),
            "changed_entry_keys": changed_entry_keys,
            "ocr_spot_checks": [str(EVIDENCE_FILES[2])],
        }
    )
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the visible INDEX ANALYTICUS blocks and the closing ORDO RERUM section from "
            "OCR files 741-748. This rerun removed validation-blocking split-word hyphen artifacts "
            "while preserving prior material locators."
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
            "current_focus": "Payload repaired and validated after PG076 line-break hyphen import failure.",
            "completed": [
                "Read alphabetical-index skill contract and output format",
                "Reviewed import validation rule for OCR line-break hyphen artifacts",
                "Spot-checked cleaned OCR reader output for PG076 file 743",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload text fields",
                "Synchronized final payload and intermediate fragments",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed)}",
                "Refs pointed to existing checkpoint entry keys; missing-entry-key errors were caused by rejected hyphenated entries.",
                "The single scripture_ref from the checkpoint was preserved unchanged.",
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
