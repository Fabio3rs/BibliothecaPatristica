from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patristica_pipeline.alphabetical_compact_driver as compact_driver
from patristica_pipeline.alphabetical_compact_driver import (
    build_discovery_prompt,
    build_locator_prompt,
    build_repair_prompt,
    build_semantic_prompt,
    run_compact_extraction,
    run_scripture_table_repairs,
)
from patristica_pipeline.alphabetical_prompt_contract import (
    LOCATOR_CONTRACT_VERSION,
    OUTPUT_SCHEMA_VERSION,
    prompt_reference_bundle,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_semantic_manifest(
    manifest_file: Path,
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    ref_count: int = 2,
) -> None:
    semantic_dir = manifest_file.parent
    semantic_input = json.loads(
        (semantic_dir.parent / "semantic_input.json").read_text(encoding="utf-8")
    )
    volume_file = semantic_dir / "volume.json"
    coverage_file = semantic_dir / "coverage.json"
    section_file = semantic_dir / "sections" / "section_0001.json"
    write_json(
        volume_file,
        {
            "volume_id": volume_id,
            "collection": collection,
            "source_root": str(source_root),
            "volume_label": volume_id,
        },
    )
    write_json(coverage_file, {})
    write_json(
        section_file,
        {
            "sections": [
                {
                    "section_key": f"{volume_id}:index",
                    "volume_id": volume_id,
                    "section_kind": "onomastic_person",
                    "heading_raw": "INDEX NOMINUM",
                }
            ],
            "nodes": [],
            "entries": [
                {
                    "entry_key": f"{volume_id}:index:e1",
                    "section_key": f"{volume_id}:index",
                    "entry_order": 1,
                    "entry_kind": "person",
                    "lemma_raw": "Aaron",
                    "entry_raw": "Aaron, 101, 102",
                    "target_file_best": None,
                }
            ],
            "refs": [
                {
                    "entry_key": f"{volume_id}:index:e1",
                    "ref_order": order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(100 + order),
                    "page_ref_raw": str(100 + order),
                    "page_ref_int": 100 + order,
                    "target_file": None,
                    "target_file_probability": None,
                }
                for order in range(1, ref_count + 1)
            ],
            "scripture_refs": [],
            "notes": [],
        },
    )
    write_json(
        manifest_file,
        {
            "schema_version": 1,
            "stage": "semantic",
            "volume_id": volume_id,
            "collection": collection,
            "source_root": str(source_root),
            "semantic_input_fingerprint": semantic_input[
                "semantic_input_fingerprint"
            ],
            "status": "complete",
            "volume_file": str(volume_file),
            "coverage_file": str(coverage_file),
            "section_fragments": [
                {
                    "section_key": f"{volume_id}:index",
                    "file": str(section_file),
                }
            ],
            "boundary_decisions": [],
        },
    )


def test_semantic_prompt_is_path_only_and_makes_ordo_a_boundary(tmp_path: Path) -> None:
    prompt = build_semantic_prompt(
        volume_id="PL001",
        collection="PL",
        source_root=tmp_path / "text",
        filtered_pages_file=tmp_path / "filtered.json",
        semantic_dir=tmp_path / "semantic",
        manifest_file=tmp_path / "semantic" / "manifest.json",
        mechanical_analysis_file=tmp_path / "mechanical_analysis.json",
    )

    assert "PHASE: SEMANTIC EXTRACTION" in prompt
    assert "one schema_version=2 fragment per" in prompt
    assert "pipeline_owner=alphabetical" in prompt
    assert "no_index_section" in prompt
    assert "consumed_spans" in prompt
    assert "printed range as one editorial_range" in prompt
    assert str(tmp_path / "mechanical_analysis.json") in prompt
    assert "glossario_notacao_editorial_indices.md" in prompt
    assert "assembled_fragments.json" not in prompt
    assert len(prompt.encode("utf-8")) < 8_000


def test_onomastic_helper_queries_separate_name_from_editorial_description() -> None:
    queries = compact_driver._search_queries(
        {
            "section_kind": "onomastic_person",
            "lemma_raw": "Gregorius II, papa Romanus",
        }
    )

    assert "Gregorius II" in queries
    assert "Gregorius" in queries
    assert "Gregorius secundus" in queries
    assert "Gregorius secundi" in queries

    leo_queries = compact_driver._search_queries(
        {"section_kind": "onomastic_person", "lemma_raw": "Leo III"}
    )
    assert "Leoni" in leo_queries
    assert "Leonis" in leo_queries


def test_compact_helper_candidate_preserves_internal_locator_evidence() -> None:
    candidate = {
        "file": "/corpus/page.txt",
        "probability": 0.99,
        "evidence": [
            {"kind": f"generic_{index}", "raw": str(index), "weight": 1.0}
            for index in range(8)
        ]
        + [
            {"kind": "body_locator_name_unique", "raw": "Gregorius", "weight": 5.0},
            {"kind": "body_locator_match", "raw": "114", "weight": 2.5},
            {
                "kind": "internal_locator_vs_editorial_sequence",
                "raw": "114 vs 153-154",
                "weight": 3.5,
            },
        ],
    }

    compact = compact_driver._compact_helper_candidate(candidate)
    kinds = {item["kind"] for item in compact["evidence"]}

    assert "body_locator_name_unique" in kinds
    assert "body_locator_match" in kinds
    assert "internal_locator_vs_editorial_sequence" in kinds


def test_helper_request_supplies_neighbor_name_groups_and_sibling_hints(
    tmp_path: Path,
) -> None:
    items = []
    for order, lemma, page in (
        (1, "Gregorius II", 114),
        (2, "Zacharias", 119),
        (3, "Stephanus II", 121),
    ):
        items.append(
            {
                "locator_key": f"e{order}::ref:000001",
                "entry_key": f"e{order}",
                "entry_order": order,
                "section_key": "s1",
                "section_kind": "onomastic_person",
                "section_heading": "INDEX ONOMASTICUS",
                "lemma_raw": lemma,
                "entry_excerpt": f"{lemma} {page}",
                "page_ref_raw": str(page),
                "cited_pages": [page],
            }
        )

    request = compact_driver._helper_request(
        items,
        volume_id="PLX",
        source_root=tmp_path,
    )

    middle = request["entries"][1]
    assert middle["sibling_page_hint_ints"] == [119]
    flattened = {query for group in middle["neighbor_query_groups"] for query in group}
    assert "Gregorius" in flattened
    assert "Stephanus II" in flattened


def test_locator_and_repair_prompts_preserve_genuine_ambiguity(tmp_path: Path) -> None:
    locator = build_locator_prompt(
        volume_id="PO025",
        source_root=tmp_path / "text",
        shard_file=tmp_path / "locator_input.json",
        result_file=tmp_path / "locator_result.json",
    )
    repair = build_repair_prompt(
        volume_id="PO025",
        source_root=tmp_path / "text",
        repair_request_file=tmp_path / "repair_request.json",
        repair_result_file=tmp_path / "repair_result.json",
    )

    assert "ambiguous keeps target_file null" in locator
    assert "readable competing_candidates" in locator
    assert "page-specific" in locator
    assert "Preserve ambiguous" in repair
    assert "false unrecoverable_ocr" in repair
    assert "Do not leave `ambiguous`" not in repair


def test_discovery_prompt_is_segmented_and_path_only(tmp_path: Path) -> None:
    prompt = build_discovery_prompt(
        volume_id="PL085",
        collection="PL",
        source_root=tmp_path / "text",
        prefilter_file=tmp_path / "prefilter.json",
        discovery_file=tmp_path / "discovery.json",
        input_fingerprint="abc",
    )

    assert "PHASE: DISCOVERY" in prompt
    assert "owned|boundary|context|uncertain" in prompt
    assert "middle of a file" in prompt
    assert "Do not extract entries" in prompt
    assert "alphabetical_discovery_manifest.schema.json" in prompt


def test_compact_driver_assembles_without_final_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    target = source_root / "page-050.txt"
    target.parent.mkdir(parents=True)
    target.write_text("101 INDEX 102", encoding="utf-8")
    phases: list[str] = []
    semantic_validations: list[Path] = []

    monkeypatch.setattr(
        compact_driver,
        "estimate_editorial_pages",
        lambda **kwargs: {
            "volume_id": "PL001",
            "collection": "PL",
            "files": [
                {
                    "file": str(target),
                    "best_left_page": 101,
                    "best_right_page": 102,
                    "confidence": 0.95,
                    "evidence": [{"kind": "header_pair"}],
                }
            ],
        },
    )

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        if phase_id == "semantic":
            write_semantic_manifest(
                expected_output,
                volume_id="PL001",
                collection="PL",
                source_root=source_root,
            )
            return
        assert phase_id == "locators/locator-0001"
        shard_file = expected_output.with_name(
            expected_output.name.replace("_result.json", "_input.json")
        )
        shard = json.loads(shard_file.read_text(encoding="utf-8"))
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                "volume_id": "PL001",
                "input_fingerprint": shard["input_fingerprint"],
                "results": [
                    {
                        "entry_key": item["entry_key"],
                        "ref_order": item["ref_order"],
                        "status": "resolved",
                        "target_file": str(target),
                        "confidence": 0.9,
                        "evidence": [{"kind": "header_pair"}],
                    }
                    for item in shard["items"]
                ],
            },
        )

    output_file = tmp_path / "out.json"
    summary = run_compact_extraction(
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages_file=tmp_path / "filtered.json",
        intermediate_dir=tmp_path / "intermediate",
        output_file=output_file,
        agent_runner=agent_runner,
        semantic_validator=lambda path: semantic_validations.append(path),
        locator_workers=2,
    )

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert phases == ["semantic", "locators/locator-0001"]
    assert semantic_validations == [
        tmp_path / "intermediate" / "semantic_payload.json"
    ]
    assert summary["repair_ran"] is False
    assert [ref["target_file"] for ref in payload["refs"]] == [str(target), str(target)]
    assert payload["entries"][0]["target_file_best"] == str(target)
    scripture_evidence = json.loads(
        Path(summary["scripture_evidence_file"]).read_text(encoding="utf-8")
    )
    assert scripture_evidence["scripture_locator_count"] == 0
    assert scripture_evidence["files_read"] == 0


