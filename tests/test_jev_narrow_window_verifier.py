"""Unit tests for jev_narrow_window_verifier.py."""
from pathlib import Path
import json
import pytest
from unittest.mock import MagicMock, patch

from scripts.jev_narrow_window_verifier import (
    resolve_candidate_window_for_column,
    compute_lexical_hint,
    build_candidate_window,
    build_jev_payload,
    execute_verifier,
    CandidateFile,
    WindowPlan,
    VolumeEditorialEstimatorCache,
    MODEL,
)


def test_resolve_candidate_window_exact_match():
    page_map = {100: "file_010.txt", 101: "file_010.txt", 102: "file_011.txt", 103: "file_011.txt"}
    physical_files = ["file_009.txt", "file_010.txt", "file_011.txt", "file_012.txt"]
    anchor_idx, radius, defects = resolve_candidate_window_for_column(102, page_map, physical_files)
    assert anchor_idx == 2
    assert radius == 1
    assert defects == []


def test_resolve_candidate_window_unprinted_preface():
    page_map = {11: "file_010.txt", 12: "file_010.txt", 13: "file_011.txt"}
    physical_files = [f"file_{i:03d}.txt" for i in range(1, 20)]
    # Column 4 is unprinted in introductory preface before column 11 starts
    anchor_idx, radius, defects = resolve_candidate_window_for_column(4, page_map, physical_files)
    assert anchor_idx == 0
    assert radius >= 3
    assert "unprinted_preface_or_introductory_columns" in defects


def test_resolve_candidate_window_interpolated_gap():
    page_map = {100: "file_010.txt", 120: "file_020.txt"}
    physical_files = [f"file_{i:03d}.txt" for i in range(30)]
    # Column 110 is midway between 100 and 120
    anchor_idx, radius, defects = resolve_candidate_window_for_column(110, page_map, physical_files)
    assert 14 <= anchor_idx <= 16
    assert radius >= 2
    assert any("gap" in d or "interpolated" in d for d in defects)


def test_compute_lexical_hint():
    lemma = "Augustinus"
    entry = "Augustinus de civitate Dei contra paganos"
    page_text_hit = "Sanctus Augustinus docet in libro de civitate Dei magnam veritatem."
    page_text_miss = "Nihil de hac re dicitur in hoc codice veteri."

    score_hit = compute_lexical_hint(lemma, entry, page_text_hit)
    score_miss = compute_lexical_hint(lemma, entry, page_text_miss)

    assert score_hit > score_miss
    assert score_hit > 0.4
    assert score_miss <= 0.1


def test_build_jev_payload():
    plan = WindowPlan(
        item_id="item-001",
        volume_id="PL105",
        target_volume="PL105",
        lemma_raw="Dungalus Reclusus",
        entry_raw="Dungalus Reclusus, CV, 474",
        ref_raw="474",
        section_kind="alphabetical",
        ref_type="point",
        target_col_start=474,
        target_col_end=474,
        center_file_seq=241,
        window_radius=1,
        ocr_defects=[],
        candidates=[],
        search_mode="single",
    )
    payload = build_jev_payload(plan, "OCR sample text containing Dungalus.")
    assert payload["model"] == MODEL
    assert "Dungalus Reclusus" in payload["questions"]["topic_present"]["instructions"]
    assert "Complete OCR" in payload["state"]


def test_execute_verifier_dry_run_early_stopping(tmp_path):
    cand1 = CandidateFile(
        path=str(tmp_path / "page_1.txt"),
        file_seq=1,
        header_text="100 LIB 101",
        observed_cols=[100, 101],
        hint_score=0.85,
    )
    cand2 = CandidateFile(
        path=str(tmp_path / "page_2.txt"),
        file_seq=2,
        header_text="102 LIB 103",
        observed_cols=[102, 103],
        hint_score=0.10,
    )

    plan = WindowPlan(
        item_id="item-test-01",
        volume_id="PL105",
        target_volume="PL105",
        lemma_raw="Augustinus",
        entry_raw="Augustinus 100",
        ref_raw="100",
        section_kind="thematic",
        ref_type="point",
        target_col_start=100,
        target_col_end=100,
        center_file_seq=1,
        window_radius=1,
        ocr_defects=[],
        candidates=[cand1, cand2],
        search_mode="single",
    )

    out_log = tmp_path / "test_log.jsonl"
    result = execute_verifier(
        plans=[plan],
        dry_run=True,
        out_jsonl=out_log,
        allow_cross_page=False,
    )

    assert result["status"] == "success"
    assert result["confirmed_items"] == 1
    # Early stopping should evaluate only cand1
    assert result["total_evaluations"] == 1
    assert out_log.exists()
    lines = out_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["status"] == "confirmed"
    assert data["primary_target"] == str(tmp_path / "page_1.txt")


def test_execute_verifier_thematic_full(tmp_path):
    cand1 = CandidateFile(
        path=str(tmp_path / "page_1.txt"),
        file_seq=1,
        header_text="100 LIB 101",
        observed_cols=[100, 101],
        hint_score=0.90,
    )
    cand2 = CandidateFile(
        path=str(tmp_path / "page_2.txt"),
        file_seq=2,
        header_text="102 LIB 103",
        observed_cols=[102, 103],
        hint_score=0.80,
    )

    plan = WindowPlan(
        item_id="item-test-thematic",
        volume_id="PL105",
        target_volume="PL105",
        lemma_raw="Trinitas",
        entry_raw="De Trinitate 100-103",
        ref_raw="100-103",
        section_kind="dogmatic",
        ref_type="span",
        target_col_start=100,
        target_col_end=103,
        center_file_seq=1,
        window_radius=1,
        ocr_defects=[],
        candidates=[cand1, cand2],
        search_mode="thematic_full",
    )

    out_log = tmp_path / "thematic_log.jsonl"
    result = execute_verifier(
        plans=[plan],
        dry_run=True,
        out_jsonl=out_log,
        workers=1,
    )

    # In thematic_full, both candidates are evaluated without early stopping
    assert result["total_evaluations"] == 2
    assert result["confirmed_items"] == 1
    lines = out_log.read_text(encoding="utf-8").strip().splitlines()
    data = json.loads(lines[0])
    assert len(data["span_targets"]) == 2


def test_execute_verifier_multiprocess_parallel(tmp_path):
    cand1 = CandidateFile(
        path=str(tmp_path / "page_1.txt"),
        file_seq=1,
        header_text="100 LIB 101",
        observed_cols=[100, 101],
        hint_score=0.90,
    )
    plans = [
        WindowPlan(
            item_id=f"item-parallel-{i}",
            volume_id="PL105",
            target_volume="PL105",
            lemma_raw=f"Lemma_{i}",
            entry_raw=f"Entry_{i}",
            ref_raw="100",
            section_kind="alphabetical",
            ref_type="point",
            target_col_start=100,
            target_col_end=100,
            center_file_seq=1,
            window_radius=1,
            ocr_defects=[],
            candidates=[cand1],
            search_mode="single",
        )
        for i in range(5)
    ]

    out_log = tmp_path / "parallel_log.jsonl"
    result = execute_verifier(
        plans=plans,
        dry_run=True,
        out_jsonl=out_log,
        workers=4,
        pool_type="process",
    )

    assert result["status"] == "success"
    assert result["total_plans"] == 5
    assert result["confirmed_items"] == 5
    assert result["workers"] == 4
    assert result["pool_type"] == "process"
    lines = out_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
