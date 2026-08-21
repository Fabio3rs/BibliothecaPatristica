from __future__ import annotations

import json
from pathlib import Path

import pytest

import patristica_pipeline.alphabetical_analysis_pipeline as analysis_pipeline
from patristica_pipeline.alphabetical_analysis_db import (
    connect_analysis_db,
    ensure_volume,
    init_analysis_schema,
    replace_semantic_payload,
    review_occurrences,
    volume_status,
)
from patristica_pipeline.alphabetical_analysis_pipeline import (
    assemble_stage,
    discover_stage,
    extract_stage,
    locate_stage,
    verify_stage,
)
from patristica_pipeline.alphabetical_prompt_contract import (
    LOCATOR_CONTRACT_VERSION,
    OUTPUT_SCHEMA_VERSION,
    prompt_reference_bundle,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_discovery_redispatches_actionable_expansion_checkpoint(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    first_file = source_root / "page-001.txt"
    second_file = source_root / "page-002.txt"
    first_file.parent.mkdir(parents=True)
    first_file.write_text("INDEX\nAARON 10\n", encoding="utf-8")
    second_file.write_text("ABEL 11\nFINIS\n", encoding="utf-8")
    filtered_file = tmp_path / "filtered.json"
    write_json(filtered_file, {"candidate_files": [str(first_file)]})
    phases: list[str] = []
    prompts: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        prompts.append(prompt)
        discovery_input = json.loads(
            (expected_output.parent / "discovery_input.json").read_text(
                encoding="utf-8"
            )
        )
        expanded = "/expansion-" in phase_id
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "stage": "discovery",
                "volume_id": "PG001",
                "source_root": str(source_root),
                "input_fingerprint": discovery_input["input_fingerprint"],
                "status": "complete" if expanded else "needs_expansion",
                "inspected_files": (
                    [str(first_file), str(second_file)]
                    if expanded
                    else [str(first_file)]
                ),
                "segments": [
                    {
                        "segment_id": "PG001:index:start",
                        "file": str(first_file),
                        "line_start": 1,
                        "line_end": 2,
                        "role": "owned",
                        "reason": "alphabetical index starts here",
                    }
                ],
                "expansion_requests": (
                    []
                    if expanded
                    else [
                        {
                            "request_id": "continue-index",
                            "reason": "index continues after the inspected file",
                            "anchor_files": [str(first_file)],
                            "direction": "after",
                            "max_files": 2,
                        }
                    ]
                ),
                "unresolved": [],
            },
        )

    discovery, _, _ = analysis_pipeline._ensure_agent_discovery(
        volume_id="PG001",
        collection="PG",
        source_root=source_root,
        filtered_pages_file=filtered_file,
        intermediate_dir=tmp_path / "intermediate",
        agent_runner=agent_runner,
        force=False,
    )

    assert discovery["status"] == "complete"
    assert phases == ["extract/discovery", "extract/discovery/expansion-0001"]
    assert "DISCOVERY EXPANSION ROUND 1" in prompts[1]


def test_discovery_fingerprint_uses_prefilter_content_not_mtime(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_file = source_root / "page-001.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("INDEX NOMINUM\nAARON 10\n", encoding="utf-8")
    filtered_file = tmp_path / "filtered.json"
    write_json(filtered_file, {"candidate_files": [str(source_file)]})
    phases: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        discovery_input = json.loads(
            (expected_output.parent / "discovery_input.json").read_text(
                encoding="utf-8"
            )
        )
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "stage": "discovery",
                "volume_id": "PG001",
                "source_root": str(source_root),
                "input_fingerprint": discovery_input["input_fingerprint"],
                "status": "complete",
                "inspected_files": [str(source_file)],
                "segments": [],
                "expansion_requests": [],
                "unresolved": [],
            },
        )

    kwargs = {
        "volume_id": "PG001",
        "collection": "PG",
        "source_root": source_root,
        "filtered_pages_file": filtered_file,
        "intermediate_dir": tmp_path / "intermediate",
        "agent_runner": agent_runner,
        "force": False,
    }
    first = analysis_pipeline._ensure_agent_discovery(**kwargs)
    stat = filtered_file.stat()
    filtered_file.touch()
    assert filtered_file.stat().st_mtime_ns >= stat.st_mtime_ns
    second = analysis_pipeline._ensure_agent_discovery(**kwargs)

    assert phases == ["extract/discovery"]
    assert second[2] == first[2]


