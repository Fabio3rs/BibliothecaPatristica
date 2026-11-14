"""
Tests for PG122 Georgius Cedrenus Internal Number Locator (Sprint 23)
"""

import pytest
from tools.indexing.pg122_cedrenus_locator import PG122CedrenusLocator


def test_tome_1_resolution():
    locator = PG122CedrenusLocator()
    cands = locator.resolve_item('Balbini Maximi imperium', 'I, 450.')
    assert len(cands) > 0
    assert any(c['volume'] == 'PG121' for c in cands)


def test_tome_2_resolution():
    locator = PG122CedrenusLocator()
    cands = locator.resolve_item('Dandulphus Longobardiæ rex', 'II, 355.')
    assert len(cands) > 0
    assert any(c['volume'] == 'PG122' for c in cands)


def test_embedded_newline_resolution():
    locator = PG122CedrenusLocator()
    cands = locator.resolve_item('Bethschemesani cives idololatræ', 'I,\n109.')
    assert len(cands) > 0
    assert any(c['volume'] == 'PG121' for c in cands)


def test_multilocator_resolution():
    locator = PG122CedrenusLocator()
    cands = locator.resolve_item('Diabasis campus', 'I, 885; II, 285.')
    volumes = {c['volume'] for c in cands}
    assert 'PG121' in volumes
    assert 'PG122' in volumes


def test_ibid_antecedent_resolution():
    locator = PG122CedrenusLocator()
    cands = locator.resolve_item('In cœlum raptus', 'ibid., 182')
    assert len(cands) > 0
    assert any(c['volume'] == 'PG121' and c['physical'] == 113 for c in cands)


def test_pg122_dry_run_promotion():
    locator = PG122CedrenusLocator()
    res = locator.run(dry_run=True, force_all=False)
    # Before apply, all 41 residual items are promoted
    if res['total_items'] > 0:
        assert res['total_items'] == 41
        assert res['promoted'] == 41
        assert res['candidates'] >= 41
    else:
        # After apply
        assert res['promoted'] == 0
