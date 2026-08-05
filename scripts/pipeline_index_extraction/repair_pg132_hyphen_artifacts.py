#!/usr/bin/env python3
"""Repair PG132 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg132_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG132"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG132_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG132"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def merge_word_hyphenation(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    repaired_fields: list[dict[str, str]] = []

    for entry in payload.get("entries", []):
        changed = False
        for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            before = entry.get(field)
            after = merge_word_hyphenation(before)
            if before != after:
                entry[field] = after
                changed = True
                repaired_fields.append({"entry_key": entry["entry_key"], "field": field})
        if changed:
            raw_json = entry.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            note = (
                "PG132 rerun merged a validator-style OCR line-break hyphen artifact after checking "
                "the cleaned OCR reader output for source files 695-700 and 702."
            )
            if note not in notes:
                notes.append(note)

    residual = []
    for idx, entry in enumerate(payload["entries"], start=1):
        for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            if has_validator_hyphen_artifact(entry.get(field)):
                residual.append({"index": idx, "entry_key": entry["entry_key"], "field": field})
    if residual:
        raise SystemExit(
            json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2)
        )

    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing_refs = [
        {"index": idx, "entry_key": ref["entry_key"]}
        for idx, ref in enumerate(payload["refs"], start=1)
        if ref["entry_key"] not in entry_keys
    ]
    if missing_refs:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing_refs[:50]}, ensure_ascii=False, indent=2))

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG132 rerun repaired validation-blocking OCR line-break hyphen artifacts in entry text "
        "fields; the earlier missing entry_key ref errors were importer cascades from rejected entries."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
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
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "repaired_field_count": len(repaired_fields),
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG132 import validation failure; ready for validation.",
            "completed": [
                "Read the current validation failure and the exact rejected PG132 entries",
                "Checked the cleaned OCR reader output for source files 695-700 and 702",
                "Merged validator-style OCR line-break hyphen artifacts in payload entry text fields",
                "Refreshed the final payload and per-volume intermediate fragments",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only for PG132"],
            "blocked": [],
            "notes": [
                "The repair is limited to proven line-wrap hyphen artifacts in entry text fields.",
                "No entry_key/ref remapping was needed; the previous missing refs were a validation cascade.",
                "The cleaned OCR reader output already showed joined forms such as porcos, amissorum, matris, Constantinopol., interpretatio.",
            ],
        },
    )
    print(json.dumps({"repaired_field_count": len(repaired_fields)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
