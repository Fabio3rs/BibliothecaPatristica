#!/usr/bin/env python3
"""Repair PG050 Ordo Rerum payload after validation failures.

Usage:
  python scripts/pipeline_index_extraction/repair_pg050_alphabetical_payload.py

The script fixes verified OCR line-break hyphen artifacts, builds a full
index_target_locator request for material page refs, merges reliable helper
target candidates when an output file is present, and rewrites the final JSON.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unicodedata import category, normalize


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG050_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG050_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG050_helper_output.json"
TODO = ROOT / "data/intermediate_payloads/PG050/todo.json"
SOURCE_ROOT = "/homessddata/Projects/pdfocr/teste/PG050/text"
SECTION_FILE_START = (
    "/homessddata/Projects/pdfocr/teste/PG050/text/"
    "a0551315-7b7b-4f0e-bc32-86309a4493e9-430.txt"
)


HYPHEN_FIXES = {
    "uti- litas": "utilitas",
    "uti-\nlitas": "utilitas",
    "occa- sonæ": "occasionæ",
    "occa-\nsonæ": "occasionæ",
    "uti litas": "utilitas",
    "occa sonæ": "occasionæ",
}


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.lower()
    text = normalize("NFKD", text)
    text = "".join(ch for ch in text if category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def ascii_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.lower()
    text = normalize("NFKD", text)
    text = "".join(ch for ch in text if ord(ch) < 128)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def fix_hyphens(value):
    if isinstance(value, str):
        for old, new in HYPHEN_FIXES.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [fix_hyphens(item) for item in value]
    if isinstance(value, dict):
        return {key: fix_hyphens(item) for key, item in value.items()}
    return value


def ref_for_entry(refs, entry_key):
    for ref in refs:
        if ref.get("entry_key") == entry_key:
            return ref
    return None


def compact_query_names(lemma_raw: str | None) -> list[str]:
    if not lemma_raw:
        return []
    candidates: list[str] = []
    first = re.split(r"\s+—\s+|\. —|\.--| -- ", lemma_raw, maxsplit=1)[0]
    candidates.append(first[:180].strip(" ."))
    for pattern in [
        r"\bHOMIL?\.?\s+[IVXLCDM]+[^.—]{0,90}",
        r"\bORAT(?:IO|\.)\s+[IVXLCDM]+[^.—]{0,90}",
        r"\bMONITUM[^.—]{0,100}",
        r"\bADMONITIO[^.—]{0,100}",
        r"\bLAUDATIO[^.—]{0,100}",
        r"\bSERMO[^.—]{0,100}",
        r"\bCATECH(?:ESIS|\.)[^.—]{0,100}",
    ]:
        match = re.search(pattern, lemma_raw, flags=re.IGNORECASE)
        if match:
            candidates.append(match.group(0).strip(" ."))
    words = re.findall(r"[A-Za-zÀ-ÿ]{5,}", lemma_raw)
    if len(words) >= 3:
        candidates.append(" ".join(words[:8]))
    deduped = []
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        key = candidate.lower()
        if candidate and key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped[:4]


def build_helper_request(data):
    entries = []
    refs = data.get("refs", [])
    for entry in data.get("entries", []):
        ref = ref_for_entry(refs, entry["entry_key"])
        if not ref or ref.get("page_ref_int") is None:
            continue
        entries.append(
            {
                "entry_id": entry["entry_key"].replace(":", "_").lower(),
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": compact_query_names(entry.get("lemma_raw")),
                "page_hints": [ref.get("page_ref_raw") or ref.get("ref_raw")],
                "page_hint_ints": [ref["page_ref_int"]],
                "context_raw": entry.get("entry_raw"),
            }
        )
    request = {
        "volume_id": "PG050",
        "source_root": SOURCE_ROOT,
        "options": {"top_k": 5, "adjacency_window": 2, "editorial_page_estimator": True},
        "entries": entries,
    }
    HELPER_REQUEST.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")


def helper_map():
    if not HELPER_OUTPUT.exists():
        return {}
    data = json.loads(HELPER_OUTPUT.read_text())
    return {entry.get("entry_id"): entry for entry in data.get("entries", [])}


def apply_helper(data):
    helpers = helper_map()
    refs_by_key = {ref["entry_key"]: ref for ref in data.get("refs", [])}
    for entry in data.get("entries", []):
        helper_id = entry["entry_key"].replace(":", "_").lower()
        helper = helpers.get(helper_id)
        ref = refs_by_key.get(entry["entry_key"])
        if not helper or not ref:
            continue
        best = helper.get("best_candidate") or {}
        status = helper.get("status")
        role = best.get("candidate_role")
        probability = best.get("probability")
        reason = best.get("reason_summary")
        evidence = {
            "helper_status": status,
            "helper_entry_id": helper_id,
            "helper_candidate_role": role,
            "helper_reason_summary": reason,
            "helper_top_candidates": [
                {
                    "file": c.get("file"),
                    "probability": c.get("probability"),
                    "candidate_role": c.get("candidate_role"),
                    "reason_summary": c.get("reason_summary"),
                }
                for c in helper.get("candidates", [])[:3]
            ],
        }
        entry.setdefault("raw_json", {})["locator_helper"] = evidence
        ref.setdefault("raw_json", {})["locator_helper"] = evidence
        if (
            status == "resolved"
            and role == "target_candidate"
            and isinstance(probability, (int, float))
            and probability >= 0.55
            and best.get("file")
        ):
            entry["target_file_best"] = best["file"]
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = probability
        else:
            entry["target_file_best"] = None
            ref["target_file"] = None
            ref["target_file_probability"] = None


def update_todo():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    todo = {
        "volume_id": "PG050",
        "updated_at": now,
        "current_focus": "PG050 payload repaired and ready for import validation",
        "completed": [
            "inspected OCR files 430-434 through read_ocr_page_text.py",
            "confirmed Ordo Rerum section spans OCR files 430-434",
            "fixed verified line-break hyphen artifacts in entries 0008 and 0043",
            "rebuilt complete helper request for all paginated entries",
            "merged reliable helper target candidates where available",
        ],
        "pending": ["run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Helper candidates are stored in raw_json.locator_helper.",
            "Only resolved target_candidate hits with probability >= 0.55 were promoted to target_file fields.",
        ],
    }
    TODO.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


def main():
    data = json.loads(PAYLOAD.read_text())
    data = fix_hyphens(data)
    for entry in data.get("entries", []):
        entry["section_start_file"] = SECTION_FILE_START
        for key in ["lemma_norm", "lemma_sort"]:
            entry[key] = ascii_norm(entry.get("lemma_raw"))
    for ref in data.get("refs", []):
        ref["section_start_file"] = SECTION_FILE_START
    build_helper_request(data)
    apply_helper(data)
    data["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    notes = data.setdefault("notes", [])
    note = (
        "PG050 rerun fixed OCR line-break hyphen artifacts verified in files 430-432 "
        "and rebuilt helper locator evidence for paginated Ordo Rerum entries."
    )
    if note not in notes:
        notes.append(note)
    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    update_todo()


if __name__ == "__main__":
    main()
