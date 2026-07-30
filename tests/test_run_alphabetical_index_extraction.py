from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import scripts.run_alphabetical_index_extraction as alphabetical_runner
from scripts.run_alphabetical_index_extraction import (
    build_analysis_arg_parser,
    clear_previous_failure,
    format_translation_progress,
    intermediate_dir_for_volume,
    inspect_output_checkpoint,
    inspect_payload_coverage,
    maybe_export_web_indices,
    normalize_language_list,
    output_checkpoint_error_path,
    previous_failure_path,
    read_previous_failure,
    resolve_path,
    resolve_previous_payload_path,
    summarize_intermediate_dir,
    validate_translation_payload,
    volume_extraction_policy,
    write_output_checkpoint_error,
    write_previous_failure,
)


def test_analysis_cli_exposes_named_independent_stages() -> None:
    for command in (
        "discover",
        "extract",
        "locate",
        "verify",
        "assemble",
        "run",
        "status",
        "review",
        "export-seeds",
    ):
        args = build_analysis_arg_parser().parse_args(
            [command, "--volume-id", "PL001"]
        )
        assert args.command == command
        assert args.volume_id == "PL001"


def test_infer_failure_stage_covers_chunk_and_quality_failures() -> None:
    assert (
        alphabetical_runner.infer_failure_stage(
            SystemExit("chunked Codex extraction failed for workplan")
        )
        == "chunk_extraction"
    )
    assert (
        alphabetical_runner.infer_failure_stage(
            SystemExit("Final payload omitted stable objects")
        )
        == "fragment_consumption"
    )
    assert (
        alphabetical_runner.infer_failure_stage(
            SystemExit("OCR evidence verification exceeded the limit")
        )
        == "payload_evidence"
    )


@pytest.mark.parametrize(
    (
        "quality_status",
        "skip_done",
        "redo_invalid",
        "expected",
    ),
    [
        ("valid", True, False, (True, False, "already_imported")),
        ("partial", True, False, (False, True, "quality_partial")),
        (
            "needs_reextract",
            True,
            False,
            (False, True, "quality_needs_reextract"),
        ),
        (
            "needs_reextract",
            False,
            True,
            (False, True, "quality_needs_reextract"),
        ),
        ("valid", False, True, (True, False, "quality_valid")),
        (None, False, True, (True, False, "quality_unassessed")),
    ],
)
def test_volume_extraction_policy_is_quality_aware(
    quality_status: str | None,
    skip_done: bool,
    redo_invalid: bool,
    expected: tuple[bool, bool, str],
) -> None:
    assert (
        volume_extraction_policy(
            db_imported=True,
            quality_status=quality_status,
            skip_done=skip_done,
            redo_invalid=redo_invalid,
            replace=False,
        )
        == expected
    )


def test_volume_extraction_policy_preserves_explicit_replace() -> None:
    assert volume_extraction_policy(
        db_imported=True,
        quality_status="valid",
        skip_done=True,
        redo_invalid=False,
        replace=True,
    ) == (False, True, "selected")


def test_maybe_export_web_indices_runs_public_exporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_run_cmd(cmd: list[str], *, cwd: Path) -> SimpleNamespace:
        calls.append((cmd, cwd))
        return SimpleNamespace(returncode=0, stdout='{"status":"ok"}\n', stderr="")

    monkeypatch.setattr(alphabetical_runner, "run_cmd", fake_run_cmd)
    args = SimpleNamespace(
        no_export_web=False,
        dry_run=False,
        db=tmp_path / "alpha.db",
        web_alpha_out=tmp_path / "public" / "alpha",
    )

    assert maybe_export_web_indices(args) is True
    assert calls == [
        (
            [
                sys.executable,
                str(alphabetical_runner.EXPORT_WEB_SCRIPT),
                "--db",
                str(args.db),
                "--out",
                str(args.web_alpha_out),
            ],
            ROOT,
        )
    ]


@pytest.mark.parametrize(
    ("no_export_web", "dry_run"),
    [(True, False), (False, True)],
)
def test_maybe_export_web_indices_honors_opt_out_and_dry_run(
    no_export_web: bool,
    dry_run: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        alphabetical_runner,
        "export_web_indices",
        lambda **kwargs: pytest.fail("export should not run"),
    )
    args = SimpleNamespace(
        no_export_web=no_export_web,
        dry_run=dry_run,
        db=tmp_path / "alpha.db",
        web_alpha_out=tmp_path / "public" / "alpha",
    )

    assert maybe_export_web_indices(args) is False


