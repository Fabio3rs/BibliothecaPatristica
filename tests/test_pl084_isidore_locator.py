"""
Tests for PL084 Isidore of Seville Arevalo Tome Locator (Sprint 21)
"""

import pytest
from tools.indexing.pl084_isidore_locator import (
    PL084IsidoreLocator,
    latin_norm
)


def test_latin_norm():
    assert latin_norm('Ægyptiæ') == 'aegyptiae'
    assert latin_norm('Pellis, IV, 14!') == 'pellis iu 14'
    assert latin_norm('Phlegma, phlegmonæ') == 'phlegma phlegmonae'


def test_get_target_volume():
    locator = PL084IsidoreLocator()
    assert locator.get_target_volume('Synonyma Ildefonsi', 'I, 590', '') == 'PL081'
    assert locator.get_target_volume('Pellis', 'IV, 14', '') == 'PL082'
    assert locator.get_target_volume('Phlegma, phlegmonæ, iii', '171 seq', '') == 'PL082'
    assert locator.get_target_volume('Vir homo et mulier v', '72, 92 seqq', '') == 'PL083'
    assert locator.get_target_volume('De principum justitia... vi', '540 seq', '') == 'PL083'
    assert locator.get_target_volume('Reliquiæ', 'VII, 334', '') == 'PL084'


def test_resolve_pellis_and_spiramenta():
    locator = PL084IsidoreLocator()
    res1 = locator.resolve_item('Pellis', 'IV, 14')
    assert res1 is not None
    assert res1['target_volume'] == 'PL082'
    assert res1['physical_page'] in [210, 516]

    res2 = locator.resolve_item('Spiramenta', 'IV, 14')
    assert res2 is not None
    assert res2['target_volume'] == 'PL082'
    assert res2['physical_page'] == 210


def test_resolve_vir_homo():
    locator = PL084IsidoreLocator()
    res = locator.resolve_item('Vir homo et mulier v', '72, 92 seqq')
    assert res is not None
    assert res['target_volume'] == 'PL083'


def test_resolve_ptolemei():
    locator = PL084IsidoreLocator()
    res = locator.resolve_item('Ptolemei plures reges, iii', '236 seqq')
    assert res is not None
    assert res['target_volume'] == 'PL082'


def test_dry_run_all_promoted():
    locator = PL084IsidoreLocator()
    res = locator.run(dry_run=True, force_all=False)
    # Post-apply state: all items have been promoted to with_destinations
    assert res['total_items'] == 0
    assert res['promoted'] == 0