def test_compact_driver_skips_locator_agent_for_unique_dual_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    target = source_root / "page-050.txt"
    target.parent.mkdir(parents=True)
    target.write_text(
        "101 HOMILIA 102\n<apparatus>I Cor. 1, 4 cod. A; ms. B</apparatus>",
        encoding="utf-8",
    )
    phases: list[str] = []
    monkeypatch.setattr(
        compact_driver,
        "estimate_editorial_pages",
        lambda **kwargs: {
            "volume_id": "PL001",
            "collection": "PL",
            "files": [
                {
                    "file": str(target),
                    "best_left_page": 101,
                    "best_right_page": 102,
                    "confidence": 0.95,
                    "evidence": [{"kind": "header_pair"}],
                }
            ],
        },
    )

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        assert phase_id == "semantic"
        write_semantic_manifest(
            expected_output,
            volume_id="PL001",
            collection="PL",
            source_root=source_root,
            ref_count=1,
        )
        fragment_file = expected_output.parent / "sections" / "section_0001.json"
        fragment = json.loads(fragment_file.read_text(encoding="utf-8"))
        fragment["sections"][0]["section_kind"] = "scripture_index"
        fragment["entries"][0].update(
            {
                "entry_kind": "scripture_citation",
                "lemma_raw": "I Cor. 1, 4",
                "entry_raw": "I Cor. 1, 4 ... 101",
            }
        )
        fragment["refs"][0]["scripture_ref_order"] = 1
        fragment["scripture_refs"] = [
            {
                "entry_key": "PL001:index:e1",
                "ref_order": 1,
                "ref_role": "citation",
                "ref_raw": "I Cor. 1, 4",
                "book_raw": "I Cor.",
                "book_norm": "1 Coríntios",
                "chapter_start": 1,
                "verse_start": 4,
            }
        ]
        write_json(fragment_file, fragment)

    output_file = tmp_path / "out.json"
    summary = run_compact_extraction(
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages_file=tmp_path / "filtered.json",
        intermediate_dir=tmp_path / "intermediate",
        output_file=output_file,
        agent_runner=agent_runner,
        locator_workers=2,
    )

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert phases == ["semantic"]
    assert summary["deterministic_resolved_count"] == 1
    assert summary["agent_locator_item_count"] == 0
    assert payload["refs"][0]["target_file"] == str(target)


