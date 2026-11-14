"""
Tests for PL101AlcuinLocator (Sprint 39)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl101_alcuin_locator import (
    PL101AlcuinLocator,
    PL101_MAP,
    DB_PATH
)


def test_pl101_mapping_completeness():
    assert len(PL101_MAP) == 19
    locator = PL101AlcuinLocator()

    for item_id, (target_vol, p_num, cap_or_col, reason) in PL101_MAP.items():
        assert target_vol in ['PL100', 'PL101']
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl101_resolution():
    locator = PL101AlcuinLocator()

    # 1. Ablactatio (I, 321 -> PL100, p. 276)
    res1 = locator.resolve_item(
        'group:4c65ceb7b70fb085455f1abdf4271a97d2a370eaffc3229d80fbcd65381aa6f1',
        'Ablactatio quo anno fiebat apud Hebræos',
        'I, 321.'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL100'
    assert res1[0]['physical_page'] == 276
    assert '276.txt' in res1[0]['target_file']

    # 2. Atrebatensis synodus (II, 377 -> PL101, p. 513)
    res2 = locator.resolve_item(
        'group:d5e2a96b56fdb5a7fe8988ddad76f4672e04351a2ba03280fa20c84f4c5e5eb3',
        'Atrebatensis synodus contra Italos quosdam in fide errantes',
        'II, 377. —'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL101'
    assert res2[0]['physical_page'] == 513
    assert '513.txt' in res2[0]['target_file']

    # 3. Quo remedio liberati (ibid. et 487 -> PL101, p. 618)
    res3 = locator.resolve_item(
        'group:3cc9674c6e32a060ffd3f5ca0a18ec7001feab917db1425158b2dfb6ed847fa4',
        'Quo remedio liberati',
        'ibid. et 487.'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL101'
    assert res3[0]['physical_page'] == 618
    assert '618.txt' in res3[0]['target_file']

    # 4. Confessio fidei Felicis (917 seq -> PL101, p. 155)
    res4 = locator.resolve_item(
        'group:90a022719d22a9099fd7d5c43de18ca74962ac63bdac63593fb1a59b9848967b',
        'Confessio fidei missa ad presbyteros et fideles parochiæ Urgellitanæ',
        '917 seq. —'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL101'
    assert res4[0]['physical_page'] == 155
    assert '155.txt' in res4[0]['target_file']

    # 5. Remedius decurio (I, 136 -> PL100, p. 168)
    res5 = locator.resolve_item(
        'a2948ddd06d44ff3d3a08378982ab2b1d4f2c13fa8ef71eaa003d8016ed914e7',
        'Remedius decurio',
        'I, 136'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL100'
    assert res5[0]['physical_page'] == 168
    assert '168.txt' in res5[0]['target_file']


def test_pl101_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL101AlcuinLocator(db_path=DB_PATH)
    res = locator.apply_promotions(dry_run=True)
    assert res['status'] == 'dry_run_complete'
    assert res['dry_run'] is True
    if res['total_evaluated'] > 0:
        assert res['total_promoted'] == 19
    else:
        assert res['total_promoted'] == 0
