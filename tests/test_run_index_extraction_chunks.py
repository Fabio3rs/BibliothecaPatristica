from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.run_index_extraction_chunks as chunk_runner
from scripts.run_index_extraction_chunks import (
    _chunk_prompt,
    _coverage_check,
    _run_codex_command,
    _validate_fragment,
)


def _workplan(tmp_path: Path) -> tuple[dict, dict]:
    chunk = {
        "chunk_id": "PL001:index-rerum:0001",
        "section_id": "PL001:index-rerum",
        "physical_files": [str(tmp_path / "scan-001.txt")],
        "overlap_context_files": [str(tmp_path / "scan-000.txt")],
        "context_files": [str(tmp_path / "scan-002.txt")],
        "deterministic_entry_estimate": {
            "estimated_entry_count": {"lower_bound": 1, "likely": 2, "upper_bound": 3}
        },
        "output_file": str(tmp_path / "chunk.json"),
    }
    workplan = {
        "volume_id": "PL001",
        "source_root": str(tmp_path),
        "pipeline_kind": "alphabetical",
        "pipeline_purpose": "Read closing alphabetical indexes.",
        "extraction_direction": "physical_end_to_start",
        "chunks": [chunk],
    }
    return workplan, chunk


def test_chunk_prompt_is_bounded_and_separates_numbering_systems(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)

    prompt = _chunk_prompt(tmp_path / "workplan.json", workplan, chunk)

    assert prompt.splitlines()[0] == "$alphabetical-index-extractor"
    assert "scan-001.txt" in prompt
    assert "scan-002.txt" in prompt
    assert "scan-000.txt" in prompt
    assert "physical file/scan sequence only" in prompt
    assert "NUMBER  PAGE-TITLE  NUMBER+1" in prompt
    assert "split" in prompt and "corrupted by CER" in prompt
    assert "Never map these systems by numeric equality" in prompt
    assert "entry_number_system" in prompt
    assert "estimated logical-entry range: 1 to 3" in prompt
    assert "real semantic reading" in prompt
    assert "Never re-emit an entry owned by an overlap/context" in prompt
    assert "physical_end_to_start" in prompt
    assert "closing alphabetical indexes" in prompt
    assert "Do not run the material target locator in this phase" in prompt
    assert "limit what you emit, not what you may investigate" in prompt
    assert "search anywhere inside this" in prompt
    assert "pala-\\nvra` -> `palavra" in prompt
    assert "fix_linebreak_hyphens.py" in prompt
    assert "input_fingerprint" in prompt


