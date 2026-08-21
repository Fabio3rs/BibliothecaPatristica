from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.run_index_extraction as run_index_extraction
from scripts.run_index_extraction import (
    build_editorial_pages_artifact,
    build_hyphen_rerun_recovery_block,
    build_previous_failure_prompt_block,
    build_work_anchor_rerun_prompt_block,
    compact_failure_text,
    failure_mentions_hyphen_artifact,
    load_failure_artifact,
    payload_has_hyphen_artifacts,
    prevalidate_existing_payload,
    validate_payload,
    write_failure_artifact,
)

PROMPT_SCRIPT = ROOT / ".codex/skills/patristic-index-extractor/scripts/build_volume_prompt.py"


def test_infer_failure_stage_covers_chunk_and_quality_failures() -> None:
    assert (
        run_index_extraction.infer_failure_stage(
            SystemExit("chunked Codex extraction failed for workplan")
        )
        == "chunk_extraction"
    )
    assert (
        run_index_extraction.infer_failure_stage(
            SystemExit("Final payload omitted stable objects")
        )
        == "fragment_consumption"
    )
    assert (
        run_index_extraction.infer_failure_stage(
            SystemExit("OCR evidence verification exceeded the limit")
        )
        == "payload_evidence"
    )
    assert (
        run_index_extraction.infer_failure_stage(
            SystemExit(
                "import_index_json.py failed for PL201_indices.json\n"
                "STDERR:\nTraceback (most recent call last):"
            )
        )
        == "import_payload"
    )


