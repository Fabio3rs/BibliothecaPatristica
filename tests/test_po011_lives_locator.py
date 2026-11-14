"""
Tests for PO011LivesLocator (Sprint 31)
"""

import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.po011_lives_locator import (
    PO011LivesLocator,
    PO011_EXACT_MAP,
    DB_PATH
)


def test_po011_exact_mapping_completeness():
    assert len(PO011_EXACT_MAP) == 27
    for item_id, (p_num, reason) in PO011_EXACT_MAP.items():
        assert 150 <= p_num <= 500
        assert len(reason) > 0

        target_files = list(Path("teste/PO011/text").glob(f"*-{p_num}.txt"))
        assert len(target_files) == 1, f"Missing file for physical page {p_num}"


def test_po011_resolution():
    locator = PO011LivesLocator()

    # 1. Saint Luc bracket [33] -> physical page 183 (Attikom, Bandou-lampes)
    res = locator.resolve_item(
        "region-entry:6fe80f05be3cb4ce9d06eead88828c6dfb8e4685511329cc1e8e75d060107ff2",
        "Ἀττικώμ",
        "33 Ba."
    )
    assert len(res) == 1
    assert res[0]["target_volume"] == "PO011"
    assert res[0]["physical_page"] == 183
    assert "-183.txt" in res[0]["target_file"]

    # 2. Saint Luc bracket [91] -> physical page 241 (Theodora, Sophie)
    res2 = locator.resolve_item(
        "region-entry:0494fb3e288bdc4b7eb96b2d27606996127ad94e8ac5a52384537e7eca2f46e8",
        "Θεοδώρα, femme de l'empereur Romain",
        "91, n. 1 de la trad."
    )
    assert len(res2) == 1
    assert res2[0]["physical_page"] == 241
    assert "-241.txt" in res2[0]["target_file"]

    # 3. Ammonas plain 400 -> physical page 408 (Jean d'Antioche)
    res3 = locator.resolve_item(
        "region-entry:36c5320552160f9bb6d1b3775d477d7e2cd512942dadf99444a973e644fa4ebf",
        "Jean, patriarche d'Antioche",
        "1100, résume Ammonas, 400."
    )
    assert len(res3) == 1
    assert res3[0]["physical_page"] == 408
    assert "-408.txt" in res3[0]["target_file"]

    # 4. Ammonas remissio Gardien V. Ange -> physical page 443
    res4 = locator.resolve_item(
        "region-entry:4e97b7e501ca37aeba4ceb2ccbfc8524c1fe6cf60325c28eab58788e8d76a1e3",
        "Gardien",
        "V. Ange"
    )
    assert len(res4) == 1
    assert res4[0]["physical_page"] == 443
    assert "-443.txt" in res4[0]["target_file"]


def test_po011_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PO011LivesLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 27
    else:
        assert result["total_promoted"] == 0
