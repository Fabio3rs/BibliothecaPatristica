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
