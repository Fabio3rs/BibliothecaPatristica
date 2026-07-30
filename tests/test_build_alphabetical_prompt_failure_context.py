from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.build_alphabetical_prompt import build_prompt


def test_build_prompt_includes_previous_failure_block(tmp_path: Path) -> None:
    previous_payload = tmp_path / "PL018_alphabetical_indices.json"
    previous_payload.write_text(
        '{"entries":[{"target_file_best":null},{"target_file_best":"/tmp/file.txt"}],"refs":[{"target_file":null},{"target_file":"/tmp/target.txt"}]}',
        encoding="utf-8",
    )
    prompt = build_prompt(
        volume_id="PL018",
        source_root=tmp_path / "pl018" / "text",
        collection="PL",
        filtered_pages={"volume_id": "PL018", "candidate_files": []},
        previous_payload=previous_payload,
        previous_result_source="default_output",
        output_checkpoint_status="invalid",
        output_checkpoint_error="Alphabetical import validation error: refs[2] looks like a cross-reference without a material anchor",
        previous_failure={
            "stage": "validate_payload",
            "payload_file": str(previous_payload),
            "error_summary": "payload validation failed",
            "error_detail": "Alphabetical import validation error: refs[2] looks like a cross-reference without a material anchor",
        },
        helper_request_json=tmp_path / "helper_request.json",
        helper_output_json=tmp_path / "helper_output.json",
        pipeline_scripts_dir=tmp_path / "scripts" / "pipeline_index_extraction",
        intermediate_dir=tmp_path / "intermediate" / "PL018",
        output_file=previous_payload,
    )

    assert prompt.splitlines()[0] == "$alphabetical-index-extractor"
    assert "PREVIOUS FAILURE" in prompt
    assert "refs[2]" in prompt
    assert "validate_payload" in prompt
    assert "Fix the exact failure for PL018" in prompt
    assert "previous_entries_missing_target_file_best: 1/2" in prompt
    assert "previous_refs_missing_target_file: 1/2" in prompt
    assert "actively try to resolve material locators" in prompt
    assert "pipeline_scripts_dir" in prompt
    assert "intermediate_dir" in prompt


def test_build_prompt_truncates_large_failure_and_filtered_pages() -> None:
    prompt = build_prompt(
        volume_id="PL069",
        source_root=Path("/tmp/pl069/text"),
        collection="PL",
        filtered_pages={
            "volume_id": "PL069",
            "candidate_files": [f"/tmp/pl069/text/page-{i:04d}.txt" for i in range(20000)],
        },
        previous_payload=Path("/tmp/PL069_alphabetical_indices.json"),
        previous_result_source="default_output",
        output_checkpoint_status="invalid",
        output_checkpoint_error="refs mismatch\n" * 20000,
        previous_failure={
            "stage": "validate_payload",
            "payload_file": "/tmp/PL069_alphabetical_indices.json",
            "error_summary": "payload validation failed",
            "error_detail": "entries mismatch\n" * 20000,
        },
        helper_request_json=Path("/tmp/helper_request.json"),
        helper_output_json=Path("/tmp/helper_output.json"),
        pipeline_scripts_dir=Path("/tmp/scripts/pipeline_index_extraction"),
        intermediate_dir=Path("/tmp/intermediate/PL069"),
        output_file=Path("/tmp/PL069_alphabetical_indices.json"),
    )

    assert "... [TRUNCATED]" in prompt
    assert len(prompt.encode("utf-8")) < 900_000


def test_build_prompt_reads_checkpoint_error_from_file_in_cli_mode(tmp_path: Path, monkeypatch) -> None:
    from scripts import build_alphabetical_prompt as mod

    filtered_pages = tmp_path / "filtered_pages.json"
    filtered_pages.write_text('{"volume_id":"PL018","candidate_files":[]}', encoding="utf-8")
    error_file = tmp_path / "checkpoint_error.txt"
    error_file.write_text("Alphabetical import validation error: refs[2] mismatch", encoding="utf-8")
    previous_payload = tmp_path / "PL018_alphabetical_indices.json"
    previous_payload.write_text("{}", encoding="utf-8")

    argv = [
        "build_alphabetical_prompt.py",
        "--volume", "PL018",
        "--source-root", "/tmp/pl018/text",
        "--collection", "PL",
        "--filtered-pages-json", str(filtered_pages),
        "--previous-payload", str(previous_payload),
        "--previous-result-source", "default_output",
        "--output-checkpoint-status", "invalid",
        "--output-checkpoint-error-file", str(error_file),
        "--helper-request-json", str(tmp_path / "helper_request.json"),
        "--helper-output-json", str(tmp_path / "helper_output.json"),
        "--pipeline-scripts-dir", str(tmp_path / "scripts/pipeline_index_extraction"),
        "--intermediate-dir", str(tmp_path / "intermediate/PL018"),
        "--output-file", str(previous_payload),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    mod.main()
