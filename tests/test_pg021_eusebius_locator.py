"""
Tests for PG021EusebiusLocator (Sprint 42)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pg021_eusebius_locator import (
    PG021EusebiusLocator,
    PG021_MAP,
    DB_PATH
)


def test_pg021_mapping_completeness():
    assert len(PG021_MAP) == 18
    locator = PG021EusebiusLocator()

    for item_id, (target_vol, p_num, cap_or_col, reason) in PG021_MAP.items():
        assert target_vol == 'PG021'
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pg021_resolution():
    locator = PG021EusebiusLocator()

    # 1. Socrates physicos (col 853 -> p. 439)
    res1 = locator.resolve_item(
        'region-entry:32eaf08d951586418508ff497b566c82bf65ce1e905ac91968bb72154a965795',
        'Socratem inducit Physicos omnes illudentem',
        '853 et seq'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PG021'
    assert res1[0]['physical_page'] == 439
    assert '439.txt' in res1[0]['target_file']

    # 2. Plotinus de immortalitate animae (col 841 -> p. 433)
    res2 = locator.resolve_item(
        '8d24081be1a753ebb6d4d6a6021443e49f67c28ea1092a2495f89f90cecbc912',
        'Plotinus ἐκ τοῦ Περ ἀθανασίας ψυχῆς δευτέρου (contra Aristotelem, qui animam entelechiam esse contendit)',
        '841 seq'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PG021'
    assert res2[0]['physical_page'] == 433
    assert '433.txt' in res2[0]['target_file']

    # 3. Antigonus Carystius (col 762 -> p. 393)
    res3 = locator.resolve_item(
        'region-entry:b2e147ea0b948b25bb37d5f99c56130c1c8cce029b547f4bf6ac35d40cdc10f3',
        'Antigonus Carystius Pyrrhonis, æqualis. Ejus Vitam scripsit',
        '762 seq'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PG021'
    assert res3[0]['physical_page'] == 393
    assert '393.txt' in res3[0]['target_file']

    # 4. Origenes adversus fatum (col 281 -> p. 153)
    res4 = locator.resolve_item(
        'region-entry:4281e6453b2ca6e7b3f7e5c83b45fc1bc47e91d64c0a7de18a7a625d94d3842d',
        'Origenes adversus fatum disputat',
        '281 et seq'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PG021'
    assert res4[0]['physical_page'] == 153
    assert '153.txt' in res4[0]['target_file']

    # 5. Medicamenta (col 131 -> p. 78)
    res5 = locator.resolve_item(
        'region-entry:0a937324ba105b11d2aba36c2d3775a743c1e9675f442e974501a7a4d4ac4711',
        'Medicamenta ex rebus naturalibus',
        '131 et seq'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PG021'
    assert res5[0]['physical_page'] == 78
    assert '078.txt' in res5[0]['target_file'] or '78.txt' in res5[0]['target_file']


def test_pg021_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PG021EusebiusLocator(db_path=DB_PATH)
    res = locator.apply_promotions(dry_run=True)
    assert res['status'] == 'dry_run_complete'
    assert res['dry_run'] is True
    if res['total_evaluated'] > 0:
        assert res['total_promoted'] == 18
    else:
        assert res['total_promoted'] == 0
