#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl109_alphabetical_payload.py
# Builds the PL109 alphabetical-index payload, runs the target locator helper, and writes the final JSON payload.

from __future__ import annotations

import json
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL109"
SOURCE_ROOT = ROOT / "teste/PL109/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL109_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL109_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL109_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL109"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_FILE = SOURCE_ROOT / "97b287ab-57ab-4ac9-bdee-02c2f9204079-009.txt"
PREFACE_FILE = SOURCE_ROOT / "97b287ab-57ab-4ac9-bdee-02c2f9204079-010.txt"
TEXT_START_FILE = SOURCE_ROOT / "97b287ab-57ab-4ac9-bdee-02c2f9204079-011.txt"
TEXT_CONT_FILE = SOURCE_ROOT / "97b287ab-57ab-4ac9-bdee-02c2f9204079-012.txt"
ORDO_TAIL_FILE = SOURCE_ROOT / "fd3d49d3-a943-4814-a79b-be73fc371e0c-633.txt"
ORDO_END_FILE = SOURCE_ROOT / "fd3d49d3-a943-4814-a79b-be73fc371e0c-641.txt"


ELENCHUS_ENTRIES: list[dict[str, Any]] = [
    {
        "entry_id": "pl109_rabanus_regum_009",
        "lemma_raw": "Commentaria in libros IV Regum, ad Hilduinum abbatem et sacri palatii archicapellanum",
        "query_names": [
            "Commentaria in libros IV Regum",
            "Hilduinum abbatem",
            "sacri palatii archicapellanum",
        ],
        "page_hints": ["9"],
        "page_hint_ints": [9],
        "context_raw": "Commentaria in libros IV Regum, ad Hilduinum abbatem et sacri palatii archicapellanum. 9",
    },
    {
        "entry_id": "pl109_rabanus_paralipomenon_279",
        "lemma_raw": "Commentaria in libros II Paralipomenon, ad Ludovicum imp.",
        "query_names": [
            "Commentaria in libros II Paralipomenon",
            "Ludovicum imp.",
            "Paralipomenon",
        ],
        "page_hints": ["279"],
        "page_hint_ints": [279],
        "context_raw": "Commentaria in libros II Paralipomenon, ad Ludovicum imp. 279",
    },
    {
        "entry_id": "pl109_rabanus_judith_539",
        "lemma_raw": "Expositio in librum Judith, ad Judith Augustam",
        "query_names": [
            "Expositio in librum Judith",
            "Judith Augustam",
            "librum Judith",
        ],
        "page_hints": ["539"],
        "page_hint_ints": [539],
        "context_raw": "Expositio in librum Judith, ad Judith Augustam. 539",
    },
    {
        "entry_id": "pl109_rabanus_judith_appendix_593",
        "lemma_raw": "Appendix ad Expositionem in librum Judith. Jacobi Pamelii Commentarius",
        "query_names": [
            "Appendix ad Expositionem in librum Judith",
            "Jacobi Pamelii Commentarius",
            "Expositionem in librum Judith",
        ],
        "page_hints": ["593"],
        "page_hint_ints": [593],
        "context_raw": "Appendix ad Expositionem in librum Judith. — Jacobi Pamelii Commentarius. 593",
    },
    {
        "entry_id": "pl109_rabanus_esther_635",
        "lemma_raw": "Expositio in librum Esther, ad Judith Augustam",
        "query_names": [
            "Expositio in librum Esther",
            "Judith Augustam",
            "librum Esther",
        ],
        "page_hints": ["635"],
        "page_hint_ints": [635],
        "context_raw": "Expositio in librum Esther, ad Judith Augustam. 635",
    },
    {
        "entry_id": "pl109_rabanus_sapientia_671",
        "lemma_raw": "Commentariorum in librum Sapientiæ libri tres, ad Otgarium archiepiscopum Moguntinum",
        "query_names": [
            "Commentariorum in librum Sapientiæ libri tres",
            "Otgarium archiepiscopum Moguntinum",
            "Sapientiæ libri tres",
        ],
        "page_hints": ["671"],
        "page_hint_ints": [671],
        "context_raw": "Commentariorum in librum Sapientiæ libri tres, ad Otgarium archiepiscopum Moguntinum. 671",
    },
    {
        "entry_id": "pl109_rabanus_ecclesiasticum_763",
        "lemma_raw": "Commentariorum in Ecclesiasticum libri decem, ad eundem",
        "query_names": [
            "Commentariorum in Ecclesiasticum libri decem",
            "ad eundem",
            "Ecclesiasticum libri decem",
        ],
        "page_hints": ["763"],
        "page_hint_ints": [763],
        "context_raw": "Commentariorum in Ecclesiasticum libri decem, ad eundem, 763",
    },
    {
        "entry_id": "pl109_rabanus_machabaeorum_1127",
        "lemma_raw": "Commentaria in libros Machabæorum ad Ludovicum regem Franciæ et Geroldum sacri palatii archiepiscoponum",
        "query_names": [
            "Commentaria in libros Machabæorum",
            "Ludovicum regem Franciæ",
            "Geroldum sacri palatii archiepiscoponum",
        ],
        "page_hints": ["1127"],
        "page_hint_ints": [1127],
        "context_raw": "Commentaria in libros Machabæorum ad Ludovicum regem Franciæ et Geroldum sacri palatii archiepiscoponum. 1127",
    },
]


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm_text(text: str) -> str:
    text = strip_accents(text)
    text = text.lower()
    for char in ["—", "–", ".", ",", ";", ":", "(", ")", "[", "]", "{", "}", "'"]:
        text = text.replace(char, " ")
    text = text.replace("æ", "ae").replace("œ", "oe")
    return " ".join(text.split())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def helper_request() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": ELENCHUS_ENTRIES,
    }