def test_compact_driver_repairs_only_pending_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PO001" / "text"
    target = source_root / "page-001.txt"
    target.parent.mkdir(parents=True)
    target.write_text("101 text", encoding="utf-8")
    phases: list[str] = []
    monkeypatch.setattr(
        compact_driver,
        "resolve_index_targets",
        lambda request: {
            "entries": [
                {
                    "entry_id": entry["entry_id"],
                    "status": "unresolved",
                    "candidates": [],
                }
                for entry in request["entries"]
            ]
        },
    )

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        if phase_id == "semantic":
            write_semantic_manifest(
                expected_output,
                volume_id="PO001",
                collection="PO",
                source_root=source_root,
                ref_count=1,
            )
        elif phase_id.startswith("locators/"):
            shard_file = expected_output.with_name(
                expected_output.name.replace("_result.json", "_input.json")
            )
            shard = json.loads(shard_file.read_text(encoding="utf-8"))
            write_json(
                expected_output,
                {
                    "schema_version": OUTPUT_SCHEMA_VERSION,
                    **prompt_reference_bundle(),
                    "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                    "input_fingerprint": shard["input_fingerprint"],
                    "results": [
                        {
                            "entry_key": "PO001:index:e1",
                            "ref_order": 1,
                            "status": "ambiguous",
                            "attempted_files": [str(target)],
                        }
                    ],
                },
            )
        else:
            request = json.loads(
                (expected_output.parent / "repair_request.json").read_text(
                    encoding="utf-8"
                )
            )
            assert request["item_count"] == 1
            assert "semantic_payload" not in request
            write_json(
                expected_output,
                {
                    "schema_version": OUTPUT_SCHEMA_VERSION,
                    **prompt_reference_bundle(),
                    "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                    "input_fingerprint": request["request_fingerprint"],
                    "results": [
                        {
                            "entry_key": "PO001:index:e1",
                            "ref_order": 1,
                            "status": "unrecoverable_ocr",
                            "target_file": None,
                            "reason": "printed header illegible",
                            "attempted_evidence": [
                                {"file": str(target), "result": "no readable digits"}
                            ],
                        }
                    ]
                },
            )

    output_file = tmp_path / "out.json"
    summary = run_compact_extraction(
        volume_id="PO001",
        collection="PO",
        source_root=source_root,
        filtered_pages_file=tmp_path / "filtered.json",
        intermediate_dir=tmp_path / "intermediate",
        output_file=output_file,
        agent_runner=agent_runner,
    )
    payload = json.loads(output_file.read_text(encoding="utf-8"))

    assert phases == ["semantic", "locators/locator-0001", "repair"]
    assert summary["repair_ran"] is True
    assert payload["refs"][0]["target_file"] is None
    assert payload["coverage"]["locator_status"] == "partial"
    estimator = json.loads(
        (tmp_path / "intermediate" / "editorial_page_map.json").read_text(
            encoding="utf-8"
        )
    )
    assert estimator["fallback"] == "index_target_locator"