def test_general_chunk_prompt_names_general_skill_first(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    workplan["pipeline_kind"] = "general"
    workplan["pipeline_purpose"] = "Read opening tables of works and chapters."

    prompt = _chunk_prompt(tmp_path / "workplan.json", workplan, chunk)

    assert prompt.splitlines()[0] == "$patristic-index-extractor"
    assert "$alphabetical-index-extractor" not in prompt
    assert "CHUNK PHASE OWNERSHIP" in prompt
    assert "Write exactly one semantic fragment" in prompt
    assert "Do not edit the workplan" in prompt
    assert "Never initialize, import into, replace, rebuild" in prompt
    assert "`init_index_db.py`, `import_index_json.py`" in prompt
    assert "`rebuild_index_db_from_payloads.py`" in prompt
    assert "acknowledgment means only that this fragment was written" in prompt


def test_chunk_prompt_includes_existing_fragment_validation_failure(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["last_validation_failure"] = {
        "stage": "validate_fragment",
        "fragment_file": chunk["output_file"],
        "error_type": "ValueError",
        "error_detail": "section_id mismatch in prior chunk",
    }

    prompt = _chunk_prompt(tmp_path / "workplan.json", workplan, chunk)

    assert "EXISTING FRAGMENT RECOVERY" in prompt
    assert f"already exists at: {chunk['output_file']}" in prompt
    assert "section_id mismatch in prior chunk" in prompt
    assert "checkpoint, not as ground truth" in prompt
    assert "top-level `section_id` is the chunk assignment identifier" in prompt


def test_chunk_prompt_includes_previous_execution_failure(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["last_error_type"] = "RuntimeError"
    chunk["last_error"] = (
        "Invalid acknowledgment for PL001:index-rerum:0001: "
        "{'status': 'failed'}"
    )

    prompt = _chunk_prompt(tmp_path / "workplan.json", workplan, chunk)

    assert "PREVIOUS CHUNK ATTEMPT FAILURE" in prompt
    assert "Invalid acknowledgment" in prompt
    assert "exact_previous_error" in prompt


def test_chunk_fragment_must_repeat_deterministic_physical_scope(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    output = Path(chunk["output_file"])
    output.write_text(
        """{
          "volume_id": "PL001",
          "chunk_id": "PL001:index-rerum:0001",
          "section_id": "PL001:index-rerum",
          "status": "complete",
          "physical_files": ["wrong-916.txt"],
          "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": true
          },
          "boundary_decisions": []
        }""",
        encoding="utf-8",
    )

    try:
        _validate_fragment(output, workplan, chunk)
    except ValueError as exc:
        assert "physical_files mismatch" in str(exc)
    else:
        raise AssertionError("invalid physical scope was accepted")


def test_chunk_fragment_requires_explicit_editorial_numbering_contract(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["deterministic_entry_estimate"]["estimated_entry_count"]["lower_bound"] = 0
    output = Path(chunk["output_file"])
    output.write_text(
        f"""{{
          "volume_id": "PL001",
          "chunk_id": "PL001:index-rerum:0001",
          "section_id": "PL001:index-rerum",
          "status": "complete",
          "physical_files": ["{tmp_path / 'scan-001.txt'}"],
          "entries": []
        }}""",
        encoding="utf-8",
    )

    try:
        _validate_fragment(output, workplan, chunk)
    except ValueError as exc:
        assert "numbering_semantics" in str(exc)
    else:
        raise AssertionError("fragment without numbering contract was accepted")


def test_complete_empty_fragment_is_rejected_when_strong_entry_lines_exist(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    output = Path(chunk["output_file"])
    output.write_text(
        f"""{{
          "volume_id": "PL001",
          "chunk_id": "PL001:index-rerum:0001",
          "section_id": "PL001:index-rerum",
          "status": "complete",
          "physical_files": ["{tmp_path / 'scan-001.txt'}"],
          "numbering_semantics": {{
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": true
          }},
          "boundary_decisions": [],
          "entries": []
        }}""",
        encoding="utf-8",
    )

    try:
        _validate_fragment(output, workplan, chunk)
    except ValueError as exc:
        assert "strong deterministic entry signals" in str(exc)
    else:
        raise AssertionError("empty complete fragment was accepted")


def test_general_fragment_accepts_relative_source_path_for_owned_absolute_file(
    tmp_path: Path,
) -> None:
    workplan, chunk = _workplan(tmp_path)
    workplan["pipeline_kind"] = "general"
    chunk["chunk_contract_version"] = 2
    output = Path(chunk["output_file"])
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "works": [],
                "sections": [
                    {
                        "section_key": "PL001:ordo",
                        "entries": [
                            {
                                "entry_key": "PL001:ordo:001",
                                "raw_json": {"source_files": ["scan-001.txt"]},
                            }
                        ],
                    }
                ],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    _validate_fragment(output, workplan, chunk)


def test_general_fragment_accepts_justified_out_of_scope_empty_result(
    tmp_path: Path,
) -> None:
    workplan, chunk = _workplan(tmp_path)
    workplan["pipeline_kind"] = "general"
    output = Path(chunk["output_file"])
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "works": [],
                "sections": [],
                "notes": [],
                "raw_json": {
                    "entries_status_reason": "The inspected pages are a closing subject index.",
                    "owned_file_evidence": {
                        "pipeline_owner": "alphabetical",
                        "classification": "out_of_scope_closing_analytical_index",
                        "files_checked": chunk["physical_files"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    _validate_fragment(output, workplan, chunk)


def test_chunk_fragment_rejects_duplicate_ref_stable_key(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    output = Path(chunk["output_file"])
    entry_key = "PL001:index-rerum:0001:entry:001"
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": "PL001:index-rerum:0001",
                "section_id": "PL001:index-rerum",
                "status": "complete",
                "physical_files": [str(tmp_path / "scan-001.txt")],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "sections": [],
                "nodes": [],
                "entries": [
                    {
                        "entry_key": entry_key,
                        "entry_raw": "HIERONYMUS, 1073, 57.",
                    }
                ],
                "refs": [
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_kind": "page_ref",
                        "ref_raw": "1073",
                    },
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_kind": "page_ref",
                        "ref_raw": "57",
                    },
                ],
                "scripture_refs": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate refs stable key inside fragment"):
        _validate_fragment(output, workplan, chunk)


@pytest.mark.parametrize("field", ["refs", "scripture_refs"])
def test_chunk_fragment_rejects_reference_to_missing_entry(
    tmp_path: Path,
    field: str,
) -> None:
    workplan, chunk = _workplan(tmp_path)
    output = Path(chunk["output_file"])
    payload = {
        "schema_version": 2,
        "volume_id": "PL001",
        "chunk_id": "PL001:index-rerum:0001",
        "section_id": "PL001:index-rerum",
        "status": "complete",
        "physical_files": [str(tmp_path / "scan-001.txt")],
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [],
        "sections": [],
        "nodes": [],
        "entries": [{"entry_key": "entry:001", "entry_raw": "AARON, 15."}],
        "refs": [],
        "scripture_refs": [],
        "notes": [],
    }
    payload[field] = [
        {
            "entry_key": "entry:missing",
            "ref_order": 1,
        }
    ]
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=rf"{field}\[0\] references missing entry_key"):
        _validate_fragment(output, workplan, chunk)


def test_coverage_check_compares_agent_count_without_replacing_semantics(
    tmp_path: Path,
) -> None:
    _, chunk = _workplan(tmp_path)

    assert _coverage_check(2, chunk)["status"] == "within_estimated_range"
    assert _coverage_check(0, chunk)["status"] == "below_lower_bound_review"
    assert _coverage_check(5, chunk)["status"] == "above_upper_bound_review"
    assert "Review signal only" in _coverage_check(2, chunk)["interpretation"]


def test_complete_fragment_requires_decision_for_high_page_boundary(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["required_boundary_decisions"] = [
        {
            "physical_left_file": str(tmp_path / "scan-001.txt"),
            "physical_right_file": str(tmp_path / "scan-002.txt"),
        }
    ]
    output = Path(chunk["output_file"])
    output.write_text(
        f"""{{
          "volume_id": "PL001",
          "chunk_id": "PL001:index-rerum:0001",
          "section_id": "PL001:index-rerum",
          "status": "complete",
          "physical_files": ["{tmp_path / 'scan-001.txt'}"],
          "numbering_semantics": {{
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": true
          }},
          "boundary_decisions": [],
          "entries": [{{"entry_raw": "AARON. De interpretatione, 15"}}]
        }}""",
        encoding="utf-8",
    )

    try:
        _validate_fragment(output, workplan, chunk)
    except ValueError as exc:
        assert "missing required page-boundary decision" in str(exc)
    else:
        raise AssertionError("fragment without required boundary decision was accepted")


def test_codex_command_streams_both_channels_and_keeps_logs(
    tmp_path: Path, capsys
) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys; "
            "prompt = sys.stdin.read(); "
            "print('out:' + prompt); "
            "print('err:progress', file=sys.stderr)"
        ),
    ]

    result = _run_codex_command(
        command=command,
        prompt="hello",
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        verbose=True,
        chunk_id="PO002:test:0001",
    )

    captured = capsys.readouterr()
    assert result.returncode == 0
    assert result.stdout == "out:hello\n"
    assert result.stderr == "err:progress\n"
    assert "[PO002:test:0001 codex stdout] out:hello" in captured.out
    assert "[PO002:test:0001 codex stderr] err:progress" in captured.err
    assert stdout_log.read_text(encoding="utf-8") == result.stdout
    assert stderr_log.read_text(encoding="utf-8") == result.stderr


def test_chunk_runner_reports_correct_position_output_and_persistent_progress(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    chunks = [
        {
            "chunk_id": f"PL001:chunk:001:00{order}",
            "section_id": "PL001:section:001",
            "status": "pending",
            "physical_files": [str(tmp_path / f"scan-{order:03d}.txt")],
            "output_file": str(tmp_path / f"chunk-{order}.json"),
        }
        for order in (1, 2)
    ]
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(
        json.dumps(
                {
                    "volume_id": "PL001",
                    "source_root": str(tmp_path),
                    "pipeline_kind": "alphabetical",
                "manifest_status": "candidate",
                "chunks": chunks,
            }
        ),
        encoding="utf-8",
    )

    def fake_execute_chunk(**kwargs):
        chunk = kwargs["chunk"]
        persisted = json.loads(workplan_path.read_text(encoding="utf-8"))
        assert persisted["manifest_status"] == "running"
        persisted_chunk = next(
            item
            for item in persisted["chunks"]
            if item["chunk_id"] == chunk["chunk_id"]
        )
        assert persisted_chunk["status"] == "queued"
        return chunk, {"entries": [{"entry_key": chunk["chunk_id"]}]}

    monkeypatch.setattr(chunk_runner, "_execute_chunk", fake_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--verbose",
        ],
    )

    chunk_runner.main()

    captured = capsys.readouterr()
    assert f"[CHUNK 1/2] DONE id={chunks[0]['chunk_id']}" in captured.out
    assert f"output={chunks[0]['output_file']}" in captured.out
    assert f"[CHUNK 2/2] DONE id={chunks[1]['chunk_id']}" in captured.out
    assert "retry_policy=none" in captured.out
    assert "[CHUNKS] DONE total=2 completed=2 skipped=0" in captured.out

    progress = (tmp_path / "logs" / "progress.log").read_text(encoding="utf-8")
    assert f"[CHUNK 1/2] DONE id={chunks[0]['chunk_id']}" in progress
    assert f"output={chunks[0]['output_file']}" in progress
    assert "[CHUNKS] DONE total=2 completed=2 skipped=0" in progress


def test_attempt_artifacts_do_not_overwrite_previous_run(tmp_path: Path) -> None:
    first = chunk_runner._attempt_artifact_path(
        tmp_path,
        order=1,
        attempt=1,
        suffix="prompt.txt",
    )
    second = chunk_runner._attempt_artifact_path(
        tmp_path,
        order=1,
        attempt=2,
        suffix="prompt.txt",
    )

    assert first.name == "chunk_0001_attempt_001_prompt.txt"
    assert second.name == "chunk_0001_attempt_002_prompt.txt"
    assert first != second

    first.write_text("prior attempt", encoding="utf-8")
    next_attempt = chunk_runner._next_available_attempt_number(
        tmp_path,
        order=1,
        chunk={"attempt_count": 0},
    )
    assert next_attempt == 2


def test_runner_prevalidates_existing_invalid_fragment_before_first_agent_call(
    tmp_path: Path, monkeypatch
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["status"] = "pending"
    workplan["manifest_status"] = "candidate"
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(json.dumps(workplan), encoding="utf-8")
    Path(chunk["output_file"]).write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": "wrong-semantic-section-id",
                "status": "complete",
                "physical_files": chunk["physical_files"],
            }
        ),
        encoding="utf-8",
    )

    captured_failure: dict | None = None

    def fake_execute_chunk(**kwargs):
        nonlocal captured_failure
        captured_failure = kwargs["chunk"].get("last_validation_failure")
        return kwargs["chunk"], {"entries": [{"entry_key": "recovered"}]}

    monkeypatch.setattr(chunk_runner, "_execute_chunk", fake_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--skip-complete",
        ],
    )

    chunk_runner.main()

    assert captured_failure is not None
    assert captured_failure["stage"] == "validate_fragment"
    assert captured_failure["fragment_file"] == chunk["output_file"]
    assert "section_id mismatch" in captured_failure["error_detail"]
    prompt = (
        tmp_path / "logs" / "chunk_0001_attempt_001_prompt.txt"
    ).read_text(encoding="utf-8")
    assert "EXISTING FRAGMENT RECOVERY" in prompt
    assert "section_id mismatch" in prompt


def test_runner_clears_stale_validation_feedback_when_fragment_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["status"] = "pending"
    chunk["last_validation_failure"] = {
        "stage": "validate_fragment",
        "fragment_file": chunk["output_file"],
        "error_type": "ValueError",
        "error_detail": "old failure for removed fragment",
    }
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(json.dumps(workplan), encoding="utf-8")
    captured_failure: dict | None = {"unexpected": True}

    def fake_execute_chunk(**kwargs):
        nonlocal captured_failure
        captured_failure = kwargs["chunk"].get("last_validation_failure")
        return kwargs["chunk"], {"entries": []}

    monkeypatch.setattr(chunk_runner, "_execute_chunk", fake_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--skip-complete",
        ],
    )

    chunk_runner.main()

    assert captured_failure is None
    prompt = (
        tmp_path / "logs" / "chunk_0001_attempt_001_prompt.txt"
    ).read_text(encoding="utf-8")
    assert "EXISTING FRAGMENT RECOVERY" not in prompt
    assert "old failure for removed fragment" not in prompt


def test_runner_recovers_valid_pending_fragment_without_calling_agent(
    tmp_path: Path, monkeypatch
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["status"] = "pending"
    chunk["chunk_contract_version"] = 2
    chunk["input_fingerprint"] = "current-fingerprint"
    chunk["deterministic_entry_estimate"]["estimated_entry_count"] = {
        "lower_bound": 0,
        "likely": 0,
        "upper_bound": 0,
    }
    workplan["manifest_status"] = "candidate"
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(json.dumps(workplan), encoding="utf-8")
    Path(chunk["output_file"]).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "input_fingerprint": chunk["input_fingerprint"],
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "sections": [],
                "nodes": [],
                "entries": [],
                "refs": [],
                "scripture_refs": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    def unexpected_execute_chunk(**kwargs):
        raise AssertionError("agent should not run for a valid existing fragment")

    monkeypatch.setattr(chunk_runner, "_execute_chunk", unexpected_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--skip-complete",
        ],
    )

    chunk_runner.main()

    saved = json.loads(workplan_path.read_text(encoding="utf-8"))
    assert saved["chunks"][0]["status"] == "complete"
    assert saved["chunks"][0]["entry_count"] == 0
    assert saved["chunks"][0]["coverage_check"]["status"] == "no_deterministic_signal"
    assert saved["manifest_status"] == "complete"


@pytest.mark.parametrize("initial_status", ["pending", "complete"])
def test_runner_does_not_recover_fragment_with_stale_fingerprint(
    tmp_path: Path, monkeypatch, initial_status: str
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["status"] = initial_status
    chunk["chunk_contract_version"] = 2
    chunk["input_fingerprint"] = "current-fingerprint"
    chunk["deterministic_entry_estimate"]["estimated_entry_count"] = {
        "lower_bound": 0,
        "likely": 0,
        "upper_bound": 0,
    }
    workplan["manifest_status"] = "candidate"
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(json.dumps(workplan), encoding="utf-8")
    Path(chunk["output_file"]).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "input_fingerprint": "old-fingerprint",
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "sections": [],
                "nodes": [],
                "entries": [],
                "refs": [],
                "scripture_refs": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    captured_failure: dict | None = None

    def fake_execute_chunk(**kwargs):
        nonlocal captured_failure
        captured_failure = kwargs["chunk"].get("last_validation_failure")
        return kwargs["chunk"], {"entries": []}

    monkeypatch.setattr(chunk_runner, "_execute_chunk", fake_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--skip-complete",
        ],
    )

    chunk_runner.main()

    assert captured_failure is not None
    assert "input_fingerprint mismatch" in captured_failure["error_detail"]
    prompt = (
        tmp_path / "logs" / "chunk_0001_attempt_001_prompt.txt"
    ).read_text(encoding="utf-8")
    assert "old-fingerprint" in prompt
    assert "current-fingerprint" in prompt


def test_dry_run_persists_prevalidation_failure_without_active_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["status"] = "pending"
    workplan["manifest_status"] = "candidate"
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(json.dumps(workplan), encoding="utf-8")
    Path(chunk["output_file"]).write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": "wrong-section",
                "status": "complete",
                "physical_files": chunk["physical_files"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--skip-complete",
            "--dry-run",
        ],
    )

    chunk_runner.main()

    saved_chunk = json.loads(workplan_path.read_text(encoding="utf-8"))["chunks"][0]
    assert "section_id mismatch" in saved_chunk["last_validation_failure"]["error_detail"]
    assert "active_attempt_number" not in saved_chunk


def test_current_fragment_validation_requires_matching_input_fingerprint(
    tmp_path: Path,
) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["input_fingerprint"] = "current-fingerprint"
    chunk["deterministic_entry_estimate"]["estimated_entry_count"] = {
        "lower_bound": 0,
        "likely": 0,
        "upper_bound": 0,
    }
    output = Path(chunk["output_file"])
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "input_fingerprint": "old-fingerprint",
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "sections": [],
                "nodes": [],
                "entries": [],
                "refs": [],
                "scripture_refs": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input_fingerprint mismatch"):
        _validate_fragment(
            output,
            workplan,
            chunk,
            require_input_fingerprint=True,
        )


def test_v2_fragment_entries_require_physical_source_files(tmp_path: Path) -> None:
    workplan, chunk = _workplan(tmp_path)
    chunk["chunk_contract_version"] = 2
    chunk["deterministic_entry_estimate"]["estimated_entry_count"]["lower_bound"] = 0
    output = Path(chunk["output_file"])
    output.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "volume_id": "PL001",
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "status": "complete",
                "physical_files": chunk["physical_files"],
                "numbering_semantics": {
                    "physical_file_fields": "physical_files_and_explicit_file_locators",
                    "entry_number_system": "editorial",
                    "numeric_equality_mapping_forbidden": True,
                },
                "boundary_decisions": [],
                "sections": [],
                "nodes": [],
                "entries": [{"entry_key": "PL001:index-rerum:entry:001"}],
                "refs": [],
                "scripture_refs": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="raw_json.source_files"):
        _validate_fragment(output, workplan, chunk)


