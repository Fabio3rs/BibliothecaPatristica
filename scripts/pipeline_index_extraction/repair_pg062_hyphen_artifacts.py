# Usage: python scripts/pipeline_index_extraction/repair_pg062_hyphen_artifacts.py
# Repairs PG062 ORDO RERUM payload OCR line-break hyphen artifacts and
# refreshes the volume-local checkpoints used by the import pipeline.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG062_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG062"
SOURCE_FILE_784 = (
    ROOT
    / "teste/PG062/text/94b5491c-7fa9-4be7-805e-2351a23ee114-784.txt"
)

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
    return repaired, count


def scan_payload(obj: Any, path: str = "$") -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            hits.extend(scan_payload(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            hits.extend(scan_payload(value, f"{path}[{index}]"))
    elif isinstance(obj, str):
        match = LINEBREAK_HYPHEN_RE.search(obj)
        if match:
            start = max(0, match.start() - 32)
            end = min(len(obj), match.end() + 32)
            hits.append({"path": path, "sample": obj[start:end]})
    return hits


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    repairs: list[dict[str, Any]] = []

    for entry in data["entries"]:
        changed_fields: dict[str, dict[str, int]] = {}
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            repaired, count = repair_text(value)
            if count:
                entry[field] = repaired
                changed_fields[field] = {"linebreak_hyphen_merges": count}
        if changed_fields:
            entry.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
                "source_file": str(SOURCE_FILE_784),
                "reason": (
                    "Removed OCR line-break hyphenation artifacts visible in "
                    "the PG062 ORDO RERUM continuation page; the OCR reader "
                    "confirms these are split words, not printed hyphenation."
                ),
                "fields": changed_fields,
            }
            repairs.append(
                {
                    "entry_key": entry["entry_key"],
                    "fields": sorted(changed_fields),
                }
            )

    remaining = scan_payload(data["entries"])
    if remaining:
        raise SystemExit(
            "remaining line-break hyphen artifacts in entries: "
            + json.dumps(remaining[:20], ensure_ascii=False)
        )

    now = now_iso()
    data["generated_at"] = now
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": now,
            "message": (
                "Repaired PG062 OCR line-break hyphen artifacts that blocked "
                "alphabetical import validation."
            ),
            "details": repairs,
        }
    )

    PAYLOAD.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs"):
        (INTERMEDIATE / f"{key}.json").write_text(
            json.dumps(data[key], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (INTERMEDIATE / "coverage.json").write_text(
        json.dumps(data.get("coverage", {}), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "notes.json").write_text(
        json.dumps(data.get("notes", []), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    todo = {
        "volume_id": "PG062",
        "updated_at": now,
        "current_focus": "Validate repaired PG062 alphabetical payload",
        "completed": [
            "Confirmed ORDO RERUM/INDEX RERUM closure spans OCR files 783-784",
            "Verified file 784 through scripts/read_ocr_page_text.py",
            "Repaired OCR line-break hyphen artifacts in entry fields",
            "Refreshed volume-local intermediate payload fragments",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Repair preserves existing entry keys, refs, and material locators from the checkpoint.",
            "The exact prior validation failure was caused by entry_raw line-break hyphen artifacts.",
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {"payload": str(PAYLOAD), "repaired_entries": len(repairs)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
