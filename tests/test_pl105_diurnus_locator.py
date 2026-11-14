"""
Tests for PL105 Liber Diurnus Romanorum Pontificum Locator (Sprint 29)
"""

import pytest
from tools.indexing.pl105_diurnus_locator import PL105DiurnusLocator


def test_exact_formula_resolution():
    locator = PL105DiurnusLocator()
    # Formula 6 -> Titulus VIII on page 17
    cands = locator.resolve_item('Episcopum forensem', '6')
    assert len(cands) > 0
    assert cands[0]['target_volume'] == 'PL105'
    assert cands[0]['formula_number'] == 6
    assert cands[0]['physical_page'] == 17


def test_multiformula_resolution():
    locator = PL105DiurnusLocator()
    cands = locator.resolve_item('Eligebant etiam Laici', '16, 17, 23')
    assert len(cands) > 0
    formula_nums = {c['formula_number'] for c in cands}
    assert 16 in formula_nums or 17 in formula_nums or 23 in formula_nums


def test_pl105_dry_run_promotion():
    locator = PL105DiurnusLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 35
        assert res['promoted'] == 35
        assert res['candidates'] >= 35
    else:
        assert res['promoted'] == 0
