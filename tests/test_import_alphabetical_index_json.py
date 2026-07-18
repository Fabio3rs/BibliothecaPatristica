from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.import_alphabetical_index_json import build_validation_summary


def minimal_payload() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-18T00:00:00Z",
        "volume": {
            "volume_id": "PO009",
            "collection": "PO",
            "source_root": "/tmp/po009/text",
            "volume_label": "PO009",
        },
        "sections": [
            {
                "section_key": "PO009:section:001",
                "volume_id": "PO009",
                "work_key": None,
                "section_order": 1,
                "section_kind": "scripture_index",
                "heading_raw": "TABLE DES CITATIONS",
                "heading_norm": "table des citations",
                "heading_letter": None,
                "page_start": 1,
                "page_end": 1,
                "file_start": "/tmp/po009/text/page-001.txt",
                "file_end": "/tmp/po009/text/page-001.txt",
                "confidence": 0.9,
                "raw_json": {},
            }
        ],
        "nodes": [],
        "entries": [
            {
                "entry_key": "PO009:entry:001",
                "section_key": "PO009:section:001",
                "parent_node_key": None,
                "entry_order": 1,
                "entry_kind": "scripture_citation",
                "lemma_raw": "GENÈSE",
                "lemma_display": "GENÈSE",
                "lemma_norm": "genese",
                "lemma_sort": "genese",
                "entry_raw": "GENÈSE I, 6 . . . 169",
                "context_raw": "GENÈSE I, 6 . . . 169",
                "heading_letter": None,
                "inferred_printed_page": 169,
                "section_start_file": "/tmp/po009/text/page-001.txt",
                "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
                "target_file_best": "/tmp/po009/text/page-169.txt",
                "confidence": 0.9,
                "raw_json": {},
            }
        ],
        "refs": [],
        "scripture_refs": [],
        "coverage": {},
        "notes": [],
    }


def test_duplicate_refs_ref_order_is_rejected() -> None:
    payload = minimal_payload()
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": "169",
            "page_ref_raw": "169",
            "page_ref_int": 169,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/tmp/po009/text/page-169.txt",
            "target_file_probability": 0.9,
            "section_start_file": "/tmp/po009/text/page-001.txt",
            "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
            "confidence": 0.9,
            "raw_json": {},
        },
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": "170",
            "page_ref_raw": "170",
            "page_ref_int": 170,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/tmp/po009/text/page-170.txt",
            "target_file_probability": 0.8,
            "section_start_file": "/tmp/po009/text/page-001.txt",
            "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
            "confidence": 0.8,
            "raw_json": {},
        },
    ]

    try:
        build_validation_summary(payload)
    except ValueError as exc:
        assert "Duplicate refs ref_order" in str(exc)
    else:
        raise AssertionError("Expected duplicate refs ref_order validation failure")


def test_duplicate_scripture_refs_ref_order_is_rejected() -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "Gen. I, 6",
            "ref_norm": "Gen 1:6",
            "book_raw": "Gen.",
            "book_norm": "Genesis",
            "chapter_start": 1,
            "verse_start": 6,
            "chapter_end": None,
            "verse_end": None,
            "is_range": 0,
            "confidence": 0.9,
            "raw_json": {},
        },
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "Gen. I, 7",
            "ref_norm": "Gen 1:7",
            "book_raw": "Gen.",
            "book_norm": "Genesis",
            "chapter_start": 1,
            "verse_start": 7,
            "chapter_end": None,
            "verse_end": None,
            "is_range": 0,
            "confidence": 0.9,
            "raw_json": {},
        },
    ]

    try:
        build_validation_summary(payload)
    except ValueError as exc:
        assert "Duplicate scripture_refs ref_order" in str(exc)
    else:
        raise AssertionError("Expected duplicate scripture_refs ref_order validation failure")
