#!/usr/bin/env python3
"""Repair PG113 alphabetical payload OCR line-break hyphen artifacts.

Usage:
  python scripts/pipeline_index_extraction/repair_pg113_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG113_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG113"

LINEBREAK_HYPHEN_RE = re.compile(
    r"(?<=[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])"
    r"[-\u2010\u2011]"
    r"\s+"
    r"(?=[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])"
)

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def repair_text(value: str) -> tuple[str, int]:
    repaired, count = LINEBREAK_HYPHEN_RE.subn("", value)
    stripped = repaired.rstrip()
    if stripped.endswith("-"):
        repaired = stripped[:-1].rstrip()
        count += 1
    return repaired, count


def scan_strings(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    residual: list[dict[str, str]] = []
    for item in items:
        entry_key = item.get("entry_key", "?")
        for field in ENTRY_FIELDS:
            value = item.get(field)
            if not isinstance(value, str):
                continue
            match = LINEBREAK_HYPHEN_RE.search(value)
            if match:
                start = max(0, match.start() - 24)
                end = min(len(value), match.end() + 24)
                residual.append(
                    {
                        "entry_key": entry_key,
                        "field": field,
                        "sample": value[start:end],
                    }
                )
    return residual


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    repairs: list[dict[str, Any]] = []
    changed_entries = 0
    changed_fields = 0
    removed_entry_keys: set[str] = set()
    cleaned_entries: list[dict[str, Any]] = []

    for entry in data["entries"]:
        entry_raw = entry.get("entry_raw")
        if isinstance(entry_raw, str) and entry_raw.strip() and set(entry_raw.strip()) <= {"-"}:
            removed_entry_keys.add(entry["entry_key"])
            continue
        field_changes: dict[str, int] = {}
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            repaired, count = repair_text(value)
            if count:
                if field != "entry_raw" and repaired.strip() and set(repaired.strip()) <= {"-"}:
                    repaired = ""
                entry[field] = repaired
                field_changes[field] = count
                changed_fields += 1
        if not (entry.get("entry_raw") or "").strip():
            removed_entry_keys.add(entry["entry_key"])
            continue
        if field_changes:
            changed_entries += 1
            entry.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
                "source_file": entry.get("raw_json", {}).get("source_file"),
                "reason": (
                    "Merged OCR line-break hyphen artifacts in canonical entry text "
                    "fields after checking the PG113 cleaned OCR reader output for "
                    "the index tail."
                ),
                "fields": field_changes,
            }
            repairs.append(
                {
                    "entry_key": entry["entry_key"],
                    "source_file": entry.get("raw_json", {}).get("source_file"),
                    "fields": field_changes,
                }
            )
        cleaned_entries.append(entry)

    data["entries"] = cleaned_entries
    if removed_entry_keys:
        data["refs"] = [
            ref for ref in data["refs"] if ref.get("entry_key") not in removed_entry_keys
        ]

    residual = scan_strings(data["entries"])
    if residual:
        raise SystemExit(
            "Residual line-break hyphen artifacts remain: "
            + json.dumps(residual[:25], ensure_ascii=False)
        )

    now = now_iso()
    data["generated_at"] = now
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": now,
            "message": (
                "PG113 rerun repaired validation-blocking OCR line-break hyphen "
                "artifacts in entry text fields while preserving the prior "
                "section structure and material locators."
            ),
            "details": {
                "changed_entries": changed_entries,
                "changed_fields": changed_fields,
                "refs_changed": 0,
                "removed_entry_keys": sorted(removed_entry_keys),
                "evidence_files": [
                    str(
                        ROOT
                        / "teste/PG113/text/2f4397d9-e241-4525-8b50-7eec2130d78b-644.txt"
                    ),
                    str(
                        ROOT
                        / "teste/PG113/text/2f4397d9-e241-4525-8b50-7eec2130d78b-654.txt"
                    ),
                    str(
                        ROOT
                        / "teste/PG113/text/2f4397d9-e241-4525-8b50-7eec2130d78b-664.txt"
                    ),
                ],
            },
        }
    )

    PAYLOAD.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "entries.json").write_text(
        json.dumps(data["entries"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "refs.json").write_text(
        json.dumps(data["refs"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    todo = {
        "volume_id": "PG113",
        "updated_at": now,
        "current_focus": "Validate repaired PG113 alphabetical payload",
        "completed": [
            "Confirmed the two alphabetical index sections in files 644-664",
            "Confirmed ORDO RERUM remains a separate closing section in files 665-666",
            "Removed validation-blocking OCR line-break hyphen artifacts from canonical entry text fields",
            "Removed the non-entry separator row inherited from the OCR tail",
            "Synchronized the PG113 payload and intermediate entry checkpoint",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only for PG113"],
        "blocked": [],
        "notes": [
            "Repair is scoped to the rerun validation failure for OCR line-break hyphen artifacts.",
            "Existing refs and target locators were preserved because no validator-style hyphen artifacts were present there.",
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(todo, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "payload": str(PAYLOAD),
                "changed_entries": changed_entries,
                "changed_fields": changed_fields,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
