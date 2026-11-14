"""
Tests for PL076 Saint Gregory the Great Homilies & Moralia Locator (Sprint 28)
"""

import pytest
from tools.indexing.pl076_gregory_homilies_locator import PL076GregoryHomiliesLocator


def test_benedictine_inline_number_resolution():
    locator = PL076GregoryHomiliesLocator()
    # 1638 is inline on physical page 651
    res = locator.resolve_item('Rosa, quae redolet, crescit cum spina quae pungit', '1638')
    assert res is not None
    assert res['target_volume'] == 'PL076'
    assert res['physical_page'] == 651


def test_migne_column_resolution():
    locator = PL076GregoryHomiliesLocator()
    res = locator.resolve_item('Ros de super veniens', '632')
    assert res is not None
    assert res['target_volume'] == 'PL076'
    assert res['column'] == 632


def test_lexical_reanchor_sadoc():
    locator = PL076GregoryHomiliesLocator()
    # Sacrilegia latent dicitur justus -> Sadoch Latine dicitur justus on page 541
    res = locator.resolve_item('Sacrilegia latent dicitur justus', '1924')
    assert res is not None
    assert res['target_volume'] == 'PL076'
    assert res['physical_page'] == 541


def test_pl076_dry_run_promotion():
    locator = PL076GregoryHomiliesLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 31
        assert res['promoted'] == 31
        assert res['candidates'] == 31
    else:
        assert res['promoted'] == 0