def test_semantic_validator_failure_gets_one_checkpoint_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    phases: list[str] = []
    validations = 0
    monkeypatch.setattr(
        compact_driver,
        "estimate_editorial_pages",
        lambda **kwargs: {"volume_id": "PL001", "collection": "PL", "files": []},
    )

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        phases.append(phase_id)
        if phase_id == "semantic":
            write_semantic_manifest(
                expected_output,
                volume_id="PL001",
                collection="PL",
                source_root=source_root,
                ref_count=0,
            )
        else:
            assert phase_id == "semantic_repair"
            assert "validation_error.txt" in prompt

    def semantic_validator(path: Path) -> None:
        nonlocal validations
        validations += 1
        if validations == 1:
            raise SystemExit("entries[0] validation failed")

    summary = run_compact_extraction(
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages_file=tmp_path / "filtered.json",
        intermediate_dir=tmp_path / "intermediate",
        output_file=tmp_path / "out.json",
        agent_runner=agent_runner,
        semantic_validator=semantic_validator,
    )

    assert phases == ["semantic", "semantic_repair"]
    assert validations == 2
    assert summary["locator_item_count"] == 0


def test_compact_driver_reextracts_semantic_checkpoint_when_filtered_input_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    filtered = tmp_path / "filtered.json"
    filtered.write_text('{"pages":["first"]}', encoding="utf-8")
    semantic_runs = 0
    monkeypatch.setattr(
        compact_driver,
        "estimate_editorial_pages",
        lambda **kwargs: {"volume_id": "PL001", "collection": "PL", "files": []},
    )

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        nonlocal semantic_runs
        assert phase_id == "semantic"
        semantic_runs += 1
        write_semantic_manifest(
            expected_output,
            volume_id="PL001",
            collection="PL",
            source_root=source_root,
            ref_count=0,
        )

    kwargs = {
        "volume_id": "PL001",
        "collection": "PL",
        "source_root": source_root,
        "filtered_pages_file": filtered,
        "intermediate_dir": tmp_path / "intermediate",
        "output_file": tmp_path / "out.json",
        "agent_runner": agent_runner,
    }
    first = run_compact_extraction(**kwargs)
    first_fingerprint = first["semantic_input_fingerprint"]
    run_compact_extraction(**kwargs)
    assert semantic_runs == 1

    filtered.write_text('{"pages":["second"]}', encoding="utf-8")
    second = run_compact_extraction(**kwargs)

    assert semantic_runs == 2
    assert second["semantic_input_fingerprint"] != first_fingerprint


