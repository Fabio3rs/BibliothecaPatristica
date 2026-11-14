"""
Tests for PL042AugustineLocator (Sprint 37)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl042_augustine_locator import (
    PL042AugustineLocator,
    PL042_MAP,
    DB_PATH
)


def test_pl042_mapping_completeness():
    assert len(PL042_MAP) == 22
    locator = PL042AugustineLocator()
    
    for item_id, (p_num, cap_or_col, reason) in PL042_MAP.items():
        assert p_num in [430, 459, 460, 469, 473, 487, 495, 496, 502, 503, 506, 513, 515, 521, 523, 531, 532, 543, 554, 564]
        assert len(reason) > 0
        fpath = locator.get_file_for_page(p_num)
        assert fpath is not None, f"Missing file for PL042 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl042_resolution():
    locator = PL042AugustineLocator()
    
    # 1. Liber II, cap. IX (col 854 -> p. 430)
    res1 = locator.resolve_item(
        'region-entry:e441afec269bf40b7aa753d10ab02fce72e287f7b6aac52c7b824fe234676f62',
        'IX. Contra eos qui credebant solum Patrem immortalem...',
        'Ibid.'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL042'
    assert res1[0]['physical_page'] == 430
    assert '430.txt' in res1[0]['target_file']

    # 2. Liber V, cap. II (cols 911-912 -> p. 459)
    res2 = locator.resolve_item(
        'region-entry:5153745806035bc46454e1351c32e6dfe7eb916b2e2bb3c5c3cc2e79b385330e',
        'II. Arianorum argumentum ex voce geniti...',
        'ibid.'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL042'
    assert res2[0]['physical_page'] == 459
    assert '459.txt' in res2[0]['target_file']

    # 3. Liber XI, cap. I (cols 983-984 -> p. 495)
    res3 = locator.resolve_item(
        'region-entry:86d0ff7d7b48a945e91f7edd8ccb1a6cdfde8634ac55e2da63ceec726e86ea8e',
        'CAPUT PRIMUM. Vestigia Trinitatis etiam in exteriore homine',
        '985-984'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL042'
    assert res3[0]['physical_page'] == 495
    assert '495.txt' in res3[0]['target_file']

    # 4. Liber XIV, cap. XIX (col 1055 -> p. 531)
    res4 = locator.resolve_item(
        'c987f120f48a1a8d70e630924820dfc7c93861b32a5dcab73973887110718893',
        'similitudine cum Trinitate in vita æterna...',
        'ibid.'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL042'
    assert res4[0]['physical_page'] == 531
    assert '531.txt' in res4[0]['target_file']

    # 5. Sermo contra Judaeos, cap. X (col 1122 -> p. 564)
    res5 = locator.resolve_item(
        'region-entry:82fe400161913b9dbaa9a80e313d03c77b652f213b2cb607102931ff03561db7',
        'X. Crudelitas Herodis',
        'ibid.'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL042'
    assert res5[0]['physical_page'] == 564
    assert '564.txt' in res5[0]['target_file']


def test_pl042_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL042AugustineLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 22
    else:
        assert result["total_promoted"] == 0