def test_discovery_skip_fingerprint_reuses_completed_manifest(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_file = source_root / "page-001.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("INDEX NOMINUM\nAARON 10\n", encoding="utf-8")
    filtered_file = tmp_path / "filtered.json"
    write_json(filtered_file, {"candidate_files": [str(source_file)]})
    phases: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        discovery_input = json.loads(
            (expected_output.parent / "discovery_input.json").read_text(
                encoding="utf-8"
            )
        )
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "stage": "discovery",
                "volume_id": "PG001",
                "source_root": str(source_root),
                "input_fingerprint": discovery_input["input_fingerprint"],
                "status": "complete",
                "inspected_files": [str(source_file)],
                "segments": [],
                "expansion_requests": [],
                "unresolved": [],
            },
        )

    kwargs = {
        "volume_id": "PG001",
        "collection": "PG",
        "source_root": source_root,
        "filtered_pages_file": filtered_file,
        "intermediate_dir": tmp_path / "intermediate",
        "agent_runner": agent_runner,
        "force": False,
    }
    first = analysis_pipeline._ensure_agent_discovery(**kwargs)
    write_json(
        filtered_file,
        {"candidate_files": [str(source_file)], "contract_changed": True},
    )
    second = analysis_pipeline._ensure_agent_discovery(
        **kwargs,
        skip_fingerprint=True,
    )

    assert phases == ["extract/discovery"]
    assert second[2] == first[2]


def scripture_semantic(source_root: Path, *, ref_count: int = 3) -> dict:
    entry_key = "PL001:scripture:e1"
    refs = [
        {
            "entry_key": entry_key,
            "ref_order": order,
            "scripture_ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": str(100 + order),
            "page_ref_raw": str(100 + order),
            "page_ref_int": 100 + order,
            "target_file": None,
            "target_file_probability": None,
        }
        for order in range(1, ref_count + 1)
    ]
    refs.append(
        {
            "entry_key": entry_key,
            "ref_order": ref_count + 1,
            "scripture_ref_order": 1,
            "ref_kind": "editorial_range",
            "ref_raw": "120-135",
            "range_start_raw": "120",
            "range_end_raw": "135",
            "target_file": None,
            "target_file_probability": None,
        }
    )
    return {
        "schema_version": 1,
        "generated_at": None,
        "volume": {
            "volume_id": "PL001",
            "collection": "PL",
            "source_root": str(source_root),
            "volume_label": "PL001",
        },
        "sections": [
            {
                "section_key": "PL001:scripture",
                "volume_id": "PL001",
                "section_order": 1,
                "section_kind": "scripture_index",
                "heading_raw": "INDEX SCRIPTURAE",
                "file_start": str(source_root / "index-001.txt"),
                "file_end": str(source_root / "index-001.txt"),
            }
        ],
        "nodes": [],
        "entries": [
            {
                "entry_key": entry_key,
                "section_key": "PL001:scripture",
                "entry_order": 1,
                "entry_kind": "scripture_citation",
                "lemma_raw": "I Cor. 1, 4",
                "lemma_norm": "1 Coríntios 1,4",
                "entry_raw": "I Cor. 1, 4 ... 101, 102, 103, 120-135",
                "context_raw": "I Cor. 1, 4 ... 101, 102, 103, 120-135",
            }
        ],
        "refs": refs,
        "scripture_refs": [
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_role": "citation",
                "ref_raw": "I Cor. 1, 4",
                "ref_norm": "1 Coríntios 1,4",
                "book_raw": "I Cor.",
                "book_norm": "1 Coríntios",
                "book_key": "1 corintios",
                "chapter_start": 1,
                "verse_start": 4,
            }
        ],
        "coverage": {"locator_status": "partial"},
        "notes": [],
    }