def test_compact_batch_reextracts_invalid_import_with_local_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "teste"
    text_root = root / "PL001" / "text"
    text_root.mkdir(parents=True)
    output_dir = tmp_path / "payloads"
    output_dir.mkdir()
    imported: list[bool] = []
    exports: list[bool] = []

    monkeypatch.setattr(
        alphabetical_runner,
        "volume_already_imported",
        lambda db_path, volume_id: True,
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "get_volume_quality",
        lambda db_path, volume_id: {"status": "needs_reextract"},
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "build_filtered_pages_artifact",
        lambda **kwargs: {"source": "test"},
    )

    def fake_compact_extraction(**kwargs: object) -> dict[str, object]:
        Path(kwargs["output_file"]).write_text(
            json.dumps({"coverage": {}}),
            encoding="utf-8",
        )
        return {"status": "ok"}

    monkeypatch.setattr(
        alphabetical_runner,
        "run_compact_extraction",
        fake_compact_extraction,
    )
    monkeypatch.setattr(alphabetical_runner, "validate_payload_file", lambda path: None)
    monkeypatch.setattr(
        alphabetical_runner,
        "write_pipeline_quality_reports",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "import_payload",
        lambda payload_file, db_path, replace: imported.append(replace),
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "maybe_export_web_indices",
        lambda args: exports.append(True) or True,
    )
    args = SimpleNamespace(
        root=root,
        output_dir=output_dir,
        intermediate_root=tmp_path / "intermediate",
        log_dir=tmp_path / "logs",
        db=tmp_path / "alpha.db",
        skip_done=True,
        redo_invalid=False,
        replace=False,
        locator_chunk_size=40,
        chunk_workers=1,
        dry_run=False,
        evidence_sample_size=20,
        max_unverified_evidence_ratio=0.25,
        skip_evidence_check=True,
        translate=False,
        continue_on_error=False,
        verbose=False,
    )

    alphabetical_runner.run_compact_batch(args, ["PL001"])

    assert imported == [True]
    assert exports == [True]


