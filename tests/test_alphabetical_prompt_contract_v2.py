from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from tools.indexing.alphabetical_artifact_validation import (
    validate_discovery_manifest,
    validate_semantic_fragment_v2,
    validate_source_span,
)
from tools.indexing.alphabetical_compact_driver import load_semantic_manifest
from tools.indexing.alphabetical_compact_pipeline import CompactPipelineError
from tools.indexing.alphabetical_prompt_contract import (
    GLOSSARY_VERSION,
    OUTPUT_SCHEMA_VERSION,
    prompt_reference_bundle,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def versioned(payload: dict) -> dict:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        **prompt_reference_bundle(),
        **payload,
    }


def test_source_span_hash_detects_stale_ocr_checkpoint(tmp_path: Path) -> None:
    source_root = tmp_path / "text"
    source_root.mkdir()
    source_file = source_root / "page-001.txt"
    source_file.write_text("first\nsecond\n", encoding="utf-8")
    span = {
        "file": str(source_file),
        "line_start": 2,
        "line_end": 2,
        "text_sha256": hashlib.sha256(b"second\n").hexdigest(),
    }

    validate_source_span(span, source_root=source_root, label="span")
    source_file.write_text("first\nchanged\n", encoding="utf-8")

    with pytest.raises(CompactPipelineError, match="current OCR span"):
        validate_source_span(span, source_root=source_root, label="span")


def test_reference_bundle_fingerprints_contract_glossary_and_schemas() -> None:
    bundle = prompt_reference_bundle()

    assert bundle["glossary_version"] == GLOSSARY_VERSION
    assert set(bundle["references"]) == {
        "skill",
        "prompt_contract",
        "output_format",
        "interpretation_contract",
        "glossary_document",
        "glossary_data",
        "spatial_field_dictionary",
        "extractor_contract",
        "discovery_schema",
        "semantic_fragment_schema",
    }
    assert all(
        record["sha256"] and len(record["sha256"]) == 64
        for record in bundle["references"].values()
    )


