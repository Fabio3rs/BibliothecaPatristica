"""
Tests for PL077 Saint Gregory the Great Regestum Epistolarum Locator (Sprint 27)
"""

import pytest
from tools.indexing.pl077_gregory_regestum_locator import PL077GregoryRegestumLocator


def test_multicolumn_resolution():
    locator = PL077GregoryRegestumLocator()
    res = locator.resolve_item('can. 44. Citius', '1129, 1230')
    assert res is not None
    assert res['target_volume'] == 'PL077'
    assert res['column'] == 1129


def test_suffix_stripping_and_location():
    locator = PL077GregoryRegestumLocator()
    # Col 1110 -> physical page 562
    res = locator.resolve_item('Angli ad fidem conversi', '1110 et seqq')
    assert res is not None
    assert res['target_volume'] == 'PL077'
    assert res['column'] == 1110
    assert res['physical_page'] in [561, 562]


def test_ocr_noise_column_correction():
    locator = PL077GregoryRegestumLocator()
    # 1637 -> 637
    res = locator.resolve_item('can. 47. Carendum', '1637')
    assert res is not None
    assert res['target_volume'] == 'PL077'
    assert res['column'] == 637


def test_pl077_dry_run_promotion():
    locator = PL077GregoryRegestumLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 37
        assert res['promoted'] == 37
        assert res['candidates'] == 37
    else:
        assert res['promoted'] == 0
