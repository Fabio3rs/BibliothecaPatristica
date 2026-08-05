"""Repair PG090 alphabetical payload after a line-break hyphen validation failure.

Usage: python scripts/pipeline_index_extraction/repair_pg090_alphabetical_payload.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG090_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG090"
FINAL_INTERMEDIATE = INTERMEDIATE / "final_payload.json"
TODO = INTERMEDIATE / "todo.json"

KEEP_KEY = "PG090:entry:0760"
REMOVE_KEY = "PG090:entry:0761"
SECTION_KEY = "PG090:alpha:analytic_subject:003"
SOURCE_739 = (
    "/homessddata/Projects/pdfocr/teste/PG090/text/"
    "350e71ff-d304-4259-b89d-0865c27b28d9-739.txt"
)
SOURCE_740 = (
    "/homessddata/Projects/pdfocr/teste/PG090/text/"
    "350e71ff-d304-4259-b89d-0865c27b28d9-740.txt"
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def renumber_entry_orders(payload: dict) -> None:
    counters: dict[str, int] = {}
    for entry in payload["entries"]:
        section_key = entry["section_key"]
        counters[section_key] = counters.get(section_key, 0) + 1
        entry["entry_order"] = counters[section_key]


def renumber_ref_orders(payload: dict) -> None:
    counters: dict[str, int] = {}
    for ref in payload["refs"]:
        entry_key = ref["entry_key"]
        counters[entry_key] = counters.get(entry_key, 0) + 1
        ref["ref_order"] = counters[entry_key]


def main() -> None:
    payload = load_json(PAYLOAD)
    entries = payload["entries"]
    by_key = {entry["entry_key"]: entry for entry in entries}
    keep = by_key[KEEP_KEY]
    remove = by_key[REMOVE_KEY]

    repaired_entry = (
        "Divinæ dotes rebus impressæ. Imaginis ratio in utentibus ratione. "
        "Sapientia artifex, non ipsa vere existens, sed in mente"
    )
    keep["lemma_raw"] = repaired_entry
    keep["lemma_display"] = repaired_entry
    keep["lemma_norm"] = repaired_entry
    keep["lemma_sort"] = (
        "divinae dotes rebus impressae imaginis ratio in utentibus ratione "
        "sapientia artifex non ipsa vere existens sed in mente"
    )
    keep["entry_raw"] = f"{repaired_entry}, 450."
    keep["inferred_printed_page"] = 450
    keep["section_start_file"] = (
        "/homessddata/Projects/pdfocr/teste/PG090/text/"
        "350e71ff-d304-4259-b89d-0865c27b28d9-736.txt"
    )
    keep["editorial_anchor_file"] = SOURCE_740
    keep["target_file_best"] = SOURCE_740
    keep["confidence"] = min(float(keep.get("confidence") or 0.87), 0.86)
    keep.setdefault("raw_json", {})["pg090_rerun_linebreak_hyphen_repair"] = {
        "reason": (
            "OCR file 739 ends the logical entry with 'utenti-' and OCR file 740 "
            "continues with 'bus ratione'; the page header '1471 INDEX RERUM. 1472' "
            "was removed from the logical entry."
        ),
        "merged_entry_keys": [KEEP_KEY, REMOVE_KEY],
        "removed_continuation_entry_key": REMOVE_KEY,
        "source_files_checked": [SOURCE_739, SOURCE_740],
        "removed_header_refs": ["1471", "1472"],
    }
    keep.setdefault("raw_json", {})["source_file"] = SOURCE_740
    keep.setdefault("raw_json", {})["page_refs"] = [
        {
            "ref_raw": "450",
            "page_ref_raw": "450",
            "page_ref_int": 450,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "ref_kind": "editorial_page",
        }
    ]

    payload["entries"] = [entry for entry in entries if entry["entry_key"] != REMOVE_KEY]

    new_refs = []
    moved_ref = None
    for ref in payload["refs"]:
        if ref["entry_key"] != REMOVE_KEY:
            new_refs.append(ref)
            continue
        if ref.get("page_ref_raw") == "450":
            moved_ref = ref
    if moved_ref is None:
        raise SystemExit("Expected continuation ref 450 was not found")
    moved_ref["entry_key"] = KEEP_KEY
    moved_ref["ref_order"] = 1
    moved_ref["target_file"] = SOURCE_740
    moved_ref["target_file_probability"] = 0.62
    moved_ref["section_start_file"] = keep["section_start_file"]
    moved_ref["editorial_anchor_file"] = SOURCE_740
    moved_ref["confidence"] = 0.72
    moved_ref["raw_json"] = {
        "source_file": SOURCE_740,
        "section_kind": "analytic_subject",
        "pg090_rerun_linebreak_hyphen_repair": {
            "reason": "Moved the real material ref from the OCR continuation entry; discarded page-header refs.",
            "remapped_from_entry_key": REMOVE_KEY,
        },
    }
    new_refs.append(moved_ref)
    payload["refs"] = new_refs

    renumber_entry_orders(payload)
    renumber_ref_orders(payload)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload.setdefault("notes", []).append(
        "PG090 rerun repaired a validation-blocking OCR line-break hyphen split at the 1469/1471 index page boundary."
    )

    write_json(PAYLOAD, payload)
    write_json(FINAL_INTERMEDIATE, payload)
    write_json(
        TODO,
        {
            "volume_id": "PG090",
            "updated_at": payload["generated_at"],
            "current_focus": "Payload repaired and ready for import validation after PG090 hyphen artifact failure.",
            "completed": [
                "Verified entries PG090:entry:0760 and PG090:entry:0761 against OCR files 739 and 740",
                "Merged terminal OCR line-break hyphen artifact into one logical entry",
                "Removed page-header refs 1471 and 1472 from the logical material refs",
                "Rebuilt canonical payload and intermediate final payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The correction preserves OCR literals except proven line-break hyphenation.",
                "The cited reference retained for the merged entry is 450.",
            ],
        },
    )


if __name__ == "__main__":
    main()