def write_semantic_manifest(
    manifest_file: Path,
    *,
    source_root: Path,
) -> None:
    semantic_dir = manifest_file.parent
    semantic_input = json.loads(
        (semantic_dir.parent / "semantic_input.json").read_text(encoding="utf-8")
    )
    payload = scripture_semantic(source_root, ref_count=1)
    payload["refs"] = payload["refs"][:1]
    volume_file = semantic_dir / "volume.json"
    coverage_file = semantic_dir / "coverage.json"
    section_file = semantic_dir / "sections" / "section_0001.json"
    write_json(volume_file, payload["volume"])
    write_json(coverage_file, payload["coverage"])
    write_json(
        section_file,
        {
            key: payload[key]
            for key in (
                "sections",
                "nodes",
                "entries",
                "refs",
                "scripture_refs",
                "notes",
            )
        },
    )
    write_json(
        manifest_file,
        {
            "schema_version": 1,
            "stage": "semantic",
            "volume_id": "PL001",
            "collection": "PL",
            "source_root": str(source_root),
            "semantic_input_fingerprint": semantic_input[
                "semantic_input_fingerprint"
            ],
            "status": "complete",
            "volume_file": str(volume_file),
            "coverage_file": str(coverage_file),
            "section_fragments": [
                {
                    "section_key": "PL001:scripture",
                    "file": str(section_file),
                }
            ],
            "boundary_decisions": [],
        },
    )


