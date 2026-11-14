"""
Tests for PO008 John Rufus Plerophories Index Locator (Sprint 26)
"""

import pytest
from tools.indexing.po008_plerophories_locator import PO008PlerophoriesLocator


def test_line_number_appended_resolution():
    locator = PO008PlerophoriesLocator()
    # 1267 -> plain page 126 -> physical page 136
    cands = locator.resolve_item('Grégoire archimandrite à Aphthoria', '1267')
    assert len(cands) > 0
    assert cands[0]['target_volume'] == 'PO008'
    assert cands[0]['plain_page'] == 126
    assert cands[0]['physical_page'] == 136


def test_explicit_page_resolution():
    locator = PO008PlerophoriesLocator()
    # p. 80 -> plain page 80 -> physical page 90
    cands = locator.resolve_item('Flavien, Thrace, μάγστρος à Constantinople vers', '430. p. 80.')
    assert len(cands) > 0
    assert cands[0]['target_volume'] == 'PO008'
    assert cands[0]['plain_page'] == 80
    assert cands[0]['physical_page'] == 90


def test_table_cross_reference_resolution():
    locator = PO008PlerophoriesLocator()
    # Table II -> physical page 196
    cands = locator.resolve_item("Certains se nourrissent d'herbes", "Cf. Les (table II). L'un demeure")
    assert len(cands) > 0
    assert cands[0]['physical_page'] == 196


def test_remissio_resolution():
    locator = PO008PlerophoriesLocator()
    # Monophysites -> physical page 211
    cands = locator.resolve_item('Monophysites', 'voir Natures, Nestorianisme.')
    assert len(cands) > 0
    assert cands[0]['physical_page'] == 211


def test_po008_dry_run_promotion():
    locator = PO008PlerophoriesLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 40
        assert res['promoted'] == 40
        assert res['candidates'] >= 40
    else:
        assert res['promoted'] == 0
