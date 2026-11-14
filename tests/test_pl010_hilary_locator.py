"""
Tests for PL010 Saint Hilary of Poitiers Column Locator (Sprint 22)
"""

import pytest
from tools.indexing.pl010_hilary_locator import PL010HilaryLocator


def test_tome_discrimination():
    locator = PL010HilaryLocator()
    # Tomus I -> PL009
    res1 = locator.resolve_item('Apostolus I', '801')
    assert res1 is not None
    assert res1['target_volume'] == 'PL009'
    assert res1['column'] == 801

    # Standard -> PL010
    res2 = locator.resolve_item('Fides', '265 et seq')
    assert res2 is not None
    assert res2['target_volume'] == 'PL010'
    assert res2['column'] == 265


def test_suffix_handling():
    locator = PL010HilaryLocator()
    res = locator.resolve_item('Doctrina', '520 et seq')
    assert res is not None
    assert res['target_volume'] == 'PL010'
    assert res['column'] == 520


def test_multicolumn_resolution():
    locator = PL010HilaryLocator()
    res = locator.resolve_item('Lex', '338, 495 et seq')
    assert res is not None
    assert res['target_volume'] == 'PL010'
    assert res['column'] == 338


def test_ocr_noise_column_correction():
    locator = PL010HilaryLocator()
    res = locator.resolve_item('Item I', '1635')
    assert res is not None
    assert res['target_volume'] == 'PL009'
    assert res['column'] == 635


def test_pl010_dry_run_all_promoted():
    locator = PL010HilaryLocator()
    res = locator.run(dry_run=True, force_all=False)
    # Post-apply state: all items have been promoted to with_destinations
    assert res['total_items'] == 0
    assert res['promoted'] == 0