def test_editorial_pages_artifact_skips_po_without_using_pgpl_estimator(tmp_path: Path) -> None:
    text_root = tmp_path / "PO001" / "text"
    text_root.mkdir(parents=True)
    output_path = tmp_path / "PO001_editorial_pages.json"

    payload = build_editorial_pages_artifact(
        volume_id="PO001",
        source_root=text_root,
        collection="PO",
        output_path=output_path,
        db_path=tmp_path / "editorial_pages.db",
        use_cache=True,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "editorial_page_estimator_supports_pg_pl_only"
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload


def test_run_index_extraction_detects_hyphen_artifacts_in_payload_and_failure() -> None:
    assert payload_has_hyphen_artifacts({"sections": [{"entries": [{"entry_raw": "pala-"}]}]})
    assert not payload_has_hyphen_artifacts({"sections": [{"entries": [{"entry_raw": "palavra"}]}]})
    assert not payload_has_hyphen_artifacts(
        {
            "sections": [
                {
                    "entries": [
                        {
                            "entry_raw": "palavra",
                            "raw_json": {"source_lines": ["pala-", "vra"]},
                        }
                    ]
                }
            ]
        }
    )
    assert payload_has_hyphen_artifacts(
        {"sections": [{"entries": [{"target_raw": "pala-"}]}]}
    )

    assert failure_mentions_hyphen_artifact(
        {
            "error_summary": "payload validation failed",
            "error_detail": "entries[2].entry_raw appears to contain an OCR line-break hyphen artifact.",
        }
    )
    assert not failure_mentions_hyphen_artifact(
        {
            "error_summary": "payload validation failed",
            "error_detail": "Missing top-level key: works",
        }
    )


def test_validate_payload_rejects_unjustified_empty_index_section(tmp_path: Path) -> None:
    payload_file = tmp_path / "PO001_indices.json"
    payload_file.write_text("{}", encoding="utf-8")
    payload = {
        "volume": {"volume_id": "PO001"},
        "works": [],
        "sections": [
                {
                    "section_key": "PO001:index:1",
                    "scope_kind": "work_front",
                    "heading_raw": "INDEX CAPITUM",
                    "file_start": "/tmp/PO001/text/page-001.txt",
                    "file_end": "/tmp/PO001/text/page-001.txt",
                    "entries": [],
                    "raw_json": {},
                }
        ],
        "notes": [],
    }

    with pytest.raises(SystemExit, match="List-bearing sections have no entries"):
        validate_payload(payload, "PO001", payload_file)

    payload["sections"][0]["raw_json"] = {
        "entries_status": "unrecoverable_ocr",
        "entries_status_reason": "Characters are not legible.",
        "evidence_files": ["/tmp/PO001/text/page-001.txt"],
    }
    payload["sections"][0]["file_start"] = "/tmp/PO001/text/page-001.txt"
    payload["sections"][0]["file_end"] = "/tmp/PO001/text/page-001.txt"
    validate_payload(payload, "PO001", payload_file)


def test_validate_payload_rejects_sections_without_import_anchors(tmp_path: Path) -> None:
    payload_file = tmp_path / "PL201_indices.json"
    payload_file.write_text("{}", encoding="utf-8")
    payload = {
        "volume": {"volume_id": "PL201"},
        "works": [],
        "sections": [
            {
                "section_key": "PL201:ordo",
                "scope_kind": "volume_end",
                "heading_raw": "ORDO RERUM",
                "page_start": None,
                "page_end": None,
                "file_start": None,
                "file_end": None,
                "raw_json": {},
                "entries": [
                    {"entry_key": "PL201:ordo:001", "entry_raw": "CAP. I"}
                ],
            }
        ],
        "notes": [],
    }

    with pytest.raises(SystemExit, match="at least one start anchor"):
        validate_payload(payload, "PL201", payload_file)


def test_validate_payload_requires_stable_entry_keys(tmp_path: Path) -> None:
    payload_file = tmp_path / "PL001_indices.json"
    payload_file.write_text("{}", encoding="utf-8")
    payload = {
        "volume": {"volume_id": "PL001"},
        "works": [],
        "sections": [
            {
                "section_key": "PL001:ordo",
                "scope_kind": "volume_end",
                "heading_raw": "ORDO RERUM",
                "file_start": "page-001.txt",
                "file_end": "page-001.txt",
                "raw_json": {},
                "entries": [{"entry_raw": "CAP. I"}],
            }
        ],
        "notes": [],
    }

    with pytest.raises(SystemExit, match="stable entry_key"):
        validate_payload(payload, "PL001", payload_file)


def test_validate_payload_rejects_closing_index_owned_by_other_pipeline(
    tmp_path: Path,
) -> None:
    payload_file = tmp_path / "PO025_indices.json"
    payload_file.write_text("{}", encoding="utf-8")
    payload = {
        "volume": {"volume_id": "PO025"},
        "works": [],
        "sections": [
            {
                "section_key": "PO025:scripture",
                "scope_kind": "work",
                "index_kind": "PO_WORK_INDEX_SCRIPTURE",
                "heading_raw": "TABLE DES CITATIONS DE LA BIBLE",
                "file_start": "page-001.txt",
                "file_end": "page-001.txt",
                "raw_json": {},
                "entries": [
                    {
                        "entry_key": "PO025:scripture:001",
                        "entry_raw": "GENESIS, I, 1",
                    }
                ],
            }
        ],
        "notes": [],
    }

    with pytest.raises(SystemExit, match="closing alphabetical/citation pipeline"):
        validate_payload(payload, "PO025", payload_file)


def test_validate_payload_rejects_inverted_work_page_range(tmp_path: Path) -> None:
    payload_file = tmp_path / "PG001_indices.json"
    payload_file.write_text("{}", encoding="utf-8")
    payload = {
        "volume": {"volume_id": "PG001"},
        "works": [
            {
                "work_key": "PG001:work:1",
                "start_page": 579,
                "end_page": 508,
            }
        ],
        "sections": [],
        "notes": [],
    }

    with pytest.raises(SystemExit, match="impossible editorial page ranges"):
        validate_payload(payload, "PG001", payload_file)


def test_prevalidate_existing_payload_returns_exact_current_failure(tmp_path: Path) -> None:
    payload_file = tmp_path / "PL001_indices.json"
    payload_file.write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PL001"},
                "sections": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    payload, failure = prevalidate_existing_payload(payload_file, "PL001")

    assert payload is None
    assert failure is not None
    assert failure["stage"] == "prevalidate_existing_payload"
    assert failure["error_detail"] == "Missing top-level key: works"


def test_run_index_extraction_loads_failure_artifact_and_builds_special_blocks(tmp_path: Path) -> None:
    failure_path = tmp_path / "PL001_failure.json"
    failure_payload = {
        "stage": "import_payload",
        "payload_file": str(tmp_path / "PL001_indices.json"),
        "error_summary": "payload import failed",
        "error_detail": (
            "import_index_json.py failed for PL001_indices.json\n"
            "STDERR:\nentries[2].entry_raw appears to contain an OCR line-break hyphen artifact."
        ),
    }
    failure_path.write_text(json.dumps(failure_payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_failure_artifact(failure_path)
    assert loaded == failure_payload

    failure_block = build_previous_failure_prompt_block("PL001", loaded)
    assert "PREVIOUS FAILURE" in failure_block
    assert "Fix the exact failure for PL001" in failure_block
    assert "entries[2].entry_raw" in failure_block

    recovery_block = build_hyphen_rerun_recovery_block()
    assert "HYPHEN RERUN RECOVERY" in recovery_block
    assert "read_ocr_page_text.py --view xml --show-source <file>" in recovery_block


def test_previous_failure_prompt_deduplicates_and_truncates_recursive_codex_error() -> None:
    repeated_recovery = (
        "Fix the exact failure for PG022; do not merely regenerate the same payload.\n"
        "### HYPHEN RERUN RECOVERY\n"
        "Re-read every affected source with read_ocr_page_text.py.\n"
    )
    terminal_error = (
        "Error: turn/start failed: Input exceeds the maximum length of 1048576 "
        "characters; actual_chars=2643474"
    )
    failure = {
        "stage": "codex",
        "error_summary": "codex exec failed for PG022",
        "error_detail": (
            "codex exec failed for PG022\nSTDERR:\nuser\n"
            + "".join(f"unique OCR transcript line {index}\n" for index in range(20_000))
            + repeated_recovery * 5
            + terminal_error
        ),
    }

    block = build_previous_failure_prompt_block("PG022", failure)

    assert len(block) < 13_000
    assert block.count("### HYPHEN RERUN RECOVERY") == 1
    assert "deduplicated" in block
    assert "truncated from" in block
    assert terminal_error in block


def test_compact_failure_text_preserves_small_diagnostic_verbatim() -> None:
    detail = "validator failed\nrefs[2].target_file is missing"

    compacted, duplicate_count, truncated = compact_failure_text(detail, 500)

    assert compacted == detail
    assert duplicate_count == 0
    assert truncated is False


def test_failure_artifact_does_not_persist_full_recursive_transcript(tmp_path: Path) -> None:
    path = tmp_path / "PG022_failure.json"
    detail = (
        "codex exec failed for PG022\n"
        + "".join(f"OCR transcript {index}\n" for index in range(30_000))
        + "entry_raw has a line-break hyphen artifact\n"
        + "Error: Input exceeds the maximum length; actual_chars=2643474"
    )

    write_failure_artifact(
        path=path,
        volume_id="PG022",
        collection="PG",
        stage="codex",
        error=SystemExit(detail),
        payload_file=None,
        prescan_file=None,
        filtered_pages_file=None,
        editorial_pages_file=None,
        helper_request_file=None,
        helper_output_file=None,
        last_message_file=None,
        stdout_log_file=tmp_path / "PG022_stdout.log",
        stderr_log_file=tmp_path / "PG022_stderr.log",
        stream_log_file=tmp_path / "PG022_stream.log",
    )

    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_size < 30_000
    assert artifact["error_detail_truncated"] is True
    assert artifact["error_detail_original_chars"] == len(detail)
    assert artifact["mentions_hyphen_artifact"] is True
    assert "actual_chars=2643474" in artifact["error_detail"]


def test_build_work_anchor_rerun_prompt_block_lists_pending_works() -> None:
    payload = {
        "works": [
            {
                "work_key": "PG001:work:1",
                "title_raw": "OPUS DUBIUM",
                "start_page": 349,
                "start_file": "/tmp/PG001/text/page-100.txt",
                "raw_json": {
                    "work_anchor_rerun": {
                        "status": "ambiguous",
                        "reason": "locator_candidate_probability_gap",
                        "locator": {
                            "candidates": [
                                {"file": "/tmp/PG001/text/page-100.txt"},
                                {"file": "/tmp/PG001/text/page-200.txt"},
                            ]
                        },
                    }
                },
            }
        ]
    }

    block = build_work_anchor_rerun_prompt_block(payload, "PG001")

    assert block is not None
    assert "WORK ANCHOR RERUN" in block
    assert "PG001:work:1" in block
    assert "Levenshtein" in block
    assert "at least four files" in block
    assert "up to three missing/corrupt headers" in block
    assert "separate OCR/XML blocks" in block
    assert "end_page to next_start - 1" in block
    assert "Pure external `Vide ... tom.` remissions" in block
    assert "nested anchor_locator_review" in block
    assert "preserve the marker with status=ambiguous" in block


def test_patristic_prompt_keeps_localization_artifacts_as_aids(tmp_path: Path) -> None:
    prescan = tmp_path / "PL001_prescan.json"
    filtered = tmp_path / "PL001_filtered_pages.json"
    editorial_pages = tmp_path / "PL001_editorial_pages.json"
    helper_request = tmp_path / "PL001_helper_request.json"
    helper_output = tmp_path / "PL001_helper_output.json"
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    prescan.write_text(
        json.dumps({"volume": "PL001", "all_hits": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    filtered.write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "source": "fallback_internal",
                "candidate_files": ["/tmp/PL001/text/volume-001.txt"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    helper_request.write_text(
        json.dumps({"volume_id": "PL001", "entries": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    editorial_pages.write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "status": "ok",
                "files": [
                    {
                        "file": "/tmp/PL001/text/volume-001.txt",
                        "best_guess": [1, 2],
                        "confidence_label": "high",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    helper_output.write_text(
        json.dumps({"volume_id": "PL001", "entries": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PROMPT_SCRIPT),
            "--volume",
            "PL001",
            "--source-root",
            "/tmp/PL001/text",
            "--collection",
            "PL",
            "--prescan-json",
            str(prescan),
            "--filtered-pages-json",
            str(filtered),
            "--editorial-pages-json",
            str(editorial_pages),
            "--helper-request-json",
            str(helper_request),
            "--helper-output-json",
            str(helper_output),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )

    prompt = result.stdout
    assert prompt.splitlines()[0] == "$patristic-index-extractor"
    assert "$patristic-index-extractor" in prompt
    assert "$alphabetical-index-extractor" not in prompt
    assert "Do not extract closing alphabetical" in prompt
    assert "inspect the closing pages for final indexes" not in prompt.lower()
    assert "Levenshtein/fuzzy title matches are additive evidence" in prompt
    assert "same block or separate blocks" in prompt
    assert "at least four physical files" in prompt
    assert "gaps of up to three files" in prompt
    assert "Never derive `end_page` as `next_start - 1`" in prompt
    assert "pure external remission" in prompt
    assert "Resolve every work_anchor_rerun/anchor_locator_review" in prompt
    assert "Use FILTERED PAGES and HELPER EVIDENCE only as localization aids" in prompt
    assert "python scripts/read_ocr_page_text.py --view xml --show-source" in prompt
    assert "automatically joins likely within-block word wraps" in prompt
    assert "fix_linebreak_hyphens.py" in prompt
    assert "not which pages you may investigate" in prompt
    assert "PHASE OWNERSHIP" in prompt
    assert "This is the final-payload agent phase" in prompt
    assert "do not rewrite them" in prompt
    assert "acknowledgment confirms only that the JSON was written" in prompt
    assert "Never invoke `init_index_db.py`, `import_index_json.py`" in prompt
    assert "driver\nalone validates and imports" in prompt
    assert (
        f"python scripts/verify_index_payload_evidence.py --input "
        f"{output_dir / 'PL001_indices.json'} --sample-size 200"
    ) in prompt
    assert "Filtered-page localization artifact" in prompt
    assert "Editorial page-to-file estimator artifact" in prompt
    assert "Target-locator helper output" in prompt
    assert str(output_dir / "PL001_indices.json") in prompt


def _make_text_volume(root: Path, volume_id: str) -> None:
    text_root = root / volume_id / "text"
    text_root.mkdir(parents=True)
    (text_root / f"{volume_id.lower()}-0001.txt").write_text("sample", encoding="utf-8")


def _configure_successful_batch_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    imported: list[str] = []

    monkeypatch.setattr(run_index_extraction, "select_volume_ids", lambda root, blob, limit: ["PL001", "PL002"])
    monkeypatch.setattr(
        run_index_extraction,
        "scan_volume",
        lambda volume_id, root: {"volume": {"volume_id": volume_id}, "all_hits": []},
    )
    monkeypatch.setattr(
        run_index_extraction,
        "build_filtered_pages_artifact",
        lambda **kwargs: {"volume_id": kwargs["volume_id"], "source": "test_stub", "candidate_files": []},
    )
    monkeypatch.setattr(
        run_index_extraction,
        "build_editorial_pages_artifact",
        lambda **kwargs: {"volume_id": kwargs["volume_id"], "status": "ok", "summary": None},
    )
    monkeypatch.setattr(
        run_index_extraction,
        "build_helper_request_artifact",
        lambda **kwargs: {"volume_id": kwargs["volume_id"], "entries": []},
    )
    monkeypatch.setattr(
        run_index_extraction,
        "run_helper_locator",
        lambda helper_request, helper_output_json: {"volume_id": helper_request["volume_id"], "entries": []},
    )
    monkeypatch.setattr(run_index_extraction, "build_prompt", lambda *args, **kwargs: "stub prompt")

    def fake_run_codex(**kwargs):
        payload_path = tmp_path / f"{Path(kwargs['last_message_path']).stem.replace('_last_message', '')}_indices.json"
        volume_id = payload_path.stem.replace("_indices", "")
        payload = {
            "volume": {"volume_id": volume_id},
            "works": [],
            "sections": [{"section_key": "s1", "entries": [{"entry_raw": "entry"}]}],
            "notes": [],
        }
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        kwargs["last_message_path"].write_text(
            json.dumps(
                {"status": "ok", "volume_id": volume_id, "written_file": str(payload_path)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_index_extraction, "run_codex", fake_run_codex)
    monkeypatch.setattr(run_index_extraction, "validate_payload", lambda payload, volume_id, expected_file: None)
    monkeypatch.setattr(
        run_index_extraction,
        "import_payload",
        lambda payload_file, replace: imported.append(payload_file.stem.replace("_indices", "")),
    )
    return imported


def test_run_index_extraction_batch_stops_on_first_failure_without_continue_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "teste"
    _make_text_volume(root, "PL001")
    _make_text_volume(root, "PL002")

    monkeypatch.setattr(run_index_extraction, "select_volume_ids", lambda root, blob, limit: ["PL001", "PL002"])

    def fake_scan(volume_id: str, root: Path) -> dict[str, object]:
        if volume_id == "PL001":
            raise SystemExit("scan_volume.py failed for PL001\nSTDOUT:\n\nSTDERR:\nboom")
        return {"volume": {"volume_id": volume_id}, "all_hits": []}

    monkeypatch.setattr(run_index_extraction, "scan_volume", fake_scan)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction.py",
            "--all-volumes",
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        run_index_extraction.main()

    assert "scan_volume.py failed for PL001" in str(excinfo.value)
    captured = capsys.readouterr()
    assert "[INFO] volume 1/2: PL001 (PL)" in captured.out
    assert "PL002" not in captured.out
    failure_path = tmp_path / "out" / "PL001_failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["stage"] == "scan"
    assert "scan_volume.py failed" in failure["error_detail"]


def test_run_index_extraction_batch_continue_on_error_writes_failure_artifact_and_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "teste"
    _make_text_volume(root, "PL001")
    _make_text_volume(root, "PL002")
    imported = _configure_successful_batch_stubs(monkeypatch, tmp_path / "out")

    def fake_import(payload_file: Path, db_path: Path, replace: bool) -> None:
        volume_id = payload_file.stem.replace("_indices", "")
        if volume_id == "PL001":
            raise SystemExit("import_index_json.py failed for PL001_indices.json\nSTDOUT:\n\nSTDERR:\ninvalid payload")
        imported.append(volume_id)

    monkeypatch.setattr(run_index_extraction, "import_payload", fake_import)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction.py",
            "--all-volumes",
            "--continue-on-error",
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        run_index_extraction.main()

    assert excinfo.value.code == 1
    assert imported == ["PL002"]

    captured = capsys.readouterr()
    stdout_lines = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    assert any(line["status"] == "failed" and line["volume_id"] == "PL001" for line in stdout_lines)
    assert any(line["status"] == "ok" and line["volume_id"] == "PL002" for line in stdout_lines)
    summary = next(line for line in stdout_lines if line["status"] == "batch-complete-with-errors")
    assert summary["failed_volumes"] == ["PL001"]
    assert summary["failed_count"] == 1
    assert "[ERROR] volume=PL001 import_index_json.py failed for PL001_indices.json" in captured.err

    failure_path = tmp_path / "out" / "PL001_failure.json"
    assert failure_path.exists()
    failure_payload = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure_payload["volume_id"] == "PL001"
    assert failure_payload["stage"] == "import_payload"
    assert "import_index_json.py failed" in failure_payload["error_detail"]


def test_run_index_extraction_single_volume_continue_on_error_still_exits_with_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "teste"
    _make_text_volume(root, "PL001")
    _configure_successful_batch_stubs(monkeypatch, tmp_path / "out")
    monkeypatch.setattr(
        run_index_extraction,
        "import_payload",
        lambda payload_file, db_path, replace: (_ for _ in ()).throw(
            SystemExit("import_index_json.py failed for PL001_indices.json\nSTDOUT:\n\nSTDERR:\nsingle failure")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction.py",
            "--volume-id",
            "PL001",
            "--continue-on-error",
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        run_index_extraction.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    stdout_lines = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    assert stdout_lines[-1]["status"] == "batch-complete-with-errors"
    assert stdout_lines[-1]["total_volumes"] == 1
    assert "[ERROR] volume=PL001 import_index_json.py failed for PL001_indices.json" in captured.err


def test_verbose_dry_run_reports_every_volume_stage_with_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "teste"
    _make_text_volume(root, "PL001")
    monkeypatch.setattr(
        run_index_extraction,
        "scan_volume",
        lambda volume_id, root: {"volume": {"volume_id": volume_id}, "all_hits": []},
    )
    monkeypatch.setattr(
        run_index_extraction,
        "build_filtered_pages_artifact",
        lambda **kwargs: {
            "volume_id": kwargs["volume_id"],
            "source": "test_stub",
            "candidate_files": [],
        },
    )
    monkeypatch.setattr(
        run_index_extraction,
        "build_editorial_pages_artifact",
        lambda **kwargs: {"volume_id": kwargs["volume_id"], "status": "ok"},
    )
    monkeypatch.setattr(run_index_extraction, "build_prompt", lambda *args, **kwargs: "stub prompt")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_extraction.py",
            "--volume-id",
            "PL001",
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
            "--intermediate-root",
            str(tmp_path / "intermediate"),
            "--dry-run",
            "--verbose",
        ],
    )

    run_index_extraction.main()

    captured = capsys.readouterr()
    assert "[VOLUME 1/1 PL001] [STAGE 1/12] START prescan OCR" in captured.out
    assert "[VOLUME 1/1 PL001] [STAGE 1/12] DONE prescan OCR" in captured.out
    assert "[VOLUME 1/1 PL001] [STAGE 6/12] SKIP extract semantic chunks" in captured.out
    assert "[VOLUME 1/1 PL001] [STAGE 12/12] SKIP import payload" in captured.out

    progress_log = tmp_path / "logs" / "PL001_progress.log"
    progress = progress_log.read_text(encoding="utf-8")
    assert "[STAGE 1/12] START prescan OCR" in progress
    assert "[STAGE 12/12] SKIP import payload" in progress
