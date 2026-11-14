"""
Tests for PL050CassianLocator (Sprint 34)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl050_cassian_locator import (
    PL050CassianLocator,
    PL050_MAP,
    DB_PATH
)


def test_pl050_mapping_completeness():
    assert len(PL050_MAP) == 24
    locator = PL050CassianLocator()
    
    for item_id, (target_vol, p_num, cap_or_col, reason) in PL050_MAP.items():
        assert target_vol in ['PL049', 'PL050']
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl050_resolution():
    locator = PL050CassianLocator()
    
    # 1. Collat. VI, 2 -> PL049 page 330
    res1 = locator.resolve_item(
        'group:171bf25a9546a9beb46b076678c268c306d0eaa06d57786575a225ed70a10ede',
        'Afflictiones justorum juste a Deo immittuntur, Collat.',
        '2 et seq'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL049'
    assert res1[0]['physical_page'] == 330
    assert '330.txt' in res1[0]['target_file']

    # 2. Incarn. VI, 3 -> PL050 page 82
    res2 = locator.resolve_item(
        'group:b1238d349e6b9221a0428bf46ab6c5024bdfdc661fc1ffc00d61eae7e5b4746f',
        'Antiocheni symboli fides, ejusque expositio, Incarn. vi',
        '3 et seq'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL050'
    assert res2[0]['physical_page'] == 82
    assert '082.txt' in res2[0]['target_file']

    # 3. Gazaeus I, col 1507 (Balaam) -> PL049 page 660
    res3 = locator.resolve_item(
        'a705230d3b825766d665b6db2a383d07496b9141a2d665cc525d3f9ba5c73eba',
        'Balaam propheta avarus et malignus',
        'I, 1507'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL049'
    assert res3[0]['physical_page'] == 660
    assert '660.txt' in res3[0]['target_file']

    # 4. Gazaeus col 869 (Corpus peccati) -> PL049 page 441
    res4 = locator.resolve_item(
        'group:0996921ab19d29ad8670b0758e9ca76386531915150a017813fc5bceb7da9e78',
        'Corpus peccati et membra ejus',
        '869 seq'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL049'
    assert res4[0]['physical_page'] == 441
    assert '441.txt' in res4[0]['target_file']


def test_pl050_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL050CassianLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 24
    else:
        assert result["total_promoted"] == 0