def test_scripture_table_micro_agent_is_path_only_and_attaches_bounded_suggestion(
    tmp_path: Path,
) -> None:
    locator_items = [
        {
            "entry_key": "PO025:index:e1",
            "ref_order": 1,
            "table_reference_suggestions": [],
        }
    ]
    evidence = {
        "table_repair_groups": [
            {
                "section_key": "PO025:index",
                "unresolved_lines": [
                    {
                        "line_id": "row-1",
                        "raw_line": "I Cor. 1, ?\t1260",
                        "partial_json": {
                            "entry_key": "PO025:index:e1",
                            "refs": [{"ref_order": 1}],
                        },
                    }
                ],
            }
        ]
    }
    prompts: list[str] = []

    def agent_runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        prompts.append(prompt)
        assert phase_id == "scripture_tables/table-0001"
        request = json.loads(
            (expected_output.parent / "table-0001_input.json").read_text(
                encoding="utf-8"
            )
        )
        write_json(
            expected_output,
            {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "volume_id": "PO025",
                "input_fingerprint": request["input_fingerprint"],
                "results": [
                    {
                        "line_id": "row-1",
                        "status": "repaired",
                        "scripture_refs": [],
                        "refs": [{"page_ref_int": 1260}],
                    }
                ],
            },
        )

    report = run_scripture_table_repairs(
        volume_id="PO025",
        locator_items=locator_items,
        citation_format_profiles={"PO025:index": {"column_style": "tab"}},
        scripture_evidence=evidence,
        intermediate_dir=tmp_path,
        agent_runner=agent_runner,
    )

    assert "I Cor. 1" not in prompts[0]
    assert report["suggestions_attached"] == 1
    assert locator_items[0]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [1260]
