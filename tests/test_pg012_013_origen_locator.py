"""
Tests for PG012_013_OrigenLocator (Sprint 32)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pg012_013_origen_locator import (
    PG012_013_OrigenLocator,
    PG012_MAP,
    PG013_MAP,
    DB_PATH
)


def test_origen_mapping_completeness():
    assert len(PG012_MAP) == 23
    assert len(PG013_MAP) == 26
    
    locator = PG012_013_OrigenLocator()
    
    # Check all PG012 files exist
    for item_id, (p_num, reason) in PG012_MAP.items():
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page('PG012', p_num)
        assert fpath is not None, f"Missing file for PG012 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"
        
    # Check all PG013 files exist
    for item_id, (p_num, reason) in PG013_MAP.items():
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page('PG013', p_num)
        assert fpath is not None, f"Missing file for PG013 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_origen_resolution():
    locator = PG012_013_OrigenLocator()
    
    # 1. PG012 Merrha (Exod hom 7, Delarue 151-152) -> physical page 175
    res1 = locator.resolve_item(
        'group:82691786b0aea6ed72b155a4b0cae18ce80ade8b1a80d72a406a5bf5fc946a6e',
        'PG012',
        'Merrha, et aqua amara',
        '151, et seqq'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PG012'
    assert res1[0]['physical_page'] == 175
    assert '175.txt' in res1[0]['target_file']
    
    # 2. PG012 Fati necessitate (Gen hom 1, Delarue 4) -> physical page 30
    res2 = locator.resolve_item(
        'group:9c0ca21a47bbd4312bb444e6151cd796b0a7c577f1ec1e859d639a77072e618c',
        'PG012',
        'Fati necessitate omnia fieri impium est',
        '4, et seqq'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PG012'
    assert res2[0]['physical_page'] == 30
    assert '030.txt' in res2[0]['target_file']

    # 3. PG013 Hosanna (Matth series, Delarue 449) -> physical page 726
    res3 = locator.resolve_item(
        'group:4947c11b5d9af46a4ab1ea31340485315568a2b9b065f7a7422d7c7480b5778c',
        'PG013',
        'Hosanna vox laetitiam significat',
        '449, et seqq'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PG013'
    assert res3[0]['physical_page'] == 726
    assert '726.txt' in res3[0]['target_file']

    # 4. PG013 Secundas nuptias (Luc hom 17, Delarue 953) -> physical page 933
    res4 = locator.resolve_item(
        'group:ad9b7e6c3c27b43440b470f507d4d7b859d8762ec7564ce4471058950a1ee662',
        'PG013',
        'In secundas nuptias non invehitur Origenes',
        '953, et seqq'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PG013'
    assert res4[0]['physical_page'] == 933
    assert '933.txt' in res4[0]['target_file']


def test_origen_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PG012_013_OrigenLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 49
        assert result["promotions_by_volume"]["PG012"] == 23
        assert result["promotions_by_volume"]["PG013"] == 26
    else:
        assert result["total_promoted"] == 0
