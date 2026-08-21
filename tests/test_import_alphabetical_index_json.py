from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.import_alphabetical_index_json import (
    ValidationErrors,
    build_validation_summary,
    looks_like_cross_reference_without_anchor,
    normalize_material_path,
    normalize_schema_version,
)


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


def test_normalize_schema_version_accepts_equivalent_v1_representations() -> None:
    assert normalize_schema_version(1) == 1
    assert normalize_schema_version(1.0) == 1
    assert normalize_schema_version("1") == 1
    assert normalize_schema_version("1.0") == 1


def test_cross_reference_detector_matches_common_remission_markers() -> None:
    assert looks_like_cross_reference_without_anchor("Ab, vid. Ex")
    assert looks_like_cross_reference_without_anchor("voir Exode")
    assert looks_like_cross_reference_without_anchor("id. 4)")
    assert not looks_like_cross_reference_without_anchor("37 à 39")
    assert not looks_like_cross_reference_without_anchor("Div. Nom. cap. 3, § 1")
    assert not looks_like_cross_reference_without_anchor("ibid.")


def test_empty_extraction_requires_explicit_coverage_evidence() -> None:
    payload = minimal_payload()
    payload["sections"] = []
    payload["entries"] = []

    try:
        build_validation_summary(payload)
    except ValueError as exc:
        assert "entries is empty" in str(exc)
    else:
        raise AssertionError("Expected empty extraction validation failure")


def test_confirmed_empty_extraction_is_valid() -> None:
    payload = minimal_payload()
    payload["sections"] = []
    payload["entries"] = []
    payload["coverage"] = {
        "entries_status": "no_index_section",
        "entries_status_reason": "No alphabetical list entries occur in the inspected index.",
        "evidence_files": ["/tmp/po009/text/page-001.txt"],
    }

    summary = build_validation_summary(payload)

    assert summary["status"] == "valid"
    assert summary["counts"]["entries"] == 0


def test_no_line_items_requires_a_detected_section() -> None:
    payload = minimal_payload()
    payload["sections"] = []
    payload["entries"] = []
    payload["coverage"] = {
        "entries_status": "no_line_items",
        "entries_status_reason": "No rows were recovered.",
        "evidence_files": ["/tmp/po009/text/page-001.txt"],
    }

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        assert "no_index_section" in str(exc)
    else:
        raise AssertionError("Expected structural empty-status validation failure")


def test_new_payload_rejects_legacy_scripture_material_ref_kind() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "scripture",
            "ref_raw": "Gen. I, 6",
            "page_ref_raw": "169",
            "target_file": "/tmp/po009/text/page-169.txt",
        }
    ]

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        assert "ref_kind has invalid value 'scripture'" in str(exc)
    else:
        raise AssertionError("Expected legacy scripture ref_kind rejection")


def test_new_payload_rejects_general_pipeline_boundary_sections() -> None:
    for section_kind in ("ordo_rerum", "editorial_closure"):
        payload = minimal_payload()
        payload["sections"][0]["section_kind"] = section_kind

        try:
            build_validation_summary(payload)
        except ValidationErrors as exc:
            assert "belongs outside the alphabetical-index pipeline" in str(exc)
        else:
            raise AssertionError(
                f"Expected {section_kind} to be rejected in a new extraction"
            )


def test_new_payload_rejects_volume_works_inventory_disguised_as_author_index() -> None:
    payload = minimal_payload()
    payload["sections"][0]["section_kind"] = "author_index"
    payload["sections"][0]["heading_raw"] = (
        "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CCIV CONTINENTUR."
    )

    with pytest.raises(
        ValidationErrors,
        match="structural works/contents inventory",
    ):
        build_validation_summary(payload)


def test_scripture_entry_requires_a_scripture_ref() -> None:
    payload = minimal_payload()
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": "169",
            "page_ref_raw": "169",
        }
    ]

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        assert "has no scripture_refs" in str(exc)
    else:
        raise AssertionError("Expected scripture entry without passage to be rejected")


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


def test_cross_reference_ref_without_anchor_is_rejected_with_specific_error() -> None:
    payload = minimal_payload()
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "unresolved",
            "ref_raw": "Ab, vid. Ex",
            "page_ref_raw": None,
            "page_ref_int": None,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "section_start_file": "/tmp/po009/text/page-001.txt",
            "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
            "confidence": 0.4,
            "raw_json": {},
        }
    ]

    try:
        build_validation_summary(payload)
    except ValueError as exc:
        assert "looks like a cross-reference without a material anchor" in str(exc)
    else:
        raise AssertionError("Expected cross-reference-without-anchor validation failure")