def test_analysis_db_keeps_one_entry_and_one_occurrence_per_printed_locator(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    db_path = tmp_path / "analysis.db"
    with connect_analysis_db(db_path) as con:
        init_analysis_schema(con)
        counts = replace_semantic_payload(
            con,
            payload=scripture_semantic(source_root),
        )
        assert counts == {
            "sections": 1,
            "entries": 1,
            "occurrences": 4,
            "scripture_refs": 1,
        }
        rows = review_occurrences(con, volume_id="PL001")
        assert [row["page_ref_int"] for row in rows[:3]] == [101, 102, 103]
        assert rows[3]["range_start_raw"] == "120"
        assert rows[3]["range_end_raw"] == "135"
        assert len({row["entry_key"] for row in rows}) == 1
        fts = review_occurrences(
            con,
            volume_id="PL001",
            text_query='"Corintios"',
        )
        assert len(fts) == 4


def test_discover_materializes_pages_until_next_boundary(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    files = []
    for page in range(1, 6):
        path = source_root / f"PL001-{page:03d}.txt"
        path.write_text(f"page {page}", encoding="utf-8")
        files.append(path)
    filtered = {
        "candidate_files": [str(files[1]), str(files[4])],
        "tail_files": [],
        "candidate_sections": [
            {
                "file": str(files[1]),
                "heading": "INDEX NOMINUM",
                "role": "section_heading",
            },
            {
                "file": str(files[4]),
                "heading": "ORDO RERUM",
                "role": "alphabetical_stop_boundary",
            },
        ],
    }
    db_path = tmp_path / "analysis.db"
    discover_stage(
        analysis_db=db_path,
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages=filtered,
    )
    with connect_analysis_db(db_path) as con:
        candidate_pages = con.execute(
            """SELECT file_seq FROM analysis_discovered_pages
            WHERE ownership_role='candidate' ORDER BY file_seq"""
        ).fetchall()
        boundaries = con.execute(
            """SELECT file_seq FROM analysis_discovered_pages
            WHERE ownership_role='boundary' ORDER BY file_seq"""
        ).fetchall()
    assert [row["file_seq"] for row in candidate_pages] == [2, 3, 4]
    assert [row["file_seq"] for row in boundaries] == [5]


def test_discover_agent_manifest_is_persisted_as_line_segments(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL085" / "text"
    source_file = source_root / "page-542.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("\n".join(f"line {line}" for line in range(1, 101)))
    filtered = {
        "volume_id": "PL085",
        "collection": "PL",
        "source_root": str(source_root),
        "candidate_files": [str(source_file)],
        "candidate_sections": [],
        "tail_files": [str(source_file)],
    }
    filtered_file = tmp_path / "filtered.json"
    write_json(filtered_file, filtered)
    phases: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        discovery_input = json.loads(
            (expected_output.parent / "discovery_input.json").read_text(
                encoding="utf-8"
            )
        )
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "stage": "discovery",
                "volume_id": "PL085",
                "source_root": str(source_root),
                "input_fingerprint": discovery_input["input_fingerprint"],
                "status": "complete",
                "inspected_files": [str(source_file)],
                "segments": [
                    {
                        "segment_id": "PL085:542:owned",
                        "file": str(source_file),
                        "line_start": 1,
                        "line_end": 50,
                        "role": "owned",
                        "reason": "alphabetical tail",
                    },
                    {
                        "segment_id": "PL085:542:boundary",
                        "file": str(source_file),
                        "line_start": 51,
                        "line_end": 100,
                        "role": "boundary",
                        "reason": "ORDO RERUM",
                    },
                ],
                "expansion_requests": [],
                "unresolved": [],
            },
        )

    db_path = tmp_path / "analysis.db"
    summary = discover_stage(
        analysis_db=db_path,
        volume_id="PL085",
        collection="PL",
        source_root=source_root,
        filtered_pages=filtered,
        filtered_pages_file=filtered_file,
        intermediate_dir=tmp_path / "intermediate",
        agent_runner=agent_runner,
    )

    with connect_analysis_db(db_path) as con:
        segments = con.execute(
            """SELECT line_start, line_end, ownership_role
            FROM analysis_discovered_segments
            WHERE volume_id='PL085' ORDER BY line_start"""
        ).fetchall()
    assert phases == ["discover/semantic"]
    assert summary["segment_count"] == 2
    assert [tuple(row) for row in segments] == [
        (1, 50, "owned"),
        (51, 100, "boundary"),
    ]


def test_named_stages_resume_and_assemble_without_locator_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    index_file = source_root / "index-001.txt"
    target_file = source_root / "page-050.txt"
    source_root.mkdir(parents=True)
    index_file.write_text("INDEX SCRIPTURAE\nI Cor. 1, 4 ... 101", encoding="utf-8")
    target_file.write_text(
        "101 HOMILIA 102\n<apparatus>I Cor. 1, 4 cod. A</apparatus>",
        encoding="utf-8",
    )
    filtered = {
        "volume_id": "PL001",
        "source_root": str(source_root),
        "collection": "PL",
        "profile": "alphabetical",
        "source": "test",
        "candidate_files": [str(index_file)],
        "tail_files": [str(index_file)],
        "candidate_sections": [
            {
                "file": str(index_file),
                "heading": "INDEX SCRIPTURAE",
                "role": "section_heading",
            }
        ],
    }
    filtered_file = tmp_path / "filtered.json"
    write_json(filtered_file, filtered)
    analysis_db = tmp_path / "analysis.db"
    intermediate = tmp_path / "intermediate"
    phases: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        if phase_id == "extract/discovery":
            discovery_input = json.loads(
                (expected_output.parent / "discovery_input.json").read_text(
                    encoding="utf-8"
                )
            )
            write_json(
                expected_output,
                {
                    "schema_version": OUTPUT_SCHEMA_VERSION,
                    **prompt_reference_bundle(),
                    "stage": "discovery",
                    "volume_id": "PL001",
                    "source_root": str(source_root),
                    "input_fingerprint": discovery_input["input_fingerprint"],
                    "status": "complete",
                    "inspected_files": [str(index_file), str(target_file)],
                    "segments": [
                        {
                            "segment_id": "PL001:index",
                            "file": str(index_file),
                            "line_start": 1,
                            "line_end": 2,
                            "role": "owned",
                            "reason": "scripture index",
                        }
                    ],
                    "expansion_requests": [],
                    "unresolved": [],
                },
            )
            return
        assert phase_id == "extract/semantic"
        semantic_input = json.loads(
            (intermediate / "semantic_input.json").read_text(encoding="utf-8")
        )
        discovery_file = intermediate / "discovery" / "discovery_manifest.json"
        source_snapshot = json.loads(
            (intermediate / "ocr_source_snapshot.json").read_text(encoding="utf-8")
        )
        assert semantic_input["discovery_sha256"] == analysis_pipeline.file_sha256(
            discovery_file
        )
        assert semantic_input["source_snapshot_fingerprint"] == source_snapshot[
            "source_snapshot_fingerprint"
        ]
        mechanical_file = intermediate / "mechanical_analysis.json"
        assert semantic_input["mechanical_analysis_sha256"] == (
            analysis_pipeline.file_sha256(mechanical_file)
        )
        assert str(mechanical_file) in prompt
        mechanical = json.loads(mechanical_file.read_text(encoding="utf-8"))
        assert mechanical["inspected_file_count"] == 2
        write_semantic_manifest(expected_output, source_root=source_root)

    monkeypatch.setattr(
        analysis_pipeline,
        "estimate_editorial_pages",
        lambda **kwargs: {
            "volume_id": "PL001",
            "collection": "PL",
            "files": [
                {
                    "file": str(target_file),
                    "best_left_page": 101,
                    "best_right_page": 102,
                    "confidence": 0.95,
                    "evidence": [{"kind": "header_pair"}],
                }
            ],
        },
    )
    first = discover_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages=filtered,
    )
    repeated = discover_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages=filtered,
    )
    assert first["status"] == "complete"
    assert repeated["status"] == "skipped"
    extract_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages_file=filtered_file,
        intermediate_dir=intermediate,
        agent_runner=agent_runner,
    )
    located = locate_stage(
        analysis_db=analysis_db,
        scripture_db=tmp_path / "missing.db",
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        intermediate_dir=intermediate,
        agent_runner=agent_runner,
    )
    assert located["resolved"] == 1
    scripture_candidate_stage = Path(
        located["scripture_candidate_stage_file"]
    )
    assert scripture_candidate_stage.is_file()
    assert scripture_candidate_stage.with_suffix(
        ".json.checkpoint.json"
    ).is_file()
    verified = verify_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        source_root=source_root,
        intermediate_dir=intermediate,
        agent_runner=agent_runner,
    )
    assert verified["owned_item_count"] == 0
    output_file = tmp_path / "PL001.json"
    assembled = assemble_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        source_root=source_root,
        output_file=output_file,
    )
    assert assembled["status"] == "complete"
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["refs"][0]["target_file"] == str(target_file)
    assert phases == ["extract/discovery", "extract/semantic"]
    with connect_analysis_db(analysis_db) as con:
        status = volume_status(con, "PL001")
    assert status["stages"]["assemble"]["status"] == "complete"
    changed_filtered = {
        **filtered,
        "candidate_sections": [
            *filtered["candidate_sections"],
            {
                "file": str(target_file),
                "heading": "INDEX TEST",
                "role": "section_heading",
            },
        ],
    }
    discover_stage(
        analysis_db=analysis_db,
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages=changed_filtered,
        force=True,
    )
    with connect_analysis_db(analysis_db) as con:
        stale_status = volume_status(con, "PL001")
    assert stale_status["stages"]["discover"]["status"] == "complete"
    assert stale_status["stages"]["extract"]["status"] == "stale"
    assert stale_status["stages"]["assemble"]["status"] == "stale"


