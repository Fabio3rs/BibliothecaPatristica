#!/usr/bin/env python3
"""Repair PG052 alphabetical payload after OCR line-break hyphen validation.

Run from the repository root:
    python scripts/pipeline_index_extraction/repair_pg052_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG052_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG052_helper_request.json"
TODO = ROOT / "data/intermediate_payloads/PG052/todo.json"
SOURCE_FILE_471 = (
    "/homessddata/Projects/pdfocr/teste/PG052/text/"
    "60d05f52-038f-435d-89f7-361b478c898b-471.txt"
)
TARGET_OPUSCULA = (
    "/homessddata/Projects/pdfocr/teste/PG052/text/"
    "b1789d47-c607-4fd5-b126-cb3f835c88f6-012.txt"
)


def norm_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    text = re.sub(r"[^0-9A-Za-z]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def repair_entry_0061(payload: dict) -> None:
    entry = next(e for e in payload["entries"] if e["entry_key"] == "PG052:entry:0061")
    changed = False
    for field in ("lemma_raw", "lemma_display", "entry_raw"):
        if "Pe- trus" in entry[field]:
            entry[field] = entry[field].replace("Pe- trus", "Petrus")
            changed = True
    if changed:
        entry["lemma_norm"] = norm_text(entry["lemma_raw"])
        entry["lemma_sort"] = entry["lemma_norm"]
    raw_json = entry.setdefault("raw_json", {})
    raw_json["helper"] = clean_helper_debug(raw_json.get("helper"))
    raw_json["rerun_repair"] = {
        "reason": "OCR line-break hyphenation verified in PG052 file 471: the split word continues as Petrus in the next column block.",
        "evidence_file": SOURCE_FILE_471,
        "repair": "Merged split Petrus and removed the line-break hyphen.",
    }
    for ref in payload["refs"]:
        if ref["entry_key"] == "PG052:entry:0061":
            if "raw_json" in ref:
                ref["raw_json"]["helper"] = clean_helper_debug(ref["raw_json"].get("helper"))
            ref.setdefault("raw_json", {})["rerun_repair"] = {
                "reason": "Entry text repaired for OCR line-break hyphenation; material locator remains the printed page 371 line.",
                "evidence_file": SOURCE_FILE_471,
            }


def repair_entry_0062(payload: dict) -> None:
    entry = next(e for e in payload["entries"] if e["entry_key"] == "PG052:entry:0062")
    entry["entry_kind"] = "heading_group"
    entry["inferred_printed_page"] = 387
    entry["target_file_best"] = TARGET_OPUSCULA
    entry["confidence"] = min(entry.get("confidence") or 1.0, 0.97)
    entry["raw_json"]["source_page_hints"] = ["387-398"]
    entry["raw_json"]["source_page_hint_ints"] = [387, 398]
    entry["raw_json"]["rerun_repair"] = {
        "reason": "OCR file 471 prints this heading as OPUSCULA ... 387-398; prior ref 371 was inherited from the previous MONITUM entry.",
        "index_evidence_file": SOURCE_FILE_471,
        "target_evidence_file": TARGET_OPUSCULA,
        "target_evidence": "The target file contains the matching DE MOTIBUS CONSTANTINOPOLITANIS heading.",
    }
    for ref in payload["refs"]:
        if ref["entry_key"] == "PG052:entry:0062":
            ref.update(
                {
                    "ref_kind": "editorial_range",
                    "ref_raw": "387-398",
                    "page_ref_raw": "387",
                    "page_ref_int": 387,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": "387",
                    "range_end_raw": "398",
                    "target_file": TARGET_OPUSCULA,
                    "target_file_probability": 0.97,
                    "confidence": 0.97,
                }
            )
            ref["raw_json"] = {
                "source_entry_id": "PG052:entry:0062",
                "rerun_repair": {
                    "reason": "Corrected printed locator from inherited 371 to the visible range 387-398.",
                    "index_evidence_file": SOURCE_FILE_471,
                    "target_evidence_file": TARGET_OPUSCULA,
                },
            }


def update_notes(payload: dict) -> None:
    notes = payload.setdefault("notes", [])
    note = (
        "PG052 rerun repaired the validation-blocking OCR line-break hyphen "
        "in entry PG052:entry:0061 and corrected the following OPUSCULA "
        "heading reference from inherited 371 to visible range 387-398."
    )
    if note not in notes:
        notes.append(note)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_helper_debug(value):
    if isinstance(value, dict):
        return {k: clean_helper_debug(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_helper_debug(v) for v in value]
    if isinstance(value, str):
        return value.replace("Pe- trus", "Petrus").replace("pe trus", "petrus")
    return value


def strip_locator_suffix(text: str) -> str:
    text = re.sub(r"\s+(?:Ibid\.|ibid\.)$", "", text).strip()
    text = re.sub(r"\s+\d{1,4}\s*[-–]\s*\d{1,4}\s*$", "", text).strip()
    text = re.sub(r"\s+\d{1,4}\s*$", "", text).strip()
    return text


def query_names_for(entry: dict) -> list[str]:
    raw = entry.get("lemma_raw") or entry.get("entry_raw") or ""
    primary = strip_locator_suffix(raw)
    before_dash = strip_locator_suffix(re.split(r"\s+[—-]\s+", raw, maxsplit=1)[0])
    names = []
    for candidate in (primary, before_dash):
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and candidate not in names:
            names.append(candidate[:240])
    if not names and raw:
        names.append(raw[:240])
    return names


def page_hints_for(entry: dict) -> tuple[list[str], list[int]]:
    hints = []
    ints = []
    raw_json = entry.get("raw_json") or {}
    for hint in raw_json.get("source_page_hints") or []:
        value = str(hint)
        if value not in hints:
            hints.append(value)
    for value in raw_json.get("source_page_hint_ints") or []:
        if isinstance(value, int) and value not in ints:
            ints.append(value)
    if not hints:
        for match in re.findall(r"\b\d{1,4}(?:\s*[-–]\s*\d{1,4})?\b", entry.get("entry_raw") or ""):
            if match not in hints:
                hints.append(match)
    if not ints:
        for hint in hints:
            for num in re.findall(r"\d{1,4}", hint):
                value = int(num)
                if value not in ints:
                    ints.append(value)
    if not hints and entry.get("inferred_printed_page") is not None:
        hints.append(str(entry["inferred_printed_page"]))
    if not ints and entry.get("inferred_printed_page") is not None:
        ints.append(entry["inferred_printed_page"])
    return hints, ints


def write_helper_request(payload: dict) -> None:
    entries = []
    for entry in payload["entries"]:
        page_hints, page_hint_ints = page_hints_for(entry)
        entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": query_names_for(entry),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry.get("entry_raw"),
            }
        )
    request = {
        "volume_id": "PG052",
        "source_root": "/homessddata/Projects/pdfocr/teste/PG052/text",
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }
    HELPER_REQUEST.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_todo() -> None:
    TODO.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": "PG052",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "current_focus": "Payload repaired after PG052 validation failure; final validation pending.",
        "completed": [
            "Read previous validation failure for entries[61] and refs[61]",
            "Verified Pe- / trus line-break hyphenation against OCR reader output for file 471",
            "Corrected PG052:entry:0062 material reference from inherited 371 to visible 387-398",
            "Rebuilt helper request for all payload entries",
        ],
        "pending": ["Run index_target_locator.py", "Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "The remaining OCR text is preserved literally; only proven line-break hyphenation was merged.",
            "OPUSCULA target evidence is the matching heading in b1789d47-c607-4fd5-b126-cb3f835c88f6-012.txt.",
        ],
    }
    TODO.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    repair_entry_0061(payload)
    repair_entry_0062(payload)
    update_notes(payload)
    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_helper_request(payload)
    write_todo()


if __name__ == "__main__":
    main()
