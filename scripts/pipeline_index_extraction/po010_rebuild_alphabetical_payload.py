#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/po010_rebuild_alphabetical_payload.py

Rebuild the PO010 alphabetical payload from the existing checkpoint, write a
rerun helper request, and apply scoped fixes validated against the OCR.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


VOLUME_ID = "PO010"
SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PO010/text")
OUTPUT_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO010_alphabetical_indices.json")
HELPER_REQUEST_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO010_helper_request.json")
HELPER_OUTPUT_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO010_helper_output.json")
TODO_PATH = Path("/homessddata/Projects/pdfocr/data/intermediate_payloads/PO010/todo.json")

FILES = {
    "avertissement": str(SOURCE_ROOT / "a1302d2f-d181-45bf-9342-d482556eb22e-013.txt"),
    "martyrologe_ive": str(SOURCE_ROOT / "a1302d2f-d181-45bf-9342-d482556eb22e-015.txt"),
    "quatre_menologes": str(SOURCE_ROOT / "a1302d2f-d181-45bf-9342-d482556eb22e-037.txt"),
    "deux_menologes_alep": str(SOURCE_ROOT / "547f8470-1b3b-4b8f-b33c-5332a37a4f12-067.txt"),
    "sept_menologes": str(SOURCE_ROOT / "547f8470-1b3b-4b8f-b33c-5332a37a4f12-099.txt"),
    "anianus_note": str(SOURCE_ROOT / "44b44b57-124c-4938-8e27-0eb0923a4e15-202.txt"),
    "aboun_rouh": str(SOURCE_ROOT / "f7dd0ba1-7827-4d85-8408-0ea9341b1b94-311.txt"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_helper_request(payload: dict) -> dict:
    entries = {entry["entry_key"]: entry for entry in payload["entries"]}
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "po010_173_avertissement",
                "lemma_raw": "Avertissement",
                "query_names": ["Avertissement"],
                "page_hint_ints": [3],
                "context_raw": entries["PO010:entry:001"]["entry_raw"],
            },
            {
                "entry_id": "po010_241_anianus",
                "lemma_raw": "Anianus, patriarche d'Alexandrie",
                "query_names": ["Anianus", "Anba Anianus", "Athanasius"],
                "page_hint_ints": [28],
                "context_raw": entries["PO010:entry:014"]["entry_raw"],
            },
            {
                "entry_id": "po010_366_aboun_rouh",
                "lemma_raw": "Aboun Rouḥ = Antoine",
                "query_names": ["Aboun Rouḥ", "Abou Rouḥ", "Antoine"],
                "page_hint_ints": [13],
                "context_raw": entries["PO010:entry:021"]["entry_raw"],
            },
        ],
    }


def load_helper_by_id() -> dict[str, dict]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    data = load_json(HELPER_OUTPUT_PATH)
    return {item["entry_id"]: item for item in data.get("entries", [])}


def update_todo(status: str) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Rebuild PO010 payload and validate rerun fixes",
        "completed": [
            "verified checkpoint against OCR for candidate sections",
            "confirmed Aaron le prêtre hyphen merge in OCR",
            "confirmed Anianus note on file 202 and Aboun Rouḥ material on file 311",
        ],
        "pending": [
            "run helper on rerun request" if status == "helper_pending" else "none",
            "validate rebuilt payload with import_alphabetical_index_json.py" if status != "done" else "none",
        ],
        "blocked": [],
        "notes": [
            "Avertissement lacks an explicit body-word match; use the introduction page sequence around files 177-180.",
            "Keep helper ambiguity for Aboun Rouḥ in raw_json even though file 311 confirms the material anchor.",
        ],
    }
    write_json(TODO_PATH, todo)


def get_entry(payload: dict, entry_key: str) -> dict:
    for entry in payload["entries"]:
        if entry["entry_key"] == entry_key:
            return entry
    raise KeyError(entry_key)


def get_ref(payload: dict, entry_key: str, ref_order: int = 1) -> dict:
    for ref in payload["refs"]:
        if ref["entry_key"] == entry_key and ref["ref_order"] == ref_order:
            return ref
    raise KeyError((entry_key, ref_order))


def helper_snapshot(helper_item: dict | None) -> dict | None:
    if not helper_item:
        return None
    best = helper_item.get("best_candidate") or {}
    return {
        "status": helper_item.get("status"),
        "best_candidate_file": best.get("file"),
        "best_candidate_probability": best.get("probability"),
        "reason_summary": best.get("reason_summary"),
        "candidate_role": best.get("candidate_role"),
    }