def test_main_persists_system_exit_and_continue_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "teste"
    text_root = root / "PL001" / "text"
    text_root.mkdir(parents=True)
    (text_root / "page-001.txt").write_text("sample", encoding="utf-8")
    output_dir = tmp_path / "out"
    log_dir = tmp_path / "logs"
    intermediate_root = tmp_path / "intermediate"

    monkeypatch.setattr(
        alphabetical_runner,
        "build_filtered_pages_artifact",
        lambda **kwargs: {
            "volume_id": kwargs["volume_id"],
            "profile": "alphabetical",
            "source": "test",
            "candidate_files": [],
            "candidate_sections": [],
        },
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "build_index_workplan",
        lambda **kwargs: {
            "volume_id": kwargs["volume_id"],
            "pipeline_kind": "alphabetical",
            "manifest_status": "no_candidates",
            "chunks": [],
        },
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "build_prompt",
        lambda **kwargs: "$alphabetical-index-extractor test prompt",
    )
    monkeypatch.setattr(
        alphabetical_runner,
        "run_codex",
        lambda **kwargs: (_ for _ in ()).throw(
            SystemExit("chunked Codex extraction failed for workplan")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_alphabetical_index_extraction.py",
            "--volume-id",
            "PL001",
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--log-dir",
            str(log_dir),
            "--intermediate-root",
            str(intermediate_root),
            "--db",
            str(tmp_path / "alpha.db"),
            "--no-export-web",
            "--continue-on-error",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        alphabetical_runner.main()

    assert excinfo.value.code == 1
    failure = json.loads(
        previous_failure_path(output_dir, "PL001").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "chunk_extraction"
    assert "chunked Codex extraction failed" in failure["error_detail"]


def test_resolve_previous_payload_path_keeps_missing_explicit_dir_candidate(tmp_path: Path) -> None:
    previous_dir = tmp_path / "previous"
    default_payload = tmp_path / "out" / "PG001_alphabetical_indices.json"
    args = SimpleNamespace(previous_result_json=None, previous_result_dir=previous_dir)

    resolved_path, source = resolve_previous_payload_path(
        args=args,
        volume_id="PG001",
        default_payload_file=default_payload,
    )

    assert resolved_path == previous_dir / "PG001_alphabetical_indices.json"
    assert source == "explicit_dir_missing"


def test_resolve_path_returns_absolute_path() -> None:
    resolved = resolve_path(Path("data"))
    assert resolved is not None
    assert resolved.is_absolute()


def test_inspect_output_checkpoint_accepts_valid_minimal_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "PG001_alphabetical_indices.json"
    payload = {
        "schema_version": 1,
        "generated_at": "2026-07-17T00:00:00Z",
        "volume": {
            "volume_id": "PG001",
            "collection": "PG",
            "source_root": "/tmp/pg001/text",
            "volume_label": "PG001",
        },
        "sections": [],
        "nodes": [],
        "entries": [],
        "refs": [],
        "scripture_refs": [],
        "coverage": {
            "entries_status": "no_index_section",
            "entries_status_reason": "The inspected files contain no alphabetical entries.",
            "evidence_files": ["/tmp/pg001/text/page-001.txt"],
        },
        "notes": [],
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    status, error = inspect_output_checkpoint(payload_path)

    assert status == "done"
    assert error is None


def test_inspect_payload_coverage_detects_partial_status(tmp_path: Path) -> None:
    payload_path = tmp_path / "PO025_alphabetical_indices.json"
    payload = {
        "schema_version": 1,
        "generated_at": "2026-07-18T00:00:00Z",
        "volume": {
            "volume_id": "PO025",
            "collection": "PO",
            "source_root": "/tmp/po025/text",
            "volume_label": "PO025",
        },
        "sections": [],
        "nodes": [],
        "entries": [],
        "refs": [],
        "scripture_refs": [],
        "coverage": {
            "entries_status": "partial_extraction",
            "entries_status_reason": "Concordance block deferred.",
        },
        "notes": [],
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    has_warning, status, reason = inspect_payload_coverage(payload_path)

    assert has_warning is True
    assert status == "partial_extraction"
    assert reason == "Concordance block deferred."


def test_previous_failure_roundtrip(tmp_path: Path) -> None:
    path = previous_failure_path(tmp_path, "PL018")

    write_previous_failure(
        path=path,
        volume_id="PL018",
        stage="validate_payload",
        error_summary="payload validation failed",
        error_detail="refs[2] looks like a cross-reference without a material anchor",
        payload_file=tmp_path / "PL018_alphabetical_indices.json",
        last_message_file=tmp_path / "PL018_last_message.txt",
        stdout_log_file=tmp_path / "PL018_stdout.log",
        stderr_log_file=tmp_path / "PL018_stderr.log",
        stream_log_file=tmp_path / "PL018_stream.log",
    )

    data = read_previous_failure(path)

    assert data is not None
    assert data["volume_id"] == "PL018"
    assert data["stage"] == "validate_payload"
    assert "refs[2]" in data["error_detail"]

    clear_previous_failure(path)
    assert not path.exists()


def test_intermediate_dir_for_volume_uses_volume_id(tmp_path: Path) -> None:
    resolved = intermediate_dir_for_volume(tmp_path, "PL018")
    assert resolved == tmp_path / "PL018"


def test_summarize_intermediate_dir_reports_files(tmp_path: Path) -> None:
    intermediate_dir = tmp_path / "PO025"
    intermediate_dir.mkdir()
    (intermediate_dir / "entries.json").write_text("[]\n", encoding="utf-8")

    summary = summarize_intermediate_dir(intermediate_dir)

    assert summary == [{"name": "entries.json", "bytes": 3}]


def test_write_output_checkpoint_error_roundtrip(tmp_path: Path) -> None:
    path = output_checkpoint_error_path(tmp_path, "PL069")

    written = write_output_checkpoint_error(path, "many validation errors here")

    assert written == path
    assert path.read_text(encoding="utf-8") == "many validation errors here"

    removed = write_output_checkpoint_error(path, None)
    assert removed is None
    assert not path.exists()


def test_normalize_language_list_deduplicates_and_normalizes() -> None:
    assert normalize_language_list("en, it,pt-br,en, FR ") == ["en", "it", "pt-br", "fr"]


def test_validate_translation_payload_requires_exact_language_set() -> None:
    validated = validate_translation_payload(
        {"en": "Saint Peter", "fr": "Saint Pierre"},
        ["en", "fr"],
    )

    assert validated == {"en": "Saint Peter", "fr": "Saint Pierre"}


def test_format_translation_progress_includes_progress_and_context() -> None:
    line = format_translation_progress(
        volume_id="PG001",
        completed=12,
        total=387,
        result={
            "source_text": "Sanctus Petrus",
            "source_kind": "entry_title",
            "section_kind": "alphabetical_general",
            "sample_context": "Sanctus Petrus apostolus princeps.",
            "translations": {"en": "Saint Peter", "fr": "Saint Pierre"},
            "attempts": 2,
            "elapsed_s": 1.234,
        },
    )

    assert "translation 12/387" in line
    assert "volume=PG001" in line
    assert "kind=entry_title" in line
    assert "section_kind=alphabetical_general" in line
    assert "attempts=2" in line
    assert "elapsed_s=1.234" in line
    assert "Sanctus Petrus" in line