def test_structural_locator_without_page_anchor_is_valid() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["coverage"] = {"locator_status": "partial"}
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "target_locator",
            "ref_raw": "Div. Nom. cap. 3, § 1",
            "page_ref_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "confidence": 0.9,
            "raw_json": {"locator_scope": "Dionysian work citation"},
        }
    ]

    summary = build_validation_summary(payload)

    assert summary["status"] == "valid"
    assert summary["counts"]["refs"] == 1


def test_open_notation_without_page_anchor_is_valid() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["coverage"] = {"locator_status": "partial"}
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "unresolved",
            "ref_raw": "ibid.",
            "page_ref_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "confidence": 0.6,
            "raw_json": {"notation": [{"notation_key": "ibid"}]},
        }
    ]

    summary = build_validation_summary(payload)

    assert summary["status"] == "valid"


def test_editorial_page_ref_without_page_anchor_is_rejected() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["coverage"] = {"locator_status": "partial"}
    payload["refs"] = [
        {
            "entry_key": "PO009:entry:001",
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": "169",
            "page_ref_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "confidence": 0.6,
            "raw_json": {},
        }
    ]

    with pytest.raises(
        ValidationErrors,
        match="must include at least one material anchor",
    ):
        build_validation_summary(payload)


def test_validation_aggregates_multiple_failures() -> None:
    payload = minimal_payload()
    payload["sections"][0]["section_kind"] = "onomastic_veterum"
    payload["entries"][0]["section_key"] = "PO009:section:999"
    payload["entries"][0]["entry_kind"] = "lemma"

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        message = str(exc)
        assert "2 validation errors" in message
        assert "sections[1].section_kind has invalid value 'onomastic_veterum'" in message
        assert "entries[1] references missing section_key: PO009:section:999" in message
    else:
        raise AssertionError("Expected aggregated validation failures")


def test_normalize_material_path_accepts_absolute_and_relative_forms() -> None:
    assert normalize_material_path("/tmp/po009/text/page-001.txt") == Path("/tmp/po009/text/page-001.txt")
    assert normalize_material_path("teste/PO009/text/page-001.txt") == ROOT / "teste/PO009/text/page-001.txt"


def test_material_paths_may_be_relative_when_still_inside_same_volume() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["volume"]["source_root"] = str(ROOT / "teste/PO009/text")
    payload["sections"][0]["file_start"] = "teste/PO009/text/page-001.txt"
    payload["sections"][0]["file_end"] = "teste/PO009/text/page-001.txt"
    payload["entries"][0]["section_start_file"] = "teste/PO009/text/page-001.txt"
    payload["entries"][0]["editorial_anchor_file"] = "teste/PO009/text/page-001.txt"
    payload["entries"][0]["target_file_best"] = "teste/PO009/text/page-169.txt"
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
            "target_file": "teste/PO009/text/page-169.txt",
            "target_file_probability": 0.9,
            "section_start_file": "teste/PO009/text/page-001.txt",
            "editorial_anchor_file": "teste/PO009/text/page-001.txt",
            "confidence": 0.9,
            "raw_json": {},
        }
    ]

    summary = build_validation_summary(payload)
    assert summary["status"] == "valid"


def test_material_paths_from_another_volume_are_rejected() -> None:
    payload = minimal_payload()
    payload["entries"][0]["target_file_best"] = "teste/PO010/text/page-169.txt"

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        assert "entries[1].target_file_best points outside volume.source_root" in str(exc)
    else:
        raise AssertionError("Expected cross-volume material path validation failure")


def test_duplicate_entry_key_is_rejected_before_sqlite() -> None:
    payload = minimal_payload()
    payload["entries"].append(
        {
            "entry_key": "PO009:entry:001",
            "section_key": "PO009:section:001",
            "parent_node_key": None,
            "entry_order": 2,
            "entry_kind": "scripture_citation",
            "lemma_raw": "EXODE",
            "lemma_display": "EXODE",
            "lemma_norm": "exode",
            "lemma_sort": "exode",
            "entry_raw": "EXODE I, 1 . . . 170",
            "context_raw": "EXODE I, 1 . . . 170",
            "heading_letter": None,
            "inferred_printed_page": 170,
            "section_start_file": "/tmp/po009/text/page-001.txt",
            "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
            "target_file_best": "/tmp/po009/text/page-170.txt",
            "confidence": 0.9,
            "raw_json": {},
        }
    )

    try:
        build_validation_summary(payload)
    except ValidationErrors as exc:
        assert "Duplicate entry_key detected" in str(exc)
        assert "entry_key must be unique across the whole volume payload" in str(exc)
    else:
        raise AssertionError("Expected duplicate entry_key validation failure")
