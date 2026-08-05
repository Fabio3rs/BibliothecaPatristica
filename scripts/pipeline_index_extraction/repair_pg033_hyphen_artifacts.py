# Usage: python scripts/pipeline_index_extraction/repair_pg033_hyphen_artifacts.py
# Repairs PG033 alphabetical payload OCR line-break hyphen artifacts and
# refreshes the volume-local checkpoints used by the import pipeline.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG033_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG033"

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
            start = max(0, match.start() - 24)
            end = min(len(obj), match.end() + 24)
            hits.append({"path": path, "sample": obj[start:end]})
    return hits


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    repairs: list[dict[str, Any]] = []

    for entry in data["entries"]:
        changed_fields: dict[str, dict[str, Any]] = {}
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
                "source_file": entry.get("raw_json", {}).get("source_file"),
                "reason": (
                    "Removed OCR line-break hyphenation artifacts where the "
                    "reader/OCR left a word split across two visual lines."
                ),
                "fields": changed_fields,
            }
            repairs.append(
                {
                    "entry_key": entry["entry_key"],
                    "fields": sorted(changed_fields),
                    "source_file": entry.get("raw_json", {}).get("source_file"),
                }
            )

    by_key = {entry["entry_key"]: entry for entry in data["entries"]}
    entry_0055 = by_key.get("PG033:alpha:alphabetical_general:001:entry:0055")
    if entry_0055 and isinstance(entry_0055.get("entry_raw"), str):
        old_tail = "Imbres lar-"
        new_tail = (
            "Imbres largiores Hierosolymis effusi, urbem pene perdiderant "
            "Cyrilli tempore, 89."
        )
        if entry_0055["entry_raw"].endswith(old_tail):
            entry_0055["entry_raw"] = entry_0055["entry_raw"][: -len(old_tail)] + new_tail
            entry_0055.setdefault("raw_json", {})[
                "page_boundary_hyphen_repair"
            ] = {
                "source_files": [
                    str(
                        ROOT
                        / "teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-850.txt"
                    ),
                    str(
                        ROOT
                        / "teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-851.txt"
                    ),
                ],
                "reason": (
                    "OCR page-boundary continuation: final 'Imbres lar-' in "
                    "file 850 continues as 'giores Hierosolymis effusi...' "
                    "at the start of file 851."
                ),
            }
            repairs.append(
                {
                    "entry_key": entry_0055["entry_key"],
                    "fields": ["entry_raw"],
                    "source_file": entry_0055.get("raw_json", {}).get("source_file"),
                    "boundary_continuation": True,
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
                "Repaired PG033 OCR line-break hyphen artifacts that blocked "
                "alphabetical import validation."
            ),
            "details": repairs,
        }
    )

    PAYLOAD.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "entries.json").write_text(
        json.dumps(data["entries"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "refs.json").write_text(
        json.dumps(data["refs"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "sections.json").write_text(
        json.dumps(data["sections"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    todo = {
        "volume_id": "PG033",
        "updated_at": now,
        "current_focus": "Validate repaired PG033 alphabetical payload",
        "completed": [
            "Confirmed INDEX IN CYRILLUM is the alphabetical/analytic section",
            "Confirmed ORDO RERUM pages are editorial closure and remain excluded",
            "Repaired OCR line-break hyphen artifacts in entry, context, and lemma fields",
            "Refreshed sections.json, entries.json, and refs.json checkpoints",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Repair is scoped to the import-blocking PG033 rerun failure.",
            "Existing material locators and entry keys are preserved from the checkpoint.",
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
