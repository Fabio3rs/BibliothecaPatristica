from __future__ import annotations

from pathlib import Path

from tools.indexing.index_workplan import reconcile_workplan_progress


def _plan(tmp_path: Path, fingerprint: str) -> dict:
    return {
        "manifest_status": "candidate",
        "chunks": [
            {
                "chunk_id": "PL001:chunk:001:001",
                "status": "pending",
                "input_fingerprint": fingerprint,
                "output_file": str(tmp_path / "fragment.json"),
            }
        ],
    }


def test_resume_reuses_only_complete_chunk_with_same_fingerprint(tmp_path: Path) -> None:
    output = tmp_path / "fragment.json"
    output.write_text("{}", encoding="utf-8")
    previous = _plan(tmp_path, "same")
    previous["chunks"][0].update(
        {
            "status": "complete",
            "entry_count": 12,
            "completed_at": "2026-01-01T00:00:00Z",
        }
    )

    reconciled = reconcile_workplan_progress(_plan(tmp_path, "same"), previous)

    assert reconciled["chunks"][0]["status"] == "complete"
    assert reconciled["chunks"][0]["entry_count"] == 12
    assert reconciled["manifest_status"] == "complete"
    assert reconciled["resume_diagnostics"]["reused_complete_chunks"] == 1


def test_resume_invalidates_complete_chunk_when_ocr_fingerprint_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "fragment.json").write_text("{}", encoding="utf-8")
    previous = _plan(tmp_path, "old")
    previous["chunks"][0]["status"] = "complete"

    reconciled = reconcile_workplan_progress(_plan(tmp_path, "new"), previous)

    assert reconciled["chunks"][0]["status"] == "pending"
    assert reconciled["resume_diagnostics"]["reused_complete_chunks"] == 0


def test_resume_preserves_attempt_count_for_unchanged_incomplete_chunk(
    tmp_path: Path,
) -> None:
    previous = _plan(tmp_path, "same")
    previous["chunks"][0].update({"status": "failed", "attempt_count": 3})

    reconciled = reconcile_workplan_progress(_plan(tmp_path, "same"), previous)

    assert reconciled["chunks"][0]["status"] == "pending"
    assert reconciled["chunks"][0]["attempt_count"] == 3


def test_resume_preserves_validation_feedback_for_unchanged_chunk(
    tmp_path: Path,
) -> None:
    previous = _plan(tmp_path, "same")
    previous["chunks"][0].update(
        {
            "status": "failed",
            "last_error": "section_id mismatch",
            "last_validation_failure": {
                "stage": "validate_fragment",
                "fragment_file": str(tmp_path / "fragment.json"),
                "error_type": "ValueError",
                "error_detail": "section_id mismatch in prior fragment",
            },
        }
    )

    reconciled = reconcile_workplan_progress(_plan(tmp_path, "same"), previous)

    chunk = reconciled["chunks"][0]
    assert chunk["last_error"] == "section_id mismatch"
    assert chunk["last_validation_failure"]["stage"] == "validate_fragment"
    assert "section_id mismatch" in chunk["last_validation_failure"]["error_detail"]