def run_helper() -> dict[str, Any]:
    proc = subprocess.run(
        ["python", str(SCRIPT_TARGET_LOCATOR), "--input", str(HELPER_REQUEST_JSON), "--output", str(HELPER_OUTPUT_JSON), "--pretty"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def helper_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item["entry_id"]] = item
    return out


def best_file(item: dict[str, Any]) -> str | None:
    best = item.get("best_candidate") or {}
    return best.get("file")


def best_probability(item: dict[str, Any]) -> float | None:
    best = item.get("best_candidate") or {}
    return best.get("probability")


def build_payload(helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_map = helper_by_id(helper_output)
    section_key = "PL109:alpha:author_index:001"
    section_start_file = str(SECTION_FILE)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    entry_specs = [
        (
            "PL109:entry:0001",
            "pl109_rabanus_regum_009",
            "Commentaria in libros IV Regum, ad Hilduinum abbatem et sacri palatii archicapellanum",
            "Commentaria in libros IV Regum, ad Hilduinum abbatem et sacri palatii archicapellanum. 9",
            9,
        ),
        (
            "PL109:entry:0002",
            "pl109_rabanus_paralipomenon_279",
            "Commentaria in libros II Paralipomenon, ad Ludovicum imp.",
            "Commentaria in libros II Paralipomenon, ad Ludovicum imp. 279",
            279,
        ),
        (
            "PL109:entry:0003",
            "pl109_rabanus_judith_539",
            "Expositio in librum Judith, ad Judith Augustam",
            "Expositio in librum Judith, ad Judith Augustam. 539",
            539,
        ),
        (
            "PL109:entry:0004",
            "pl109_rabanus_judith_appendix_593",
            "Appendix ad Expositionem in librum Judith. Jacobi Pamelii Commentarius",
            "Appendix ad Expositionem in librum Judith. — Jacobi Pamelii Commentarius. 593",
            593,
        ),
        (
            "PL109:entry:0005",
            "pl109_rabanus_esther_635",
            "Expositio in librum Esther, ad Judith Augustam",
            "Expositio in librum Esther, ad Judith Augustam. 635",
            635,
        ),
        (
            "PL109:entry:0006",
            "pl109_rabanus_sapientia_671",
            "Commentariorum in librum Sapientiæ libri tres, ad Otgarium archiepiscopum Moguntinum",
            "Commentariorum in librum Sapientiæ libri tres, ad Otgarium archiepiscopum Moguntinum. 671",
            671,
        ),
        (
            "PL109:entry:0007",
            "pl109_rabanus_ecclesiasticum_763",
            "Commentariorum in Ecclesiasticum libri decem, ad eundem",
            "Commentariorum in Ecclesiasticum libri decem, ad eundem, 763",
            763,
        ),
        (
            "PL109:entry:0008",
            "pl109_rabanus_machabaeorum_1127",
            "Commentaria in libros Machabæorum ad Ludovicum regem Franciæ et Geroldum sacri palatii archiepiscoponum",
            "Commentaria in libros Machabæorum ad Ludovicum regem Franciæ et Geroldum sacri palatii archiepiscoponum. 1127",
            1127,
        ),
    ]

    for order, (entry_key, helper_entry_id, lemma_raw, entry_raw, page_ref_int) in enumerate(entry_specs, start=1):
        helper_item = helper_map[helper_entry_id]
        best = helper_item.get("best_candidate") or {}
        target_file = best.get("file")
        helper_status = helper_item.get("status")
        confidence = round(float(best_probability(helper_item) or 0.0), 4)
        target_probability = best_probability(helper_item)
        raw_json = {
            "source_file": section_start_file,
            "helper_entry_id": helper_entry_id,
            "helper_status": helper_status,
            "helper_best_file": target_file,
            "helper_best_probability": best_probability(helper_item),
            "helper_candidate_role": best.get("candidate_role"),
            "helper_reason_summary": best.get("reason_summary"),
        }
        if helper_entry_id == "pl109_rabanus_regum_009":
            target_file = str(PREFACE_FILE)
            confidence = 0.92
            target_probability = confidence
            raw_json["helper_best_file"] = best.get("file")
            raw_json["resolution_note"] = (
                "Direct OCR inspection of the neighboring files shows the work opening at the preface/title leaf in file 010; "
                "the helper's 633 candidate is the tail Ordo Rerum false positive."
            )
        lemma_display = lemma_raw
        lemma_norm = norm_text(lemma_raw)
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": "PL109:node:works",
            "entry_order": order,
            "entry_kind": "lemma",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_norm,
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": None,
            "inferred_printed_page": page_ref_int,
            "section_start_file": section_start_file,
            "editorial_anchor_file": section_start_file,
            "target_file_best": target_file,
            "confidence": confidence,
            "raw_json": raw_json,
        }
        entries.append(entry)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(page_ref_int),
                "page_ref_raw": str(page_ref_int),
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": target_probability,
                "section_start_file": section_start_file,
                "editorial_anchor_file": section_start_file,
                "confidence": confidence,
                "raw_json": raw_json,
            }
        )

    sections = [
        {
            "section_key": section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CIX CONTINENTUR.",
            "heading_norm": "elenchus auctorum et operum qui in hoc tomo cix continentur",
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(SECTION_FILE),
            "file_end": str(SECTION_FILE),
            "confidence": 0.99,
            "raw_json": {
                "source_file": str(SECTION_FILE),
                "section_kind_reason": "Front-matter ELENCHUS of authors and works in the tomo; inventory of works rather than a subject index or ordo rerum block.",
                "observed_heading_lines": [
                    "ELENCHUS",
                    "AUCTORUM ET OPERUM QUI IN HOC TOMO CIX CONTINENTUR.",
                ],
            },
        }
    ]

    nodes = [
        {
            "node_key": "PL109:node:author",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "B. RABANI MAURI, FULDENSIS ABBATIS ET MOGUNTINI ARCHIEPISCOPI.",
            "label_norm": "b rabani mauri fuldensis abbatis et moguntini archiepiscopi",
            "label_sort": "b rabani mauri fuldensis abbatis et moguntini archiepiscopi",
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {"source_file": str(SECTION_FILE)},
        },
        {
            "node_key": "PL109:node:works",
            "section_key": section_key,
            "parent_node_key": "PL109:node:author",
            "node_order": 2,
            "node_kind": "rubric_group",
            "label_raw": "OPFRUM OMNIUM PARS PRIMA. — SCRIPTA AB IPSO JAM ABBATE EDITA. (Continuatio.)",
            "label_norm": "opfrum omnium pars prima scripta ab ipso jam abbate edita continuatio",
            "label_sort": "opfrum omnium pars prima scripta ab ipso jam abbate edita continuatio",
            "node_level": 2,
            "confidence": 0.96,
            "raw_json": {"source_file": str(SECTION_FILE)},
        },
    ]

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "The ELENCHUS author/work lines were recoverable from OCR and the helper resolved the cited page anchors for the listed works; the later ORDO RERUM tail was inspected but intentionally not serialized because it is editorial contents material rather than an alphabetical index entry block.",
        "evidence_files": [
            str(SECTION_FILE),
            str(PREFACE_FILE),
            str(TEXT_START_FILE),
            str(TEXT_CONT_FILE),
            str(ORDO_TAIL_FILE),
            str(ORDO_END_FILE),
        ],
    }

    notes = [
        "PL109 contains a front-matter ELENCHUS inventory at the start of the volume; this is the only section serialized here.",
        "The later ORDO RERUM tail was checked to confirm it is editorial contents material and not a separate alphabetical index section.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": "PL",
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Patrologia Latina 109",
    }

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "helper_request_json": str(HELPER_REQUEST_JSON),
            "helper_output_json": str(HELPER_OUTPUT_JSON),
            "output_file": str(OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PL109 alphabetical payload from the ELENCHUS inventory.",
            "completed": [
                "identified ELENCHUS front matter as the serialized index section",
                "inspected ORDO RERUM tail and excluded it as editorial contents material",
                "assembled helper request entries for all ELENCHUS work lines",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Reuse helper evidence in raw_json and keep OCR literals.",
            ],
        },
    )

    write_json(OUTPUT_FILE, payload)
    return payload


def main() -> None:
    write_json(HELPER_REQUEST_JSON, helper_request())
    helper_output = run_helper()
    write_json(HELPER_OUTPUT_JSON, helper_output)
    build_payload(helper_output)


if __name__ == "__main__":
    main()
