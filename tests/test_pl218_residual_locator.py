"""
Tests for PL218ResidualLocator (Sprint 41)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl218_residual_locator import (
    PL218ResidualLocator,
    PL218_MAP,
    DB_PATH
)


def test_pl218_mapping_completeness():
    assert len(PL218_MAP) == 18
    locator = PL218ResidualLocator()

    for item_id, (target_vol, p_num, cap_or_col, reason) in PL218_MAP.items():
        assert target_vol.startswith('PL')
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl218_resolution():
    locator = PL218ResidualLocator()

    # 1. Aimericus (CII, 1403 -> CCI, 1403 -> PL201, p. 730)
    res1 = locator.resolve_item(
        'region-entry:c792d16bcf2a4d139eb93d54c0a38e4b39c43296432e20a014e9b25ca04152cc',
        'Aimericus patriarch. Antioch.',
        'CII, 1403.'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL201'
    assert res1[0]['physical_page'] == 730
    assert '730.txt' in res1[0]['target_file']

    # 2. Goslenus (CLXXVI, 1399 -> CLXXXVI, 1399 -> PL186, p. 704)
    res2 = locator.resolve_item(
        'region-entry:81bd094bd3fd23c5b07ac5a1c6a80f2e0a3bafd8d3c5b41aa56551f704483342',
        'Goslenus episc. Carnot.',
        'CLXXVI, 1399.'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL186'
    assert res2[0]['physical_page'] == 704
    assert '704.txt' in res2[0]['target_file']

    # 3. Lucas Tudensis (CCLX, 9 -> CCVIII, 9 -> PL208, p. 11)
    res3 = locator.resolve_item(
        'region-entry:83a4defc062d91db315ecf5d6bb49e3b8965c0e1f53a204b98cb39e1b80e45b3',
        'Lucas diacon. Legionens., postmodum episc. Tudensis',
        'CCLX, 9.'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL208'
    assert res3[0]['physical_page'] == 11
    assert '011.txt' in res3[0]['target_file'] or '11.txt' in res3[0]['target_file']

    # 4. Petrus Bibliothecarius (CII, 1279 -> CLI, 1279 -> PL151, p. 642)
    res4 = locator.resolve_item(
        'region-entry:8f60555333a00f6cc63818433a9b249a1398be1ea9eb6683e4f564395efec319',
        'Petrus Bibliothecarius',
        'CII, 1279.'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL151'
    assert res4[0]['physical_page'] == 642
    assert '642.txt' in res4[0]['target_file']

    # 5. Benedictus Levita (1769 -> XCVII, col 769 -> PL097, p. 389)
    res5 = locator.resolve_item(
        '8eebbc842122f1b4a953b046a676897367a721f0425b249df6a5722a24b74b9b',
        'Benedicti Capitularium collectione',
        '1769'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL097'
    assert res5[0]['physical_page'] == 389
    assert '389.txt' in res5[0]['target_file']


def test_pl218_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL218ResidualLocator(db_path=DB_PATH)
    res = locator.apply_promotions(dry_run=True)
    assert res['status'] == 'dry_run_complete'
    assert res['dry_run'] is True
    if res['total_evaluated'] > 0:
        assert res['total_promoted'] == 18
    else:
        assert res['total_promoted'] == 0