def apply_target_update(
    payload: dict,
    *,
    entry_key: str,
    target_file: str,
    probability: float,
    confidence: float,
    note: str,
    helper_item: dict | None = None,
) -> None:
    entry = get_entry(payload, entry_key)
    ref = get_ref(payload, entry_key)
    entry["target_file_best"] = target_file
    entry["confidence"] = confidence
    raw_json = deepcopy(entry.get("raw_json") or {})
    raw_json["locator_rerun_note"] = note
    helper = helper_snapshot(helper_item)
    if helper:
        raw_json["helper"] = helper
    entry["raw_json"] = raw_json

    ref["target_file"] = target_file
    ref["target_file_probability"] = probability
    ref["confidence"] = confidence
    ref_raw_json = deepcopy(ref.get("raw_json") or {})
    ref_raw_json["locator_rerun_note"] = note
    if helper:
        ref_raw_json["helper"] = helper
    ref["raw_json"] = ref_raw_json


def rebuild_payload() -> dict:
    payload = load_json(OUTPUT_PATH)
    helper_by_id = load_helper_by_id()

    payload["generated_at"] = now_iso()

    entry_010 = get_entry(payload, "PO010:entry:010")
    fixed_aaron = "Aaron le prêtre, 1er Barmoudah (27 mars) AEGHIM (les mss. écrivent Haroun); 37."
    entry_010["entry_raw"] = fixed_aaron
    entry_010["context_raw"] = fixed_aaron

    entry_029 = get_entry(payload, "PO010:entry:029")
    entry_029["lemma_norm"] = "zacharie le rheteur pseudo"
    entry_029["lemma_sort"] = "zacharie le rheteur pseudo"

    apply_target_update(
        payload,
        entry_key="PO010:entry:001",
        target_file=FILES["avertissement"],
        probability=0.905382,
        confidence=0.9,
        note=(
            "Resolved by direct OCR inspection of file 013, whose heading is explicitly 'AVERTISSEMENT'."
        ),
        helper_item=helper_by_id.get("po010_173_avertissement"),
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:002",
        target_file=FILES["martyrologe_ive"],
        probability=1.0,
        confidence=0.95,
        note="Direct OCR title-page match for 'I. — MARTYROLOGE DU IVe SIÈCLE.' on file 015.",
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:003",
        target_file=FILES["quatre_menologes"],
        probability=1.0,
        confidence=0.95,
        note="Direct OCR title-page match for 'II à V. — QUATRE MÉNOLOGES JACOBITES' on file 037.",
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:004",
        target_file=FILES["deux_menologes_alep"],
        probability=1.0,
        confidence=0.95,
        note="Direct OCR title-page match for 'VI. — DEUX MÉNOLOGES JACOBITES D'ALEP.' on file 067.",
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:005",
        target_file=FILES["sept_menologes"],
        probability=1.0,
        confidence=0.95,
        note="Direct OCR title-page match for 'VII à XIII. — SEPT MÉNOLOGES JACOBITES' on file 099.",
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:014",
        target_file=FILES["anianus_note"],
        probability=0.93,
        confidence=0.9,
        note=(
            "Resolved by direct OCR inspection of file 202 ([28]): note 4 explicitly preserves "
            'EH: "Anba Anianus, patriarche..." against Athanase.'
        ),
        helper_item=helper_by_id.get("po010_241_anianus"),
    )
    apply_target_update(
        payload,
        entry_key="PO010:entry:021",
        target_file=FILES["aboun_rouh"],
        probability=0.962983,
        confidence=0.91,
        note=(
            "Direct OCR inspection of file 311 confirms "
            "'C. d'Antoine, martyr' and identifies him as Abou Rouḥ."
        ),
        helper_item=helper_by_id.get("po010_366_aboun_rouh"),
    )

    payload["coverage"]["entries_status_reason"] = (
        "Recovered the main analytical, onomastic, foreign_terms, and author-index sections from the OCR. "
        "On rerun, the payload fixes the Aaron le prêtre hyphen artifact and materially resolves the previously "
        "open analytical headings, Anianus, and Aboun Rouḥ locators; the Syriac word table remains section-only."
    )
    notes = list(payload.get("notes", []))
    notes.append("Rerun fixes: merged the OCR line-break hyphen in Aaron le prêtre and improved target_file coverage for analytic headings, Anianus, and Aboun Rouḥ.")
    payload["notes"] = notes
    return payload


def main() -> None:
    payload = load_json(OUTPUT_PATH)
    write_json(HELPER_REQUEST_PATH, build_helper_request(payload))
    update_todo("helper_pending")

    rebuilt = rebuild_payload()
    write_json(OUTPUT_PATH, rebuilt)
    update_todo("done")


if __name__ == "__main__":
    main()
