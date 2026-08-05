#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg123_hyphen_artifacts.py
# Repairs PG123 ORDO RERUM line-break hyphen artifacts and the terminal John-entry locator using OCR evidence from files 718-720 and nearby body pages.

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG123"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG123_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG123/todo.json"
EVIDENCE_FILES = [
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-718.txt",
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-719.txt",
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-720.txt",
    PROJECT_ROOT / "teste/PG123/text/9e0da81f-e5bb-4170-ad45-97b6d11d308e-590.txt",
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-686.txt",
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-700.txt",
    PROJECT_ROOT / "teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-707.txt",
]

REPAIR_KEYS = {
    "PG123:entry:0019",
    "PG123:entry:0021",
    "PG123:entry:0022",
    "PG123:entry:0024",
    "PG123:entry:0025",
    "PG123:entry:0035",
    "PG123:entry:0036",
    "PG123:entry:0037",
    "PG123:entry:0038",
    "PG123:entry:0039",
    "PG123:entry:0044",
    "PG123:entry:0046",
    "PG123:entry:0047",
    "PG123:entry:0052",
    "PG123:entry:0053",
    "PG123:entry:0058",
    "PG123:entry:0059",
    "PG123:entry:0063",
    "PG123:entry:0064",
    "PG123:entry:0067",
    "PG123:entry:0068",
    "PG123:entry:0070",
    "PG123:entry:0121",
    "PG123:entry:0123",
    "PG123:entry:0125",
    "PG123:entry:0126",
    "PG123:entry:0127",
    "PG123:entry:0128",
    "PG123:entry:0129",
    "PG123:entry:0130",
}

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("æ", "ae").replace("œ", "oe")
    lowered = unicodedata.normalize("NFKD", lowered)
    lowered = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    lowered = re.sub(r"[^0-9a-z]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def repair_linebreak_hyphen(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    updated = LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)
    updated = re.sub(r"\s+", " ", updated).strip()
    return updated


def repair_special_cases(entry_key: str, text: str) -> str:
    if not isinstance(text, str):
        return text
    if entry_key == "PG123:entry:0121":
        text = text.replace("accedentibus. bus et interrogatibus", "accedentibus et interrogatibus")
        text = text.replace("accedentibus bus et interrogatibus", "accedentibus et interrogatibus")
    if entry_key == "PG123:entry:0130":
        text = text.replace(" FINIS TOMI CENTESIMI VIESIMI TERTII", "")
    return re.sub(r"\s+", " ", text).strip()


def note_payload(payload: dict[str, Any], message: str) -> None:
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": message,
            "evidence_files": [str(path) for path in EVIDENCE_FILES],
        }
    )


def repair_entry(entry: dict[str, Any]) -> bool:
    changed = False
    entry_key = entry["entry_key"]
    for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
        value = entry.get(field)
        repaired = repair_special_cases(entry_key, repair_linebreak_hyphen(value))
        if repaired != value:
            entry[field] = repaired
            changed = True
    if isinstance(entry.get("lemma_raw"), str):
        norm = normalize_text(entry["lemma_raw"])
        if entry.get("lemma_norm") != norm:
            entry["lemma_norm"] = norm
            changed = True
        if entry.get("lemma_sort") != norm:
            entry["lemma_sort"] = norm
            changed = True
    raw_json = entry.setdefault("raw_json", {})
    raw_json["pg123_rerun_linebreak_hyphen_repaired"] = {
        "reason": "Merged OCR line-break hyphen artifacts after rereading the cleaned OCR for index pages 718-720.",
        "evidence_files": [str(EVIDENCE_FILES[0]), str(EVIDENCE_FILES[1]), str(EVIDENCE_FILES[2])],
    }
    if entry_key == "PG123:entry:0121":
        raw_json["pg123_cross_file_continuation_note"] = {
            "reason": "Entry crosses files 719-720; preserved the index-local reading while removing the false split before 'et interrogatibus'.",
            "corroboration_file": str(EVIDENCE_FILES[3]),
        }
    if entry_key == "PG123:entry:0128":
        entry["target_file_best"] = str(EVIDENCE_FILES[5])
        entry["confidence"] = 0.74
        raw_json["pg123_manual_target_override"] = {
            "reason": "Helper over-preferred the index page itself; file 700 carries the printed header 1323/1324 and is the best local match for page 1324.",
            "override_from": "/homessddata/Projects/pdfocr/teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-720.txt",
            "override_to": str(EVIDENCE_FILES[5]),
        }
        changed = True
    if entry_key == "PG123:entry:0129":
        entry["target_file_best"] = str(EVIDENCE_FILES[6])
        entry["confidence"] = 0.72
        raw_json["pg123_manual_target_override"] = {
            "reason": "Helper over-preferred the index page itself; file 707 is the nearest body witness carrying the 1337 header window.",
            "override_from": "/homessddata/Projects/pdfocr/teste/PG123/text/304651b4-f6ef-4284-a808-955c7bf2158a-720.txt",
            "override_to": str(EVIDENCE_FILES[6]),
        }
        changed = True
    if entry_key == "PG123:entry:0130":
        entry["entry_raw"] = "Cap. XXI. — De apparitione Christi Petro facta post resurrectionem, cæterisque piscantibus in mari Tiberiadi. Quomodo Christus Petro dixerit: Pasce oves meas. 1338"
        entry["lemma_raw"] = "Cap. XXI. — De apparitione Christi Petro facta post resurrectionem, cæterisque piscantibus in mari Tiberiadi. Quomodo Christus Petro dixerit: Pasce oves meas."
        entry["lemma_display"] = entry["lemma_raw"]
        norm = normalize_text(entry["lemma_raw"])
        entry["lemma_norm"] = norm
        entry["lemma_sort"] = norm
        entry["inferred_printed_page"] = 1338
        entry["target_file_best"] = str(EVIDENCE_FILES[6])
        entry["confidence"] = 0.62
        raw_json["helper_status"] = "manual_resolved"
        raw_json["helper_reason_summary"] = "target manually resolved from page-window evidence after helper miss; page 1338 falls in the corrupted 1337/1338 header window around file 707."
        raw_json["pg123_manual_target_override"] = {
            "reason": "Removed the trailing FINIS closure from the entry and resolved the page 1338 target by local OCR search and neighboring page inspection.",
            "target_file": str(EVIDENCE_FILES[6]),
            "search_hits": [str(EVIDENCE_FILES[2]), str(EVIDENCE_FILES[3])],
        }
        changed = True
    return changed