def test_locate_resumes_scripture_fusion_after_later_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    (source_root / "index-001.txt").write_text(
        "INDEX SCRIPTURAE\nI Cor. 1, 4 ... 101",
        encoding="utf-8",
    )
    (source_root / "page-050.txt").write_text(
        "101 HOMILIA 102\nI Cor. 1, 4",
        encoding="utf-8",
    )
    analysis_db = tmp_path / "analysis.db"
    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        replace_semantic_payload(
            con,
            payload=scripture_semantic(source_root, ref_count=1),
        )

    monkeypatch.setattr(
        analysis_pipeline,
        "estimate_editorial_pages",
        lambda **kwargs: {"entries": []},
    )
    fallback_calls = 0
    original_fallback = analysis_pipeline.add_scripture_evidence_candidates

    def counted_fallback(*args, **kwargs):
        nonlocal fallback_calls
        fallback_calls += 1
        return original_fallback(*args, **kwargs)

    monkeypatch.setattr(
        analysis_pipeline,
        "add_scripture_evidence_candidates",
        counted_fallback,
    )
    monkeypatch.setattr(
        analysis_pipeline,
        "run_scripture_table_repairs",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("later failure")),
    )
    kwargs = {
        "analysis_db": analysis_db,
        "scripture_db": tmp_path / "missing.db",
        "volume_id": "PL001",
        "collection": "PL",
        "source_root": source_root,
        "intermediate_dir": tmp_path / "intermediate",
        "agent_runner": lambda *args: None,
    }
    with pytest.raises(RuntimeError, match="later failure"):
        locate_stage(**kwargs)
    assert fallback_calls == 1

    monkeypatch.setattr(
        analysis_pipeline,
        "run_scripture_table_repairs",
        lambda **kwargs: {"status": "not_needed", "groups": []},
    )
    result = locate_stage(**kwargs)

    assert result["status"] == "complete"
    assert fallback_calls == 1


