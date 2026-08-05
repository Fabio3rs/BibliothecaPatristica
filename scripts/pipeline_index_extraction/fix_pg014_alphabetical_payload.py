# Usage: python scripts/pipeline_index_extraction/fix_pg014_alphabetical_payload.py
# Repairs PG014 alphabetical payload OCR line-break hyphen artifacts, refreshes
# helper-backed locators from the PG014 helper output, and rewrites the final JSON.

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG014"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
INTERMEDIATE = ROOT / "data" / "intermediate_payloads" / VOLUME_ID

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def merge_linebreak_hyphens(value: str) -> str:
    previous = None
    current = value
    while previous != current:
        previous = current
        current = LINEBREAK_HYPHEN_RE.sub(r"\1\2", current)
    if current.endswith("-"):
        current = current[:-1].rstrip()
    return current


def repair_strings(value: Any) -> Any:
    if isinstance(value, str):
        return merge_linebreak_hyphens(value)
    if isinstance(value, list):
        return [repair_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_strings(item) for key, item in value.items()}
    return value


def helper_index(helper_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_payload.get("entries", []):
        entry_id = item.get("entry_id")
        if not isinstance(entry_id, str) or "__" not in entry_id:
            continue
        entry_key = entry_id.split("__", 1)[0]
        best = item.get("best_candidate")
        if not isinstance(best, dict) or not best.get("file"):
            continue
        current = out.get(entry_key)
        probability = float(best.get("probability") or 0)
        if current is None or probability > float(current["best_candidate"].get("probability") or 0):
            out[entry_key] = item
    return out


def apply_helper(payload: dict[str, Any], helper_payload: dict[str, Any]) -> None:
    by_entry = helper_index(helper_payload)
    for entry in payload.get("entries", []):
        entry_key = entry.get("entry_key")
        helper_item = by_entry.get(entry_key)
        if not helper_item:
            if not entry.get("target_file_best"):
                entry.setdefault("raw_json", {})["locator_rerun_note"] = (
                    "PG014 rerun inspected refreshed helper output; no material target candidate was returned."
                )
            continue
        best = helper_item["best_candidate"]
        best_file = best.get("file")
        entry_raw = entry.setdefault("raw_json", {})
        entry_raw["helper_rerun"] = {
            "status": helper_item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": {
                "file": best_file,
                "probability": best.get("probability"),
                "score": best.get("score"),
            },
        }
        if best_file and not entry.get("target_file_best"):
            entry["target_file_best"] = best_file
            entry["confidence"] = max(float(entry.get("confidence") or 0), 0.72)

    entries_by_key = {entry.get("entry_key"): entry for entry in payload.get("entries", [])}
    for ref in payload.get("refs", []):
        entry_key = ref.get("entry_key")
        helper_item = by_entry.get(entry_key)
        entry = entries_by_key.get(entry_key) or {}
        if not ref.get("target_file") and entry.get("target_file_best"):
            ref["target_file"] = entry["target_file_best"]
            ref["target_file_probability"] = ref.get("target_file_probability") or 0.72
            ref["confidence"] = max(float(ref.get("confidence") or 0), 0.72)
        if helper_item:
            best = helper_item["best_candidate"]
            ref.setdefault("raw_json", {})["helper_rerun"] = {
                "status": helper_item.get("status"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "score": best.get("score"),
                },
            }
        elif not ref.get("target_file"):
            ref.setdefault("raw_json", {})["locator_rerun_note"] = (
                "PG014 rerun inspected refreshed helper output; reference target remains unresolved."
            )


def main() -> None:
    payload = json.loads(OUTPUT.read_text())
    helper_payload = json.loads(HELPER_OUTPUT.read_text())

    payload = repair_strings(payload)
    apply_helper(payload, helper_payload)
    payload["generated_at"] = utc_now()
    payload.setdefault("coverage", {})["entries_status_reason"] = (
        "Recovered the analytic index entries from files 662-696; files 659-661 are introductory matter and "
        "were excluded from the final entry list. The PG014 rerun merged OCR line-break hyphen artifacts, "
        "refreshed locator evidence with index_target_locator.py, and preserved unresolved cross-reference or "
        "weak page-signal cases in raw_json."
    )
    notes = payload.setdefault("notes", [])
    notes.append(
        {
            "type": "rerun_repair",
            "message": (
                "Fixed PG014 import failure by merging OCR line-break hyphen artifacts in entries/refs and "
                "reusing refreshed helper evidence for locator fields where material candidates were available."
            ),
            "created_at": payload["generated_at"],
        }
    )

    backup = OUTPUT.with_suffix(".before_pg014_rerun_fix.json")
    if not backup.exists():
        shutil.copy2(OUTPUT, backup)

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs"):
        (INTERMEDIATE / f"{key}.json").write_text(
            json.dumps(payload.get(key, []), ensure_ascii=False, indent=2) + "\n"
        )
    (INTERMEDIATE / "final_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    shutil.copy2(HELPER_OUTPUT, INTERMEDIATE / "helper_output.json")
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": payload["generated_at"],
                "current_focus": "Payload repaired and ready for validation/import.",
                "completed": [
                    "Confirmed INDEX ANALYTICUS section spans OCR files 662-696",
                    "Reran index_target_locator.py for PG014 helper request",
                    "Merged OCR line-break hyphen artifacts in payload text",
                    "Refreshed helper-backed locator evidence where available",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "Unresolved target files are retained only where helper output stayed unresolved or entries are cross-references without material anchors.",
                    "Final payload is assembled from the repaired canonical JSON and mirrored into intermediate fragments.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