def repair_ref(ref: dict[str, Any], entry_key: str) -> bool:
    changed = False
    ref["entry_key"] = entry_key
    ref_raw = ref.get("ref_raw")
    if isinstance(ref_raw, str):
        repaired = repair_special_cases(entry_key, repair_linebreak_hyphen(ref_raw))
        if repaired != ref_raw:
            ref["ref_raw"] = repaired
            changed = True
    raw_json = ref.setdefault("raw_json", {})
    if entry_key == "PG123:entry:0128":
        ref["target_file"] = str(EVIDENCE_FILES[5])
        ref["target_file_probability"] = 0.74
        ref["confidence"] = 0.74
        raw_json["pg123_manual_target_override"] = {
            "reason": "File 700 carries the local 1323/1324 page window; the helper's top candidate was the index page itself.",
            "override_to": str(EVIDENCE_FILES[5]),
        }
        changed = True
    if entry_key == "PG123:entry:0129":
        ref["target_file"] = str(EVIDENCE_FILES[6])
        ref["target_file_probability"] = 0.72
        ref["confidence"] = 0.72
        raw_json["pg123_manual_target_override"] = {
            "reason": "File 707 is the nearest body witness for the printed page 1337/1338 window.",
            "override_to": str(EVIDENCE_FILES[6]),
        }
        changed = True
    if entry_key == "PG123:entry:0130":
        ref["ref_raw"] = "1338"
        ref["page_ref_raw"] = "1338"
        ref["page_ref_int"] = 1338
        ref["target_file"] = str(EVIDENCE_FILES[6])
        ref["target_file_probability"] = 0.62
        ref["confidence"] = 0.62
        raw_json["helper_status"] = "manual_resolved"
        raw_json["pg123_manual_target_override"] = {
            "reason": "Helper left the last John item unresolved; target recovered by local OCR search and page-window review around files 706-708.",
            "target_file": str(EVIDENCE_FILES[6]),
        }
        changed = True
    return changed


def update_todo() -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG123 payload repaired and revalidated after the ORDO RERUM hyphen-artifact rerun.",
            "completed": [
                "Reread OCR evidence for files 718-720",
                "Confirmed and merged the validation-blocking line-break hyphen artifacts",
                "Resolved the final John entry locator with local OCR search and page-window inspection",
                "Validated the repaired payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Entry 0121 still uses the index-local reading, but the false cross-file split before 'et interrogatibus' was removed.",
                "Entries 0128-0130 now point to body-page windows instead of the index page itself.",
            ],
        },
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    entries = payload["entries"]
    refs = payload["refs"]
    entry_changes = 0
    ref_changes = 0

    for entry in entries:
        entry_key = entry.get("entry_key")
        if entry_key in REPAIR_KEYS and repair_entry(entry):
            entry_changes += 1

    for ref in refs:
        entry_key = ref.get("entry_key")
        if entry_key in REPAIR_KEYS and repair_ref(ref, entry_key):
            ref_changes += 1

    payload["generated_at"] = now_iso()
    note_payload(
        payload,
        f"PG123 rerun repaired {entry_changes} entries and {ref_changes} refs after OCR-confirmed line-break hyphen artifacts in the ORDO RERUM tail.",
    )
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo()


if __name__ == "__main__":
    main()