def test_codex_command_emits_stall_heartbeat_without_retrying(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(chunk_runner, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(chunk_runner, "STALL_WARNING_SECONDS", 0.02)
    progress_log = tmp_path / "progress.log"

    result = _run_codex_command(
        command=[sys.executable, "-c", "import time; time.sleep(0.05)"],
        prompt="",
        stdout_log=tmp_path / "stdout.log",
        stderr_log=tmp_path / "stderr.log",
        verbose=True,
        chunk_id="PL001:chunk:001:001",
        progress_log=progress_log,
    )

    captured = capsys.readouterr()
    assert result.returncode == 0
    assert "[CHUNK PL001:chunk:001:001] STALLED" in captured.err
    assert "no_output=" in progress_log.read_text(encoding="utf-8")


def test_parallel_runner_records_successes_even_when_another_chunk_fails(
    tmp_path: Path, monkeypatch
) -> None:
    chunks = [
        {
            "chunk_id": f"PL001:chunk:001:00{order}",
            "section_id": "PL001:section:001",
            "status": "pending",
            "physical_files": [str(tmp_path / f"scan-{order:03d}.txt")],
            "output_file": str(tmp_path / f"chunk-{order}.json"),
        }
        for order in (1, 2)
    ]
    workplan_path = tmp_path / "manifest.json"
    workplan_path.write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "source_root": str(tmp_path),
                "pipeline_kind": "alphabetical",
                "manifest_status": "candidate",
                "chunks": chunks,
            }
        ),
        encoding="utf-8",
    )

    def fake_execute_chunk(**kwargs):
        chunk = kwargs["chunk"]
        if chunk["chunk_id"].endswith("001"):
            raise RuntimeError("simulated failure")
        return chunk, {"entries": [{"entry_key": chunk["chunk_id"]}]}

    monkeypatch.setattr(chunk_runner, "_execute_chunk", fake_execute_chunk)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction_chunks.py",
            "--workplan",
            str(workplan_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--workers",
            "2",
        ],
    )

    with pytest.raises(SystemExit, match="simulated failure"):
        chunk_runner.main()

    saved = json.loads(workplan_path.read_text(encoding="utf-8"))
    assert saved["chunks"][0]["status"] == "failed"
    assert saved["chunks"][0]["attempt_count"] == 1
    assert saved["chunks"][1]["status"] == "complete"
    assert saved["chunks"][1]["attempt_count"] == 1
