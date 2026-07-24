from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.run_alphabetical_index_extraction import (
    clear_previous_failure,
    format_translation_progress,
    intermediate_dir_for_volume,
    inspect_output_checkpoint,
    inspect_payload_coverage,
    normalize_language_list,
    output_checkpoint_error_path,
    previous_failure_path,
    read_previous_failure,
    resolve_path,
    resolve_previous_payload_path,
    summarize_intermediate_dir,
    validate_translation_payload,
    write_output_checkpoint_error,
    write_previous_failure,
)


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
        "coverage": {},
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
