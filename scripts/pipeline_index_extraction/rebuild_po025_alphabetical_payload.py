"""Usage: python scripts/pipeline_index_extraction/rebuild_po025_alphabetical_payload.py

Rebuild the PO025 alphabetical payload checkpoint by fixing invalid hyphen
artifacts and adding the omitted `INDEX DES CITATIONS DES ÉCRITURES` section.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PO025_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PO025_helper_request.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO025"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

SECTION_KEY = "PO025:alpha:scripture_index:006"
ENTRY_KEY = "PO025:entry:0007"
SECTION_FILE_START = str(
    ROOT / "teste/PO025/text/e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt"
)
SECTION_FILE_END = str(
    ROOT / "teste/PO025/text/e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-623.txt"
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def fix_hyphen_artifacts(payload: dict) -> None:
    replacements = {
        "510_2-3-": "510_2-3",
        "2-3-": "2-3",
    }
    for entry in payload["entries"]:
        if entry["entry_key"] in {"PO025:entry:0002", "PO025:entry:0005"}:
            for old, new in replacements.items():
                entry["entry_raw"] = entry["entry_raw"].replace(old, new)
                if entry.get("context_raw"):
                    entry["context_raw"] = entry["context_raw"].replace(old, new)
    for ref in payload["refs"]:
        if ref["entry_key"] in {"PO025:entry:0002", "PO025:entry:0005"} and ref["page_ref_int"] == 510:
            ref["ref_raw"] = "510_2-3"
            ref["line_ref_raw"] = "2-3"
            ref["raw_json"]["source_token"] = "510_2-3"


def ensure_section(payload: dict) -> None:
    if any(section["section_key"] == SECTION_KEY for section in payload["sections"]):
        return
    payload["sections"].append(
        {
            "section_key": SECTION_KEY,
            "volume_id": "PO025",
            "work_key": None,
            "section_order": 6,
            "section_kind": "scripture_index",
            "heading_raw": "INDEX DES CITATIONS DES ÉCRITURES",
            "heading_norm": "index des citations des ecritures",
            "heading_letter": None,
            "page_start": 612,
            "page_end": 613,
            "file_start": SECTION_FILE_START,
            "file_end": SECTION_FILE_END,
            "confidence": 0.96,
            "raw_json": {
                "source": "OCR files 622-623; Euchologium scripture-citation index over printed pages 612-613.",
                "section_title_tokens": ["INDEX DES CITATIONS DES ÉCRITURES"],
                "section_kind_reason": "Real closing scripture index omitted from the invalid checkpoint.",
            },
        }
    )


def ensure_entry(payload: dict) -> None:
    if any(entry["entry_key"] == ENTRY_KEY for entry in payload["entries"]):
        return
    payload["entries"].append(
        {
            "entry_key": ENTRY_KEY,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": 1,
            "entry_kind": "scripture_citation",
            "lemma_raw": "GENESE I, 11-12",
            "lemma_display": "GENESE I, 11-12",
            "lemma_norm": "genese i, 11-12",
            "lemma_sort": "genese i, 11-12",
            "entry_raw": "GENESE. I, 11-12 . . . . . . 17_10-10",
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": 17,
            "section_start_file": SECTION_FILE_START,
            "editorial_anchor_file": None,
            "target_file_best": None,
            "confidence": 0.32,
            "raw_json": {
                "section_kind": "scripture_index",
                "source_entry_id": "po025_scripture_index_622",
                "source_section_key": SECTION_KEY,
                "helper": {
                    "status": "unresolved",
                    "candidate_role": None,
                    "reason_summary": None,
                    "best_candidate": None,
                    "top_candidates": [],
                    "debug": {
                        "normalized_queries": ["genese", "i 11 12", "17_10 10"],
                        "page_hints_used": [
                            17,
                            22,
                            41,
                            53,
                            75,
                            92,
                            100,
                            116,
                            117,
                            121,
                            124,
                            125,
                            136,
                            139,
                            144,
                            150,
                            156,
                            163,
                            174,
                            176,
                        ],
                    },
                },
                "locator_attempts": [
                    "Reviewed source files 622-623 directly.",
                    "Checked available e1a2562f OCR span; only files 561-629 are present locally, so the cited page 17 is not materialized in this OCR family.",
                    "Left target_file_best null explicitly instead of inheriting a false anchor.",
                ],
            },
        }
    )


def ensure_ref(payload: dict) -> None:
    if any(ref["entry_key"] == ENTRY_KEY and ref["ref_order"] == 1 for ref in payload["refs"]):
        return
    payload["refs"].append(
        {
            "entry_key": ENTRY_KEY,
            "ref_order": 1,
            "ref_kind": "editorial_page_line",
            "ref_raw": "17_10-10",
            "page_ref_raw": "17",
            "page_ref_int": 17,
            "page_ref_col": None,
            "line_ref_raw": "10-10",
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "section_start_file": SECTION_FILE_START,
            "editorial_anchor_file": None,
            "confidence": 0.0,
            "raw_json": {
                "source_token": "17_10-10",
                "source_entry_id": "po025_scripture_index_622",
                "section_kind": "scripture_index",
                "locator_attempts": [
                    "No file in the local e1a2562f OCR subset materializes printed page 17.",
                    "Retained the printed locator exactly and kept the material anchor unresolved.",
                ],
            },
        }
    )


def ensure_scripture_ref(payload: dict) -> None:
    if any(ref["entry_key"] == ENTRY_KEY and ref["ref_order"] == 1 for ref in payload["scripture_refs"]):
        return
    payload["scripture_refs"].append(
        {
            "entry_key": ENTRY_KEY,
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "GENESE. I, 11-12",
            "book_raw": "GENESE",
            "book_norm": "Gênesis",
            "chapter_start": 1,
            "verse_start": 11,
            "chapter_end": 1,
            "verse_end": 12,
            "is_range": True,
            "confidence": 0.85,
            "raw_json": {
                "source_entry_id": "po025_scripture_index_622",
                "source_section_key": SECTION_KEY,
                "source_token": "GENESE. I, 11-12 . . . . . . 17_10-10",
            },
        }
    )


def update_coverage_and_notes(payload: dict) -> None:
    evidence_files = payload["coverage"].setdefault("evidence_files", [])
    for item in [SECTION_FILE_START, SECTION_FILE_END]:
        if item not in evidence_files:
            evidence_files.append(item)
    reason = payload["coverage"].get("entries_status_reason", "")
    extra = (
        " The rerun also reinstated the omitted Euchologium scripture index section "
        "from files 622-623 and removed the invalid trailing-hyphen artifacts from the Mary refs."
    )
    if extra.strip() not in reason:
        payload["coverage"]["entries_status_reason"] = f"{reason}{extra}".strip()
    notes = payload.setdefault("notes", [])
    additions = [
        "The rerun removed the invalid `510_2-3-` line-break artifacts from both Mary entries and their refs.",
        "A conservative `INDEX DES CITATIONS DES ÉCRITURES` section was restored from files 622-623; its first citation stays materially unresolved because the cited printed page 17 is outside the locally available e1a2562f OCR span.",
    ]
    for note in additions:
        if note not in notes:
            notes.append(note)


def update_helper_request() -> None:
    helper_request = load_json(HELPER_REQUEST_PATH)
    for entry in helper_request.get("entries", []):
        if entry["entry_id"] in {"po025_onomastic_mary_176", "po025_onomastic_mary_816"}:
            entry["context_raw"] = entry["context_raw"].replace("510_2-3-", "510_2-3")
    save_json(HELPER_REQUEST_PATH, helper_request)


def write_todo() -> None:
    save_json(
        TODO_PATH,
        {
            "volume_id": "PO025",
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "current_focus": "Payload rebuilt after validation failure; restore omitted scripture-index section and remove trailing-hyphen artifacts.",
            "completed": [
                "Rechecked prior validation failure against refs[20] and refs[34].",
                "Removed invalid trailing hyphen artifacts from Mary refs.",
                "Restored the omitted `INDEX DES CITATIONS DES ÉCRITURES` section from files 622-623.",
            ],
            "pending": [
                "Validate final payload with import_alphabetical_index_json.py.",
            ],
            "blocked": [
                "Material anchor for `GENESE. I, 11-12` remains unresolved because the cited printed page 17 is outside the locally available `e1a2562f` OCR file span.",
            ],
            "notes": [
                "Do not fabricate a target_file when the cited early pages are absent from the current OCR family.",
            ],
        },
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fix_hyphen_artifacts(payload)
    ensure_section(payload)
    ensure_entry(payload)
    ensure_ref(payload)
    ensure_scripture_ref(payload)
    update_coverage_and_notes(payload)
    save_json(PAYLOAD_PATH, payload)
    update_helper_request()
    write_todo()


if __name__ == "__main__":
    main()
