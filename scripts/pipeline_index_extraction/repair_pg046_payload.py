#!/usr/bin/env python3
"""Repair PG046 alphabetical payload artifacts.

Usage:
  python scripts/pipeline_index_extraction/repair_pg046_payload.py

The script fixes OCR line-break hyphen artifacts in the PG046 alphabetical
payload/checkpoints, removes two verified running-head intrusions from logical
entries, updates the confirmed tail section spans, and refreshes the local TODO.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "teste" / "PG046" / "text"
PAYLOAD_PATH = ROOT / "data" / "alphabetical_index_payloads" / "PG046_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / "PG046_helper_request.json"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / "PG046"
NULL_REF_HELPER_REQUEST_PATH = INTERMEDIATE_DIR / "null_ref_helper_request.json"
NULL_REF_HELPER_OUTPUT_PATH = INTERMEDIATE_DIR / "null_ref_helper_output.json"

F628 = str(SOURCE_ROOT / "f2201358-451d-41c2-a1ce-03a01a12a31b-628.txt")
F638 = str(SOURCE_ROOT / "f2201358-451d-41c2-a1ce-03a01a12a31b-638.txt")
F639 = str(SOURCE_ROOT / "f2201358-451d-41c2-a1ce-03a01a12a31b-639.txt")
F640 = str(SOURCE_ROOT / "f2201358-451d-41c2-a1ce-03a01a12a31b-640.txt")
F641 = str(SOURCE_ROOT / "f2201358-451d-41c2-a1ce-03a01a12a31b-641.txt")

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])- +([{WORD_CHARS}])")

SPECIAL_TEXT_REPAIRS = {
    "Octavæ my- 1263 INDEX ANALYTICUS 1264 sternum": "Octavæ mysterium",
    "ejus my- 1269 ORDO NOVUS CUM VETERI COLLATUS. 1270 ntica": "ejus myntica",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def repair_text(value: str) -> str:
    repaired = value
    for old, new in SPECIAL_TEXT_REPAIRS.items():
        repaired = repaired.replace(old, new)
    while True:
        next_value = LINEBREAK_HYPHEN_RE.sub(r"\1\2", repaired)
        if next_value == repaired:
            return repaired
        repaired = next_value


def repair_strings(value: Any) -> Any:
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_strings(item) for key, item in value.items()}
    return value


def update_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = deepcopy(sections)
    for section in updated:
        key = section.get("section_key")
        if key == "PG046:alpha:analytic_subject:001":
            section["page_end"] = 1268
            section["file_start"] = F628
            section["file_end"] = F638
            section["raw_json"]["evidence_files"] = [
                str(SOURCE_ROOT / f"f2201358-451d-41c2-a1ce-03a01a12a31b-{seq}.txt")
                for seq in range(628, 639)
            ]
            section["raw_json"]["tail_boundary_note"] = (
                "OCR inspection shows the analytical index continues through the "
                "upper text block of file 638 and stops before the ORDO NOVUS table."
            )
        elif key == "PG046:alpha:crosswalk_index:002":
            section["page_start"] = 1269
            section["page_end"] = 1272
            section["file_start"] = F638
            section["file_end"] = F639
            section["raw_json"]["evidence_files"] = [F638, F639]
            section["raw_json"]["section_kind_reason"] = (
                "Parallel comparison table between the newer edition and the "
                "Morellian order. It begins in the lower half of OCR file 638 and "
                "continues in file 639."
            )
        elif key == "PG046:alpha:ordo_rerum:003":
            section["page_start"] = 1273
            section["page_end"] = 1276
            section["file_start"] = F640
            section["file_end"] = F641
            section["raw_json"]["evidence_files"] = [F640, F641]
            section["raw_json"]["tail_boundary_note"] = (
                "OCR inspection confirms ORDO RERUM spans files 640-641."
            )
    return updated


def update_coverage(payload: dict[str, Any]) -> None:
    payload["coverage"]["evidence_files"] = [
        str(SOURCE_ROOT / f"f2201358-451d-41c2-a1ce-03a01a12a31b-{seq}.txt")
        for seq in range(628, 642)
    ]
    payload["coverage"]["entries_status_reason"] = (
        "Recovered the PG046 analytical index from files 628-638 and retained the "
        "adjacent crosswalk and ORDO RERUM editorial blocks as separate sections. "
        "This rerun removes OCR line-break hyphen artifacts from the checkpoint."
    )
    upsert_note(
        payload,
        "pg046_hyphen_repair_20260725",
        "Rerun repaired line-break hyphen artifacts in logical entries and "
        "helper echoes; verified analytical/crosswalk/ordo tail boundaries "
        "against OCR reader output.",
        0.96,
    )


def update_todo() -> None:
    todo = {
        "volume_id": "PG046",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "current_focus": "Completed PG046 rerun repair and validation",
        "completed": [
            "confirmed analytical/crosswalk/ordo section spans through OCR reader",
            "removed line-break hyphen artifacts from payload and helper request",
            "refreshed intermediate sections, entries, refs, coverage, notes, manifest, and TODO",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Final import validation should pass without orphan refs after repaired entries are accepted.",
            "Supplemental ref-level helper applied only high-confidence target_candidate matches.",
        ],
    }
    dump_json(INTERMEDIATE_DIR / "todo.json", todo)


def upsert_note(payload: dict[str, Any], note_key: str, note_raw: str, confidence: float) -> None:
    notes = [note for note in payload.get("notes", []) if note.get("note_key") != note_key]
    notes.append({"note_key": note_key, "note_raw": note_raw, "confidence": confidence})
    payload["notes"] = notes


def write_null_ref_helper_request(payload: dict[str, Any]) -> None:
    entries_by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    helper_entries = []
    for ref in payload["refs"]:
        if ref.get("target_file"):
            continue
        entry = entries_by_key.get(ref["entry_key"])
        if not entry:
            continue
        page_hint = ref.get("page_ref_raw") or ref.get("range_start_raw")
        page_hint_int = ref.get("page_ref_int")
        query_names = [
            text
            for text in (
                entry.get("lemma_raw"),
                ref.get("ref_raw"),
                entry.get("entry_raw"),
            )
            if text
        ]
        helper_entries.append(
            {
                "entry_id": f"nullref_{ref['entry_key'].replace(':', '_')}_{ref['ref_order']:03d}",
                "lemma_raw": entry.get("lemma_raw") or ref.get("ref_raw"),
                "query_names": query_names,
                "page_hints": [str(page_hint)] if page_hint else [],
                "page_hint_ints": [page_hint_int] if isinstance(page_hint_int, int) else [],
                "context_raw": entry.get("entry_raw"),
                "raw_json": {
                    "entry_key": ref["entry_key"],
                    "ref_order": ref["ref_order"],
                    "ref_raw": ref.get("ref_raw"),
                },
            }
        )
    request = {
        "volume_id": "PG046",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    dump_json(NULL_REF_HELPER_REQUEST_PATH, request)


def apply_null_ref_helper_output(payload: dict[str, Any]) -> int:
    if not NULL_REF_HELPER_OUTPUT_PATH.exists():
        return 0
    output = load_json(NULL_REF_HELPER_OUTPUT_PATH)
    by_id = {entry["entry_id"]: entry for entry in output.get("entries", [])}
    updated = 0
    for ref in payload["refs"]:
        if ref.get("target_file"):
            continue
        helper_id = f"nullref_{ref['entry_key'].replace(':', '_')}_{ref['ref_order']:03d}"
        helper_entry = by_id.get(helper_id)
        if not helper_entry or helper_entry.get("status") != "resolved":
            continue
        best = helper_entry.get("best_candidate") or {}
        reason = best.get("reason_summary") or ""
        probability = best.get("probability")
        if not reason.startswith("target_candidate"):
            continue
        if not isinstance(probability, (int, float)) or probability < 0.75:
            continue
        ref["target_file"] = best.get("file")
        ref["target_file_probability"] = probability
        ref.setdefault("raw_json", {})["null_ref_helper_locator"] = {
            "status": helper_entry.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": reason,
            "file_seq": best.get("file_seq"),
            "inferred_printed_page": best.get("inferred_printed_page"),
            "editorial_page_decision_source": best.get("editorial_page_decision_source"),
            "probability": probability,
        }
        updated += 1
    upsert_note(
        payload,
        "pg046_ref_locator_retry_20260725",
        f"Supplemental ref-level helper retried null target_file refs; "
        f"{updated} high-confidence target_candidate results were applied, "
        "while ambiguous, unresolved, index-page, and low-probability matches "
        "were left null.",
        0.9,
    )
    return updated


def main() -> None:
    payload = repair_strings(load_json(PAYLOAD_PATH))
    payload["sections"] = update_sections(payload["sections"])
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    update_coverage(payload)
    apply_null_ref_helper_output(payload)
    write_null_ref_helper_request(payload)
    dump_json(PAYLOAD_PATH, payload)

    helper_request = repair_strings(load_json(HELPER_REQUEST_PATH))
    dump_json(HELPER_REQUEST_PATH, helper_request)

    fragment_map = {
        "sections.json": payload["sections"],
        "nodes.json": payload["nodes"],
        "entries.json": payload["entries"],
        "refs.json": payload["refs"],
        "scripture_refs.json": payload["scripture_refs"],
        "coverage.json": payload["coverage"],
        "notes.json": payload["notes"],
        "volume.json": payload["volume"],
    }
    for filename, data in fragment_map.items():
        dump_json(INTERMEDIATE_DIR / filename, data)
    dump_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": "PG046",
            "generated_at": payload["generated_at"],
            "sections_count": len(payload["sections"]),
            "nodes_count": len(payload["nodes"]),
            "entries_count": len(payload["entries"]),
            "refs_count": len(payload["refs"]),
            "scripture_refs_count": len(payload["scripture_refs"]),
        },
    )
    update_todo()


if __name__ == "__main__":
    main()
