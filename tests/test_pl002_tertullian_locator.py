"""
Tests for PL002TertullianLocator (Sprint 40)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl002_tertullian_locator import (
    PL002TertullianLocator,
    PL002_MAP,
    DB_PATH
)


def test_pl002_mapping_completeness():
    assert len(PL002_MAP) == 18
    locator = PL002TertullianLocator()

    for item_id, (target_vol, p_num, cap_or_col, reason) in PL002_MAP.items():
        assert target_vol in ['PL001', 'PL002']
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl002_resolution():
    locator = PL002TertullianLocator()

    # 1. Enoch (col 1307 -> PL001, p. 675)
    res1 = locator.resolve_item(
        'group:800b9019faef74a2000b72a63b6fd24dd0d8d536eeff4c65c9068cf4dc99537f',
        'ENOCH libri verba citantur, I, 665 C, 666 A, 2507 A. In armarium Judaicum non admittuntur',
        'ibid'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL001'
    assert res1[0]['physical_page'] == 675
    assert '675.txt' in res1[0]['target_file']

    # 2. Hostanes / Rostanes (De Anima cap. 57 -> PL002, p. 382)
    res2 = locator.resolve_item(
        'group:a87c280cdedc32c1cca1b597975ee8a61e953a1069e316ef1f846ba9368727bc',
        'ROSTANES',
        'ibid'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL002'
    assert res2[0]['physical_page'] == 382
    assert '382.txt' in res2[0]['target_file']

    # 3. Plato Socrate (col 503 -> PL002, p. 256)
    res3 = locator.resolve_item(
        'group:6142fee5939e609f7850b1b40f2ccb1dd8c27ed36cd738826f4093f7fc3d9215',
        'PLATO, I, 405 A, 416 B, 417 A, II, 1033 B; in Timæo, II, 481 A; in Socrate, II, 503 A, 509 A ; in Timæo',
        'Ibid'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL002'
    assert res3[0]['physical_page'] == 256
    assert '256.txt' in res3[0]['target_file']

    # 4. Plato Phaedro (col 659 -> PL002, p. 338)
    res4 = locator.resolve_item(
        'group:0b4fb1e4d51d040c41ecde9a245889eab7dbf9ba74b81d359e24600a40722c8c',
        'in Phædro, II, 659 A; in Timæo, II, 661 A, 666 A; in Phædro, II, 668 B; in Timæo',
        'Ibid'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL002'
    assert res4[0]['physical_page'] == 338
    assert '338.txt' in res4[0]['target_file']

    # 5. Plato Legibus (col 693 -> PL002, p. 355)
    res5 = locator.resolve_item(
        'group:093b2440a432e1a14cf6e96a03945444211da24ebd454d2626d292799a148b77',
        'et II, 693 A; in Legibus',
        'Ibid'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL002'
    assert res5[0]['physical_page'] == 355
    assert '355.txt' in res5[0]['target_file']


def test_pl002_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL002TertullianLocator(db_path=DB_PATH)
    res = locator.apply_promotions(dry_run=True)
    assert res['status'] == 'dry_run_complete'
    assert res['dry_run'] is True
    if res['total_evaluated'] > 0:
        assert res['total_promoted'] == 18
    else:
        assert res['total_promoted'] == 0
