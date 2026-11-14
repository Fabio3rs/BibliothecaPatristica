"""
Tests for PL221MethodicusLocator (Sprint 30)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl221_methodicus_locator import (
    PL221MethodicusLocator,
    PL221_EXACT_MAP,
    DB_PATH
)


def test_pl221_exact_mapping_completeness():
    assert len(PL221_EXACT_MAP) == 31
    for item_id, (vol, col, fname, reason) in PL221_EXACT_MAP.items():
        assert vol.startswith("PL")
        assert col > 0
        assert fname.endswith(".txt")
        assert len(reason) > 0

        target_path = Path("teste") / vol / "text" / fname
        assert target_path.exists(), f"Target file does not exist: {target_path}"


def test_pl221_resolution():
    locator = PL221MethodicusLocator()

    # 1. Heribert of Eichstett (appendix in PL141)
    res = locator.resolve_item(
        "e59e743ea6e61dad51d5a73f966f097dbbb6df72d44094f48a1c729a9bac5e7a",
        "HERIBERTUS Eichstettensis episcopus. — Herberti hymni",
        "CXL, 1569"
    )
    assert len(res) == 1
    assert res[0]["target_volume"] == "PL141"
    assert "3db4fee2-5a08-42ac-bd40-31116c6b1681-689.txt" in res[0]["target_file"]

    # 2. Concordatum Wormatiense (PL163 col 1359)
    res2 = locator.resolve_item(
        "c1477e44d473f85786cb4b5e30ebfa2a414174393a97ba8c8f7bfef08fe468bf",
        "Concordatum Wormatiense",
        "1359"
    )
    assert len(res2) == 1
    assert res2[0]["target_volume"] == "PL163"
    assert res2[0]["target_column"] == 1359

    # 3. Ambrose Anacephaleosis (PL015 col 2205)
    res3 = locator.resolve_item(
        "1731f74a15e994bc6182dd96250da8daa22a3e1f05c7191a74f0bcf7f0498f1e",
        "Anacephaleosis historiæ de excidio urbis Hierosolymitanæ",
        "2205"
    )
    assert len(res3) == 1
    assert res3[0]["target_volume"] == "PL015"
    assert res3[0]["target_column"] == 2205

    # 4. Augustine Regula ad servos Dei (PL032 col 1377)
    res4 = locator.resolve_item(
        "70dfb89910265d9cce6a650b034bff8df95ee027927b7ef92f0305c61dee28c4",
        "Regula ad servos Dei S. Augustino scripta",
        "XXX, 1377"
    )
    assert len(res4) == 1
    assert res4[0]["target_volume"] == "PL032"
    assert res4[0]["target_column"] == 1377


def test_pl221_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL221MethodicusLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    # If not yet applied, promoted is 31; if already applied, evaluated is 0
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 31
    else:
        assert result["total_promoted"] == 0
