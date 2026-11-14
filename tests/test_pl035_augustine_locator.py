"""
Tests for PL035AugustineLocator (Sprint 35)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl035_augustine_locator import (
    PL035AugustineLocator,
    PL035_MAP,
    DB_PATH
)


def test_pl035_mapping_completeness():
    assert len(PL035_MAP) == 24
    locator = PL035AugustineLocator()
    
    for item_id, (target_vol, p_num, cap_or_col, reason) in PL035_MAP.items():
        assert target_vol in ['PL034', 'PL035']
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl035_resolution():
    locator = PL035AugustineLocator()
    
    # 1. De Consensu Evangelistarum IV, 6 (col 1220 -> PL034 page 610)
    res1 = locator.resolve_item(
        'fe5dda07e6a3c02eb6b10d65d872ed4c7f73d556c25a39127d88080125adc69b',
        'queretur, Marcus amplius quam Lucas Dominus divisse narravit...',
        '1220'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL034'
    assert res1[0]['physical_page'] == 610
    assert '610.txt' in res1[0]['target_file']

    # 2. De Sermone Domini Liber Secundus (cols 1269-1270 -> PL034 page 635)
    res2 = locator.resolve_item(
        'f2dcc628f1fd77c9a759e0b6eac8fa25edfcf5f34618fbff9896a75ec0e0d75c',
        'LIBER SECUNDUS.—In posteriorem partem sermonis Domini in monte...',
        '1269-1270'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL034'
    assert res2[0]['physical_page'] == 635
    assert '635.txt' in res2[0]['target_file']

    # 3. Quaestiones Evangeliorum Liber Primus (cols 1323-1324 -> PL035 page 9)
    res3 = locator.resolve_item(
        'ee7f2174b5a0573176ec72717def915a252e97e5bfa42ba7a97b1f6a577cf9cc',
        'LIBER PRIMUS. — Quæstiones in Evangelium secundum Matthæum',
        '1323-1324'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL035'
    assert res3[0]['physical_page'] == 9
    assert '009.txt' in res3[0]['target_file']

    # 4. Tractatus in Joannem CXV (col 1938 -> PL035 page 331)
    res4 = locator.resolve_item(
        '5fb1b22fe62e3679f58305649e432eb926a9a2824a9221658fa2eb505cf466db',
        'TRACT. CXV. De eo quod dicitur, Introivit ergo iterum in prætorium...',
        '33-40. 1938'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL035'
    assert res4[0]['physical_page'] == 331
    assert '331.txt' in res4[0]['target_file']


def test_pl035_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL035AugustineLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 24
    else:
        assert result["total_promoted"] == 0
