from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from scripts.alphabetical_index_db import (  # noqa: E402
    ALPHABETICAL_DB_SCHEMA_VERSION,
    canonical_ref_location,
    connect_db,
    init_schema,
)
from scripts.import_alphabetical_index_json import import_payload  # noqa: E402
from test_import_alphabetical_index_json import minimal_payload  # noqa: E402


def test_page_line_range_does_not_become_a_page_range() -> None:
    location = canonical_ref_location(
        {
            "ref_kind": "editorial_page_line",
            "page_ref_raw": "10",
            "page_ref_int": 10,
            "line_ref_raw": ",2-3",
            "range_start_raw": "10",
            "range_end_raw": "2",
        }
    )

    assert location["cited_editorial_page_start_number"] == 10
    assert location["cited_editorial_page_end_number"] == 10
    assert location["cited_editorial_line_start_number"] == 2
    assert location["cited_editorial_line_end_number"] == 3
    assert location["cited_location_parse_status"] == "parsed"


def test_cross_page_line_range_has_two_typed_endpoints() -> None:
    location = canonical_ref_location(
        {
            "ref_kind": "editorial_page_line",
            "page_ref_raw": "69",
            "page_ref_int": 69,
            "line_ref_raw": "_13-70_1",
        }
    )

    assert location["cited_editorial_page_start_number"] == 69
    assert location["cited_editorial_line_start_number"] == 13
    assert location["cited_editorial_page_end_number"] == 70
    assert location["cited_editorial_line_end_number"] == 1


def test_subscript_line_digits_are_parsed_without_changing_the_literal() -> None:
    location = canonical_ref_location(
        {
            "ref_kind": "editorial_page_line",
            "page_ref_raw": "58",
            "page_ref_int": 58,
            "line_ref_raw": "₆₋₁₀",
        }
    )

    assert location["cited_editorial_line_raw"] == "₆₋₁₀"
    assert location["cited_editorial_line_start_number"] == 6
    assert location["cited_editorial_line_end_number"] == 10
    assert location["cited_location_parse_status"] == "parsed"


def test_import_materializes_explicit_semantics_and_evidence(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["sections"][0]["raw_json"] = {
        "pipeline_owner": "alphabetical",
        "alphabetical_role": "owned_section",
        "material_reference_mode": "remissive",
        "scripture_mode": "none",
    }
    payload["entries"][0]["source_span"] = {
        "file": "/tmp/po009/text/page-001.txt",
        "line_start": 2,
        "line_end": 2,
    }
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page_line",
            "ref_raw": "10,2-3",
            "page_ref_raw": "10",
            "page_ref_int": 10,
            "line_ref_raw": ",2-3",
            "target_file": "/tmp/po009/text/page-010.txt",
            "target_file_probability": 0.91,
            "raw_json": {
                "compact_locator": {
                    "status": "resolved",
                    "evidence": [
                        {
                            "kind": "editorial_header_match",
                            "file": "/tmp/po009/text/page-010.txt",
                            "editorial_page": "10",
                            "detail": "header plus neighbor sequence",
                        }
                    ],
                }
            },
        }
    ]
    payload["coverage"] = {"locator_status": "complete"}
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()
        section = con.execute(
            """SELECT pipeline_owner, alphabetical_role,
                      material_reference_mode, scripture_mode, taxonomy_source,
                      index_ocr_file_start
            FROM alphabetical_sections"""
        ).fetchone()
        ref = con.execute(
            """SELECT cited_editorial_page_start_number,
                      cited_editorial_page_end_number,
                      cited_editorial_line_start_number,
                      cited_editorial_line_end_number,
                      resolved_target_ocr_file,
                      cited_location_parse_status
            FROM alphabetical_refs"""
        ).fetchone()
        span_count = con.execute(
            "SELECT COUNT(*) FROM alphabetical_entry_source_spans"
        ).fetchone()[0]
        evidence = con.execute(
            """SELECT evidence_kind, evidence_ocr_file_path,
                      observed_editorial_page_number
            FROM alphabetical_locator_evidence"""
        ).fetchone()
        schema_version = con.execute(
            """SELECT meta_value FROM alphabetical_schema_meta
            WHERE meta_key = 'schema_version'"""
        ).fetchone()[0]

    assert dict(section) == {
        "pipeline_owner": "alphabetical",
        "alphabetical_role": "owned_section",
        "material_reference_mode": "remissive",
        "scripture_mode": "none",
        "taxonomy_source": "explicit",
        "index_ocr_file_start": "/tmp/po009/text/page-001.txt",
    }
    assert dict(ref) == {
        "cited_editorial_page_start_number": 10,
        "cited_editorial_page_end_number": 10,
        "cited_editorial_line_start_number": 2,
        "cited_editorial_line_end_number": 3,
        "resolved_target_ocr_file": "/tmp/po009/text/page-010.txt",
        "cited_location_parse_status": "parsed",
    }
    assert span_count == 1
    assert dict(evidence) == {
        "evidence_kind": "editorial_header_match",
        "evidence_ocr_file_path": "/tmp/po009/text/page-010.txt",
        "observed_editorial_page_number": 10,
    }
    assert schema_version == str(ALPHABETICAL_DB_SCHEMA_VERSION)


def test_legacy_target_updates_keep_canonical_aliases_in_sync(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": "10",
            "page_ref_raw": "10",
            "page_ref_int": 10,
            "target_file": "/tmp/po009/text/page-010.txt",
            "target_file_probability": 0.8,
            "raw_json": {},
        }
    ]
    payload["coverage"] = {"locator_status": "complete"}
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.execute(
            """UPDATE alphabetical_refs
            SET target_file = NULL, target_file_probability = NULL"""
        )
        con.execute(
            """UPDATE alphabetical_entries
            SET target_file_best = NULL"""
        )
        ref = con.execute(
            """SELECT target_file, resolved_target_ocr_file,
                      target_file_probability,
                      target_ocr_file_candidate_score
            FROM alphabetical_refs"""
        ).fetchone()
        entry = con.execute(
            """SELECT target_file_best, resolved_target_ocr_file
            FROM alphabetical_entries"""
        ).fetchone()

    assert tuple(ref) == (None, None, None, None)
    assert tuple(entry) == (None, None)
