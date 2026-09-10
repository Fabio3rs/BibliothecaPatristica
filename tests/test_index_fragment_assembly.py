from __future__ import annotations

import json
from pathlib import Path

from patristica_pipeline.index_fragment_assembly import (
    assemble_index_fragments,
    verify_payload_consumes_fragments,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_alphabetical_assembly_consumes_every_complete_fragment(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = {
        "volume_id": "PL001",
        "status": "complete",
        "sections": [{"section_key": "PL001:index"}],
        "nodes": [],
        "scripture_refs": [],
        "notes": [],
    }
    _write(
        first,
        {
            **common,
            "chunk_id": "c1",
            "entries": [{"entry_key": "PL001:index:e1", "entry_raw": "Aaron, 1"}],
            "refs": [{"entry_key": "PL001:index:e1", "ref_order": 1, "ref_raw": "1"}],
        },
    )
    _write(
        second,
        {
            **common,
            "chunk_id": "c2",
            "entries": [{"entry_key": "PL001:index:e2", "entry_raw": "Abel, 2"}],
            "refs": [{"entry_key": "PL001:index:e2", "ref_order": 1, "ref_raw": "2"}],
        },
    )
    plan = {
        "volume_id": "PL001",
        "pipeline_kind": "alphabetical",
        "chunks": [
            {"chunk_id": "c1", "status": "complete", "output_file": str(first)},
            {"chunk_id": "c2", "status": "complete", "output_file": str(second)},
        ],
    }

    assembled = assemble_index_fragments(plan, tmp_path / "assembled.json")

    assert assembled["consumed_chunk_count"] == 2
    assert assembled["counts"]["entries"] == 2
    report = verify_payload_consumes_fragments(
        {
            "sections": assembled["data"]["sections"],
            "nodes": [],
            "entries": assembled["data"]["entries"],
            "refs": assembled["data"]["refs"],
            "scripture_refs": [],
        },
        assembled,
    )
    assert report["status"] == "ok"


def test_fragment_consumption_report_detects_dropped_entry_and_ref(tmp_path: Path) -> None:
    fragment = tmp_path / "fragment.json"
    _write(
        fragment,
        {
            "volume_id": "PL001",
            "chunk_id": "c1",
            "status": "complete",
            "sections": [{"section_key": "PL001:index"}],
            "nodes": [],
            "entries": [{"entry_key": "PL001:index:e1"}],
            "refs": [{"entry_key": "PL001:index:e1", "ref_order": 1}],
            "scripture_refs": [],
            "notes": [],
        },
    )
    assembled = assemble_index_fragments(
        {
            "volume_id": "PL001",
            "pipeline_kind": "alphabetical",
            "chunks": [
                {"chunk_id": "c1", "status": "complete", "output_file": str(fragment)}
            ],
        },
        tmp_path / "assembled.json",
    )

    report = verify_payload_consumes_fragments(
        {
            "sections": [{"section_key": "PL001:index"}],
            "nodes": [],
            "entries": [],
            "refs": [],
            "scripture_refs": [],
        },
        assembled,
    )

    assert report["status"] == "missing_fragment_objects"
    assert report["checks"]["entries"]["missing_stable_keys"]
    assert report["checks"]["refs"]["missing_stable_keys"]


def test_general_consumption_excludes_sections_owned_by_alphabetical_pipeline() -> None:
    assembled = {
        "schema_version": 1,
        "volume_id": "PO025",
        "pipeline_kind": "general",
        "data": {
            "works": [],
            "sections": [
                {
                    "section_key": "PO025:contents",
                    "scope_kind": "work_internal_table",
                    "index_kind": "TABLE DES MATIÈRES",
                    "heading_raw": "TABLE DES MATIÈRES",
                    "entries": [{"entry_key": "PO025:contents:001"}],
                },
                {
                    "section_key": "PO025:scripture",
                    "scope_kind": "work",
                    "index_kind": "PO_WORK_INDEX_SCRIPTURE",
                    "heading_raw": "TABLE DES CITATIONS DE LA BIBLE",
                    "entries": [{"entry_key": "PO025:scripture:001"}],
                },
            ],
        },
    }
    payload = {
        "works": [],
        "sections": [
            {
                "section_key": "PO025:contents",
                "entries": [{"entry_key": "PO025:contents:001"}],
            }
        ],
    }

    report = verify_payload_consumes_fragments(payload, assembled)

    assert report["status"] == "ok"
    assert report["checks"]["ownership"]["excluded_non_owned_section_count"] == 1


def test_general_consumption_excludes_post_volume_publisher_advertisement() -> None:
    assembled = {
        "schema_version": 1,
        "volume_id": "PL186",
        "pipeline_kind": "general",
        "data": {
            "works": [],
            "sections": [
                {
                    "section_key": "PL186:ordo",
                    "scope_kind": "volume_end",
                    "index_kind": "ORDO RERUM",
                    "heading_raw": "ORDO RERUM",
                    "entries": [{"entry_key": "PL186:ordo:001"}],
                },
                {
                    "section_key": "PL186:publisher-advertisement",
                    "scope_kind": "publisher_advertisement",
                    "index_kind": "publisher_catalogue_contents",
                    "heading_raw": "DEMONSTRATIONS EVANGELIQUES",
                    "raw_json": {
                        "editorial_scope_note": (
                            "Publisher advertisement after FINIS TOMI, external to PL186."
                        )
                    },
                    "entries": [{"entry_key": "PL186:publisher-advertisement:001"}],
                },
            ],
        },
    }
    payload = {
        "works": [],
        "sections": [
            {
                "section_key": "PL186:ordo",
                "entries": [{"entry_key": "PL186:ordo:001"}],
            }
        ],
    }

    report = verify_payload_consumes_fragments(payload, assembled)

    assert report["status"] == "ok"
    ownership = report["checks"]["ownership"]
    assert ownership["excluded_non_owned_section_count"] == 1
    assert ownership["excluded_non_owned_sections"][0]["section_key"] == (
        "PL186:publisher-advertisement"
    )


def test_general_consumption_preserves_chunk_target_and_evidence() -> None:
    evidence = {
        "status": "resolved",
        "method": "monotonic_chapter_heading_sequence",
        "query_raw": "CAP. I. De fide.",
        "matched_heading_raw": "CAPUT I. De fide.",
        "target_file": "/volume/body-010.txt",
        "inspected_files": ["/volume/body-010.txt"],
    }
    assembled = {
        "volume_id": "PL001",
        "pipeline_kind": "general",
        "data": {
            "works": [],
            "sections": [
                {
                    "section_key": "PL001:index-capitum",
                    "scope_kind": "work_front",
                    "index_kind": "INDEX CAPITUM",
                    "heading_raw": "INDEX CAPITUM",
                    "entries": [
                        {
                            "entry_key": "PL001:index-capitum:001",
                            "target_file": "/volume/body-010.txt",
                            "raw_json": {"physical_target_evidence": evidence},
                        }
                    ],
                }
            ],
        },
    }
    payload = {
        "works": [],
        "sections": [
            {
                "section_key": "PL001:index-capitum",
                "entries": [
                    {
                        "entry_key": "PL001:index-capitum:001",
                        "target_file": None,
                        "raw_json": {},
                    }
                ],
            }
        ],
    }

    report = verify_payload_consumes_fragments(payload, assembled)

    assert report["status"] == "missing_fragment_objects"
    assert report["checks"]["entry_targets"]["target_mismatch_entry_keys"] == [
        "PL001:index-capitum:001"
    ]


def test_general_assembly_accepts_and_deduplicates_string_notes(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = {
        "volume_id": "PO002",
        "status": "complete",
        "works": [],
        "sections": [],
    }
    _write(
        first,
        {
            **common,
            "chunk_id": "c1",
            "notes": ["Printed page numbers are editorial references."],
        },
    )
    _write(
        second,
        {
            **common,
            "chunk_id": "c2",
            "notes": [
                "Printed page numbers are editorial references.",
                "The following scan is blank.",
            ],
        },
    )

    assembled = assemble_index_fragments(
        {
            "volume_id": "PO002",
            "pipeline_kind": "general",
            "chunks": [
                {"chunk_id": "c1", "status": "complete", "output_file": str(first)},
                {"chunk_id": "c2", "status": "complete", "output_file": str(second)},
            ],
        },
        tmp_path / "assembled.json",
    )

    assert assembled["data"]["notes"] == [
        "Printed page numbers are editorial references.",
        "The following scan is blank.",
    ]


def test_alphabetical_assembly_accepts_structured_and_string_notes(tmp_path: Path) -> None:
    fragment = tmp_path / "fragment.json"
    _write(
        fragment,
        {
            "volume_id": "PL001",
            "chunk_id": "c1",
            "status": "complete",
            "sections": [],
            "nodes": [],
            "entries": [],
            "refs": [],
            "scripture_refs": [],
            "notes": [
                "OCR evidence was checked.",
                {"note_key": "PL001:note:1", "text": "Helper remained ambiguous."},
            ],
        },
    )

    assembled = assemble_index_fragments(
        {
            "volume_id": "PL001",
            "pipeline_kind": "alphabetical",
            "chunks": [
                {"chunk_id": "c1", "status": "complete", "output_file": str(fragment)}
            ],
        },
        tmp_path / "assembled.json",
    )

    assert assembled["data"]["notes"] == [
        "OCR evidence was checked.",
        {"note_key": "PL001:note:1", "text": "Helper remained ambiguous."},
    ]
