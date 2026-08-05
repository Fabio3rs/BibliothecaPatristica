#!/usr/bin/env python3
"""Build PO021 editorial-closure alphabetical payload.

Usage: python scripts/pipeline_index_extraction/build_po021_alphabetical_payload.py
Writes the PO021 helper request and canonical payload, incorporating helper
output evidence when data/alphabetical_index_payloads/PO021_helper_output.json
already exists.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


VOLUME_ID = "PO021"
SOURCE_ROOT = "/homessddata/Projects/pdfocr/teste/PO021/text"
OUT_DIR = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads")
INTERMEDIATE_DIR = Path("/homessddata/Projects/pdfocr/data/intermediate_payloads/PO021")
HELPER_REQUEST = OUT_DIR / "PO021_helper_request.json"
HELPER_OUTPUT = OUT_DIR / "PO021_helper_output.json"
FINAL_OUTPUT = OUT_DIR / "PO021_alphabetical_indices.json"
SECTION_FILE = f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-850.txt"
TABLE_FILE = f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-892.txt"
MEMORIAL_FILE = f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-890.txt"
COLOPHON_FILE = f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-891.txt"


ERRATA = [
    {
        "page": "[1736]",
        "page_int": 1736,
        "locator": "ligne 1",
        "line_raw": "Page [1736], ligne 1, lire Ուսպէտյան.",
        "correction": "Ուսպէտյան",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-704.txt",
    },
    {
        "page": "[1749]",
        "page_int": 1749,
        "locator": "note 1",
        "line_raw": "Page [1749], note 1, lire ևայաստանի.",
        "correction": "ևայաստանի",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-717.txt",
    },
    {
        "page": "[1760]",
        "page_int": 1760,
        "locator": "note 1",
        "line_raw": "Page [1760], note 1, lire սրբապարգեւ.",
        "correction": "սրբապարգեւ",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-728.txt",
    },
    {
        "page": "[1763]",
        "page_int": 1763,
        "locator": "ligne 11",
        "line_raw": "Page [1763], ligne 11, lire գրչակալ.",
        "correction": "գրչակալ",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-731.txt",
    },
    {
        "page": "[1764]",
        "page_int": 1764,
        "locator": "l. 14",
        "line_raw": "Page [1764], l. 14, lire տակաւին.",
        "correction": "տակաւին",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-732.txt",
    },
    {
        "page": "[1772]",
        "page_int": 1772,
        "locator": "l. 15",
        "line_raw": "Page [1772], l. 15, lire անգրագիտ.",
        "correction": "անգրագիտ",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-740.txt",
    },
    {
        "page": "[1774]",
        "page_int": 1774,
        "locator": "l. 1",
        "line_raw": "Page [1774], l. 1, lire այսօրէն նորիկ.",
        "correction": "այսօրէն նորիկ",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-742.txt",
    },
    {
        "page": "——",
        "page_int": 1774,
        "locator": "l. 13",
        "line_raw": "Page ——, l. 13, lire կանոնագրք.",
        "correction": "կանոնագրք",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-742.txt",
        "inherited_page_from_previous": "[1774]",
    },
    {
        "page": "[1775]",
        "page_int": 1775,
        "locator": "l. 15",
        "line_raw": "Page [1775], l. 15, lire գրչաուս.",
        "correction": "գրչաուս",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-743.txt",
    },
    {
        "page": "[1777]",
        "page_int": 1777,
        "locator": "l. 11",
        "line_raw": "Page [1777], l. 11, lire անապատեայ.",
        "correction": "անապատեայ",
        "target": f"{SOURCE_ROOT}/d306e9e5-aa17-41bf-9b1c-80816e85278c-745.txt",
    },
    {
        "page": "[1782]",
        "page_int": 1782,
        "locator": "note",
        "line_raw": "Page [1782], note, lire Պատմութիւն.",
        "correction": "Պատմութիւն",
        "target": f"{SOURCE_ROOT}/e4c6cd18-51d0-4018-aed6-94652646b47d-750.txt",
    },
    {
        "page": "[1783]",
        "page_int": 1783,
        "locator": "l. 14",
        "line_raw": "Page [1783], l. 14, lire ճշմ.",
        "correction": "ճշմ",
        "target": f"{SOURCE_ROOT}/e4c6cd18-51d0-4018-aed6-94652646b47d-751.txt",
    },
]


def entry_key(order: int) -> str:
    return f"po021_errata_{order:03d}"


def load_helper_entries() -> dict[str, dict]:
    if not HELPER_OUTPUT.exists():
        return {}
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    return {entry.get("entry_id"): entry for entry in entries if entry.get("entry_id")}


def helper_summary(helper_entry: dict | None) -> dict | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates = helper_entry.get("candidates", [])[:3]
    return {
        "status": helper_entry.get("status"),
        "candidate_role": helper_entry.get("candidate_role") or best.get("candidate_role"),
        "reason_summary": helper_entry.get("reason_summary") or best.get("reason_summary"),
        "best_candidate_file": best.get("file"),
        "best_candidate_probability": best.get("probability"),
        "top_candidates": [
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "evidence_kinds": [
                    evidence.get("kind")
                    for evidence in candidate.get("evidence", [])
                    if evidence.get("kind")
                ],
            }
            for candidate in candidates
        ],
    }


def build_helper_request() -> dict:
    entries = []
    for order, item in enumerate(ERRATA, start=1):
        entries.append(
            {
                "entry_id": entry_key(order),
                "lemma_raw": item["line_raw"],
                "query_names": [
                    item["correction"],
                    item["line_raw"],
                    f"LE SYNAXAIRE ARMÉNIEN {item['page']}",
                ],
                "page_hints": [item["page"]],
                "page_hint_ints": [item["page_int"]],
                "context_raw": item["line_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": SOURCE_ROOT,
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def build_payload() -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    helper_entries = load_helper_entries()
    section_key = "po021_editorial_closure_errata"

    sections = [
        {
            "section_key": section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "editorial_closure",
            "heading_raw": "ERRATA",
            "heading_norm": "ERRATA",
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": SECTION_FILE,
            "file_end": SECTION_FILE,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "The block is a printed errata list with page/line correction locators, not an alphabetical, onomastic, scripture, or concordance index.",
                "detected_by": "manual OCR inspection plus scoped rg for ERRATA/Page/lire",
                "neighboring_files_checked": [
                    f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-848.txt",
                    f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-849.txt",
                    SECTION_FILE,
                    f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-851.txt",
                    f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-852.txt",
                ],
            },
        }
    ]

    entries = []
    refs = []
    for order, item in enumerate(ERRATA, start=1):
        key = entry_key(order)
        helper = helper_entries.get(key)
        raw_json = {
            "correction_text": item["correction"],
            "errata_locator": item["locator"],
            "target_resolution": "Direct rg on bracketed printed page heading within PO021, cross-checked against helper when available.",
            "helper_use_note": "Helper evidence was retained as material support, but exact OCR heading matches were preferred over ambiguous adjacent helper candidates.",
        }
        if item.get("inherited_page_from_previous"):
            raw_json["page_ref_inheritance"] = {
                "page_ref_raw": item["page"],
                "inherited_from_previous_page_ref": item["inherited_page_from_previous"],
            }
        hs = helper_summary(helper)
        if hs:
            raw_json["helper"] = hs

        entries.append(
            {
                "entry_key": key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": order,
                "entry_kind": "editorial_note",
                "lemma_raw": item["line_raw"],
                "lemma_display": item["line_raw"],
                "lemma_norm": item["line_raw"],
                "lemma_sort": f"{order:03d}",
                "entry_raw": item["line_raw"],
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": item["page_int"],
                "section_start_file": SECTION_FILE,
                "editorial_anchor_file": SECTION_FILE,
                "target_file_best": item["target"],
                "confidence": 0.96 if not item.get("inherited_page_from_previous") else 0.9,
                "raw_json": raw_json,
            }
        )

        refs.append(
            {
                "entry_key": key,
                "ref_order": 1,
                "ref_kind": "editorial_page_line"
                if "l" in item["locator"]
                else "editorial_page",
                "ref_raw": f"Page {item['page']}, {item['locator']}",
                "page_ref_raw": item["page"],
                "page_ref_int": item["page_int"],
                "page_ref_col": None,
                "line_ref_raw": item["locator"],
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": item["target"],
                "target_file_probability": 0.98 if not item.get("inherited_page_from_previous") else 0.9,
                "section_start_file": SECTION_FILE,
                "editorial_anchor_file": SECTION_FILE,
                "confidence": 0.96 if not item.get("inherited_page_from_previous") else 0.9,
                "raw_json": {
                    "locator_kind": "errata_correction",
                    "helper": helper_summary(helper),
                    "direct_target_evidence": f"Bracketed printed page heading {item['page_int']} found by scoped rg in target file.",
                },
            }
        )

    return {
        "schema_version": 1,
        "generated_at": now,
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PO",
            "source_root": SOURCE_ROOT,
            "volume_label": VOLUME_ID,
            "notes": [
                "PO021 has no recoverable alphabetical, analytical, onomastic, scripture, pericope, concordance, or author index in the inspected tail.",
                "A printed ERRATA block on OCR file 850 is represented as editorial_closure because it carries material page/line correction locators.",
                "The final pages after the ERRATA contain the Jours Avéleats fascicule title, continuous Synaxaire text, memorial/colophon, table of contents, blank guard pages, and library pocket text.",
            ],
        },
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete_editorial_closure_no_alphabetical_index",
            "entries_status_reason": "The only extractable index-schema section found in the expanded tail review is the ERRATA editorial-closure block. The later TABLE DES MATIÈRES is a volume contents page, not an alphabetical or remissive index.",
            "evidence_files": [
                SECTION_FILE,
                f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-851.txt",
                f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-853.txt",
                MEMORIAL_FILE,
                COLOPHON_FILE,
                TABLE_FILE,
                f"{SOURCE_ROOT}/2e8903be-9e03-4337-8c2d-25dad00e6187-895.txt",
            ],
        },
        "notes": [
            "Previous empty payload was too narrow because it did not include the ERRATA block immediately before the filtered tail window.",
            "Scoped searches for index-like headings found TABLE DES MATIÈRES at file 892 and ERRATA at file 850, but no alphabetical, scripture, onomastic, concordance, or author index section.",
            "The dash page in 'Page ——, l. 13' is modeled as inherited from the preceding '[1774]' errata page instead of inventing a separate printed page.",
        ],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    HELPER_REQUEST.write_text(
        json.dumps(build_helper_request(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = build_payload()
    FINAL_OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE_DIR / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": payload["generated_at"],
                "current_focus": "PO021 payload assembled and ready for validation",
                "completed": [
                    "verified filtered tail pages 865-896",
                    "expanded inspection to neighboring files 848-852",
                    "serialized ERRATA as editorial_closure",
                    "confirmed no alphabetical/scripture/onomastic/concordance index",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "Helper request contains the twelve errata correction lines.",
                    "Final payload should be regenerated after helper output if helper evidence changes.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
