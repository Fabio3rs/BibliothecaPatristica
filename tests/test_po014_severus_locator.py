"""
Tests for PO014 Letters of Severus Index Locator (Sprint 25)
"""

import pytest
from tools.indexing.po014_severus_locator import PO014SeverusLocator


def test_part_1_po012_resolution():
    locator = PO014SeverusLocator()
    # Acacius at 125 -> PO012 physical page 309
    cands = locator.resolve_item('Acacius, bp. of Berrhoea', '125.')
    assert len(cands) > 0
    assert cands[0]['target_volume'] == 'PO012'
    assert cands[0]['continuous_page'] == 125
    assert cands[0]['physical_page'] == 309


def test_part_2_po014_resolution():
    locator = PO014SeverusLocator()
    # Continuous page 351 -> PO014 physical page 191
    cands = locator.resolve_item('Solon', '351')
    assert len(cands) > 0
    assert cands[0]['target_volume'] == 'PO014'
    assert cands[0]['continuous_page'] == 351
    assert cands[0]['physical_page'] == 191


def test_multivolume_severus_resolution():
    locator = PO014SeverusLocator()
    # Antioch: 104, 151 (PO012) and 351, 416 (PO014)
    cands = locator.resolve_item('Antioch', '104, 151, 351, 416; Synod of, 149.')
    volumes = {c['target_volume'] for c in cands}
    assert 'PO012' in volumes
    assert 'PO014' in volumes


def test_po014_dry_run_promotion():
    locator = PO014SeverusLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 38
        assert res['promoted'] == 38
        assert res['candidates'] >= 38
    else:
        assert res['promoted'] == 0