def test_discovery_manifest_supports_owned_and_boundary_segments_in_one_file(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL085" / "text"
    source_file = source_root / "page-542.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("\n".join(f"line {n}" for n in range(1, 121)))
    payload = versioned(
        {
            "stage": "discovery",
            "volume_id": "PL085",
            "source_root": str(source_root),
            "input_fingerprint": "fingerprint",
            "status": "complete",
            "inspected_files": [str(source_file)],
            "segments": [
                {
                    "segment_id": "PL085:542:owned",
                    "file": str(source_file),
                    "line_start": 3,
                    "line_end": 50,
                    "role": "owned",
                    "reason": "alphabetical entries",
                },
                {
                    "segment_id": "PL085:542:boundary",
                    "file": str(source_file),
                    "line_start": 53,
                    "line_end": 120,
                    "role": "boundary",
                    "reason": "ORDO RERUM starts at line 53",
                },
            ],
            "expansion_requests": [],
            "unresolved": [],
        }
    )

    validated = validate_discovery_manifest(
        payload,
        source_root=source_root,
        expected_volume_id="PL085",
        expected_input_fingerprint="fingerprint",
    )

    assert [segment["role"] for segment in validated["segments"]] == [
        "owned",
        "boundary",
    ]


def test_discovery_manifest_rejects_overlapping_segments(tmp_path: Path) -> None:
    source_root = tmp_path / "text"
    source_file = source_root / "page-001.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("one\ntwo\nthree\nfour\n")
    payload = versioned(
        {
            "stage": "discovery",
            "volume_id": "PL001",
            "source_root": str(source_root),
            "input_fingerprint": "fp",
            "status": "complete",
            "inspected_files": [str(source_file)],
            "segments": [
                {
                    "segment_id": "one",
                    "file": str(source_file),
                    "line_start": 1,
                    "line_end": 3,
                    "role": "owned",
                    "reason": "index",
                },
                {
                    "segment_id": "two",
                    "file": str(source_file),
                    "line_start": 3,
                    "line_end": 4,
                    "role": "boundary",
                    "reason": "closure",
                },
            ],
            "expansion_requests": [],
            "unresolved": [],
        }
    )

    with pytest.raises(CompactPipelineError, match="overlapping discovery"):
        validate_discovery_manifest(
            payload,
            source_root=source_root,
            expected_volume_id="PL001",
            expected_input_fingerprint="fp",
        )


def test_semantic_fragment_requires_traceable_entries_and_known_notation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "text"
    source_file = source_root / "page-010.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("INDEX\nAaron, 10, ibid.\n")
    span = {
        "file": str(source_file),
        "line_start": 2,
        "line_end": 2,
    }
    fragment = {
        "schema_version": 2,
        **prompt_reference_bundle(),
        "task_id": "PL001:semantic:index",
        "input_fingerprint": "fp",
        "consumed_spans": [span],
        "residual_spans": [],
        "sections": [
            {
                "section_key": "PL001:index",
                "file_start": str(source_file),
                "file_end": str(source_file),
                "raw_json": {
                    "pipeline_owner": "alphabetical",
                    "alphabetical_role": "owned_section",
                    "material_reference_mode": "remissive",
                    "scripture_mode": "none",
                },
            }
        ],
        "nodes": [],
        "entries": [
            {
                "entry_key": "PL001:index:e1",
                "section_key": "PL001:index",
                "entry_raw": "Aaron, 10, ibid.",
                "source_span": span,
            }
        ],
        "refs": [
            {
                "entry_key": "PL001:index:e1",
                "ref_order": 2,
                "ref_kind": "unresolved",
                "ref_raw": "ibid.",
                "notation": [
                    {
                        "notation_key": "ibid",
                        "resolution_status": "resolved",
                        "inherits_from_ref_order": 1,
                    }
                ],
            }
        ],
        "scripture_refs": [],
        "unresolved": [],
        "decision_log": [],
        "notes": [],
    }

    validate_semantic_fragment_v2(
        fragment,
        source_root=source_root,
        expected_input_fingerprint="fp",
        expected_section_key="PL001:index",
        known_notation_keys={"ibid"},
    )

    fragment["refs"][0]["notation"][0]["notation_key"] = "invented"
    with pytest.raises(CompactPipelineError, match="versioned glossary"):
        validate_semantic_fragment_v2(
            fragment,
            source_root=source_root,
            expected_input_fingerprint="fp",
            expected_section_key="PL001:index",
            known_notation_keys={"ibid"},
        )

    fragment["refs"][0]["notation"][0]["notation_key"] = "ibid"
    fragment["sections"][0].update(
        {
            "section_kind": "author_index",
            "heading_raw": (
                "INDEX AUCTORUM ET OPERUM QUAE IN HOC TOMO CONTINENTUR"
            ),
        }
    )
    with pytest.raises(CompactPipelineError, match="outside the alphabetical pipeline"):
        validate_semantic_fragment_v2(
            fragment,
            source_root=source_root,
            expected_input_fingerprint="fp",
            expected_section_key="PL001:index",
            known_notation_keys={"ibid"},
        )


def test_loader_accepts_versioned_traceable_semantic_manifest(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_file = source_root / "page-010.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("INDEX NOMINUM\nAaron, 10\n")
    semantic_dir = tmp_path / "semantic"
    manifest_file = semantic_dir / "manifest.json"
    discovery_file = tmp_path / "discovery" / "discovery_manifest.json"
    volume_file = semantic_dir / "volume.json"
    coverage_file = semantic_dir / "coverage.json"
    fragment_file = semantic_dir / "sections" / "section_0001.json"
    fingerprint = "semantic-fingerprint"
    discovery_fingerprint = "discovery-fingerprint"
    span = {
        "file": str(source_file),
        "line_start": 1,
        "line_end": 2,
    }
    write_json(
        discovery_file,
        versioned(
            {
                "stage": "discovery",
                "volume_id": "PL001",
                "source_root": str(source_root),
                "input_fingerprint": discovery_fingerprint,
                "status": "complete",
                "inspected_files": [str(source_file)],
                "segments": [
                    {
                        "segment_id": "PL001:index",
                        "file": str(source_file),
                        "line_start": 1,
                        "line_end": 2,
                        "role": "owned",
                        "reason": "onomastic index",
                    }
                ],
                "expansion_requests": [],
                "unresolved": [],
            }
        ),
    )
    write_json(
        volume_file,
        {
            "volume_id": "PL001",
            "collection": "PL",
            "source_root": str(source_root),
            "volume_label": "PL001",
        },
    )
    write_json(coverage_file, {"entries_status": "extracted"})
    write_json(
        fragment_file,
        {
            "schema_version": 2,
            **prompt_reference_bundle(),
            "task_id": "PL001:semantic:index",
            "input_fingerprint": fingerprint,
            "consumed_spans": [span],
            "residual_spans": [],
            "sections": [
                {
                    "section_key": "PL001:index",
                    "volume_id": "PL001",
                    "section_order": 1,
                    "section_kind": "onomastic_person",
                    "heading_raw": "INDEX NOMINUM",
                    "file_start": str(source_file),
                    "file_end": str(source_file),
                    "raw_json": {
                        "pipeline_owner": "alphabetical",
                        "alphabetical_role": "owned_section",
                        "material_reference_mode": "remissive",
                        "scripture_mode": "none",
                    },
                }
            ],
            "nodes": [],
            "entries": [
                {
                    "entry_key": "PL001:index:e1",
                    "section_key": "PL001:index",
                    "entry_order": 1,
                    "entry_kind": "lemma",
                    "lemma_raw": "Aaron",
                    "entry_raw": "Aaron, 10",
                    "source_span": {
                        "file": str(source_file),
                        "line_start": 2,
                        "line_end": 2,
                    },
                }
            ],
            "refs": [
                {
                    "entry_key": "PL001:index:e1",
                    "ref_order": 1,
                    "scripture_ref_order": None,
                    "ref_kind": "editorial_page",
                    "ref_raw": "10",
                    "page_ref_raw": "10",
                    "page_ref_int": 10,
                    "target_file": None,
                    "target_file_probability": None,
                    "notation": [],
                }
            ],
            "scripture_refs": [],
            "unresolved": [],
            "decision_log": [],
            "notes": [],
        },
    )
    write_json(
        manifest_file,
        versioned(
            {
                "stage": "semantic",
                "volume_id": "PL001",
                "collection": "PL",
                "source_root": str(source_root),
                "input_fingerprint": fingerprint,
                "status": "complete",
                "volume_file": str(volume_file),
                "coverage_file": str(coverage_file),
                "section_fragments": [
                    {
                        "section_key": "PL001:index",
                        "file": str(fragment_file),
                    }
                ],
                "boundary_decisions": [],
            }
        ),
    )

    payload = load_semantic_manifest(
        manifest_file,
        expected_volume_id="PL001",
        expected_collection="PL",
        expected_source_root=source_root,
        expected_input_fingerprint=fingerprint,
        expected_discovery_file=discovery_file,
        expected_discovery_input_fingerprint=discovery_fingerprint,
    )

    assert payload["entries"][0]["source_span"]["line_start"] == 2
    assert payload["coverage"]["locator_status"] == "partial"
