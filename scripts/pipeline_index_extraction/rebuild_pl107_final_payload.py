#!/usr/bin/env python3
"""Rebuild the PL107 alphabetical payload from the validated chunk and helper output.

Run:
  python scripts/pipeline_index_extraction/rebuild_pl107_final_payload.py
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
CHUNK_PATH = ROOT / "data/intermediate_payloads/PL107/chunks/section_001_part_001.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL107_helper_output.json"
CURRENT_FINAL_PATH = ROOT / "data/alphabetical_index_payloads/PL107_alphabetical_indices.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def helper_summary(helper_entry: dict) -> dict:
    best = helper_entry.get("best_candidate") or {}
    summary = {
        "status": helper_entry.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "file_seq": best.get("file_seq"),
            "inferred_printed_page": best.get("inferred_printed_page"),
        },
    }
    top_candidates = []
    for candidate in helper_entry.get("candidates", [])[:3]:
        evidence_kinds = [
            item.get("kind")
            for item in candidate.get("evidence", [])
            if isinstance(item, dict) and item.get("kind")
        ]
        top_candidates.append(
            {
                "rank": candidate.get("rank"),
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": evidence_kinds,
            }
        )
    if top_candidates:
        summary["top_candidates"] = top_candidates
    return summary


def main() -> None:
    chunk = load_json(CHUNK_PATH)
    helper = load_json(HELPER_OUTPUT_PATH)
    existing = load_json(CURRENT_FINAL_PATH)

    helper_by_order = {}
    for idx, helper_entry in enumerate(helper.get("entries", []), start=1):
        helper_by_order[idx] = helper_entry

    entries = copy.deepcopy(chunk.get("entries", []))
    refs = copy.deepcopy(chunk.get("refs", []))
    ref_index = {}
    for ref in refs:
        ref_index.setdefault(ref["entry_key"], []).append(ref)

    for idx, entry in enumerate(entries, start=1):
        helper_entry = helper_by_order.get(idx)
        if not helper_entry:
            continue
        summary = helper_summary(helper_entry)
        entry.setdefault("raw_json", {})
        entry["raw_json"]["helper_resolution"] = summary
        best = helper_entry.get("best_candidate") or {}
        best_file = best.get("file")
        if best_file:
            entry["target_file_best"] = best_file
        if best.get("probability") is not None:
            entry["raw_json"]["helper_resolution"]["best_candidate"]["probability"] = best.get("probability")

        for ref in ref_index.get(entry["entry_key"], []):
            ref.setdefault("raw_json", {})
            ref["raw_json"]["helper_resolution"] = summary
            if best_file:
                ref["target_file"] = best_file
                ref["target_file_probability"] = best.get("probability")

    final = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume": existing["volume"],
        "sections": copy.deepcopy(chunk.get("sections", [])),
        "nodes": copy.deepcopy(chunk.get("nodes", [])),
        "entries": entries,
        "refs": refs,
        "scripture_refs": copy.deepcopy(chunk.get("scripture_refs", [])),
        "coverage": copy.deepcopy(existing.get("coverage", {})),
        "notes": copy.deepcopy(existing.get("notes", [])),
    }

    final["coverage"]["entries_status"] = "recovered"
    final["coverage"]["entries_status_reason"] = (
        "Recovered the closing ORDO RERUM contents table from the OCR tail, rebuilt the final "
        "payload from the validated chunk sequence, and enriched locator targets from the helper "
        "output where available."
    )
    evidence_files = final["coverage"].get("evidence_files") or []
    if evidence_files:
        final["coverage"]["evidence_files"] = evidence_files
    else:
        final["coverage"]["evidence_files"] = [
            str(CHUNK_PATH),
            "/homessddata/Projects/pdfocr/teste/PL107/text/707f0d60-a977-42e4-bb6e-28bb5caeea37-582.txt",
            "/homessddata/Projects/pdfocr/teste/PL107/text/707f0d60-a977-42e4-bb6e-28bb5caeea37-583.txt",
            "/homessddata/Projects/pdfocr/teste/PL107/text/707f0d60-a977-42e4-bb6e-28bb5caeea37-584.txt",
            "/homessddata/Projects/pdfocr/teste/PL107/text/707f0d60-a977-42e4-bb6e-28bb5caeea37-585.txt",
            "/homessddata/Projects/pdfocr/teste/PL107/text/707f0d60-a977-42e4-bb6e-28bb5caeea37-586.txt",
        ]

    notes = final["notes"]
    helper_note = "Helper output was used to enrich target_file_best and target_file for the first 437 entries."
    if helper_note not in notes:
        notes.append(helper_note)

    CURRENT_FINAL_PATH.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