def test_verify_upgrades_legacy_item_and_supplies_facsimile_hint(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG003" / "text"
    images_root = tmp_path / "PG003" / "images"
    source_root.mkdir(parents=True)
    images_root.mkdir()
    index_file = source_root / "index-010.txt"
    target_file = source_root / "page-001.txt"
    image_file = images_root / "PG003-001.png"
    index_file.write_text("INDEX\nGregorius 101", encoding="utf-8")
    target_file.write_text("101 TEST 102\nGregorius", encoding="utf-8")
    image_file.write_bytes(b"")
    db_path = tmp_path / "analysis.db"
    legacy_item = {
        "locator_key": "PG003:e1::ref:000001",
        "entry_key": "PG003:e1",
        "ref_order": 1,
        "ref_kind": "editorial_page",
        "ref_raw": "101",
        "page_ref_raw": "101",
        "page_ref_int": 101,
        "cited_pages": [101],
        "candidates": [
            {
                "file": str(target_file),
                "probability": 0.6,
                "evidence": [{"kind": "header_pair", "detail": "101/102"}],
            }
        ],
    }
    with connect_analysis_db(db_path) as con:
        init_analysis_schema(con)
        ensure_volume(
            con,
            volume_id="PG003",
            collection="PG",
            source_root=source_root,
        )
        con.execute(
            """INSERT INTO analysis_sections(
                section_key, volume_id, section_order, section_kind,
                heading_raw, file_start, file_end, semantic_json
            ) VALUES ('PG003:index', 'PG003', 1, 'onomastic_person',
                      'INDEX ONOMASTICUS', ?, ?, '{}')""",
            (str(index_file), str(index_file)),
        )
        con.execute(
            """INSERT INTO analysis_entries(
                entry_key, volume_id, section_key, entry_order, entry_kind,
                lemma_raw, entry_raw, source_file, semantic_json
            ) VALUES ('PG003:e1', 'PG003', 'PG003:index', 1, 'person',
                      'Gregorius', 'Gregorius 101', ?, '{}')""",
            (str(index_file),),
        )
        con.execute(
            """INSERT INTO analysis_occurrences(
                occurrence_key, volume_id, entry_key, ref_order, ref_kind,
                ref_raw, locator_status, ref_json, locator_item_json
            ) VALUES (?, 'PG003', 'PG003:e1', 1, 'editorial_page', '101',
                      'pending', '{}', ?)""",
            (legacy_item["locator_key"], json.dumps(legacy_item)),
        )
        con.commit()

    seen_item: dict[str, object] = {}

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        shard = json.loads(
            expected_output.with_name("locator-0001_input.json").read_text(
                encoding="utf-8"
            )
        )
        item = shard["items"][0]
        seen_item.update(item)
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                "volume_id": "PG003",
                "input_fingerprint": shard["input_fingerprint"],
                "results": [
                    {
                        "entry_key": "PG003:e1",
                        "ref_order": 1,
                        "status": "unrecoverable_ocr",
                        "target_file": None,
                        "reason": "test keeps the locator terminal",
                        "attempted_files": [str(target_file)],
                    }
                ],
            },
        )

    summary = verify_stage(
        analysis_db=db_path,
        volume_id="PG003",
        source_root=source_root,
        intermediate_dir=tmp_path / "intermediate",
        agent_runner=agent_runner,
    )

    assert summary["contract_upgrade"]["updated"] == 1
    assert seen_item["locator_format_version"] == 2
    assert "locator_contract" in seen_item
    assert seen_item["candidates"][0]["facsimile_hint"]["image_path"] == str(
        image_file.resolve()
    )
