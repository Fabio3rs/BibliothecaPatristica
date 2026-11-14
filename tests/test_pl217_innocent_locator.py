"""
Tests for PL217 Innocent III Register Locator (Sprint 20)
"""

import pytest
from tools.indexing.pl217_innocent_locator import (
    PL217InnocentLocator,
    latin_norm,
    roman_to_int
)


def test_roman_to_int():
    assert roman_to_int('I') == 1
    assert roman_to_int('IV') == 4
    assert roman_to_int('VII') == 7
    assert roman_to_int('IX') == 9
    assert roman_to_int('X') == 10
    assert roman_to_int('XV') == 15
    assert roman_to_int('XVI') == 16
    assert roman_to_int('CCXL') == 240
    assert roman_to_int('') is None
    assert roman_to_int('INVALID') is None


def test_latin_norm():
    assert latin_norm('Ad honōrem Dēi.') == 'ad honorem dei'
    assert latin_norm('Æternus jura vobis!') == 'aeternus iura uobis'
    assert latin_norm('Sæpe   contingit...') == 'saepe contingit'


def test_resolve_imperial_register():
    locator = PL217InnocentLocator()
    # Test CLI (151) -> p. 579
    res = locator.resolve_item('Affectum dilectionis.', 'Registr. de neg. imp. 151.')
    assert res is not None
    assert res['target_volume'] == 'PL216'
    assert res['physical_page'] == 579
    assert res['series'] == 'imperial_register'

    # Test LXXI (71) -> p. 545
    res = locator.resolve_item('Quantum venerabili.', 'Registr. de neg. imp. 71.')
    assert res is not None
    assert res['target_volume'] == 'PL216'
    assert res['physical_page'] == 545

    # Test CXCIV (194) -> p. 592
    res = locator.resolve_item('Si commode posset.', 'Registr. de neg. imp. 194.')
    assert res is not None
    assert res['target_volume'] == 'PL216'
    assert res['physical_page'] == 592


def test_resolve_supplement():
    locator = PL217InnocentLocator()
    # Test Suppl. 73 -> p. 58
    res = locator.resolve_item('Lacrymabilis venerabilis.', 'Suppl. 73.')
    assert res is not None
    assert res['target_volume'] == 'PL217'
    assert res['physical_page'] == 58
    assert res['series'] == 'supplement'

    # Test Suppl. 255 -> p. 143
    res = locator.resolve_item('Tam suppliciter quam.', 'Suppl. 255.')
    assert res is not None
    assert res['target_volume'] == 'PL217'
    assert res['physical_page'] == 143


def test_resolve_register_books():
    locator = PL217InnocentLocator()
    # Book VII Ep. 10 -> PL215 p. 155 or 782
    res = locator.resolve_item('Ad honorem Dei.', 'VII, 10.')
    assert res is not None
    assert res['target_volume'] == 'PL215'

    # Book XV Ep. 256 -> PL216 p. 396
    res = locator.resolve_item('Ea quæ illicite', 'XV, 256.')
    assert res is not None
    assert res['target_volume'] == 'PL216'
    assert res['physical_page'] == 396

    # Book XV Ep. 157 -> PL216 p. 347
    res = locator.resolve_item('His quae ad ecclesiarum', 'XV, 157.')
    assert res is not None
    assert res['target_volume'] == 'PL216'
    assert res['physical_page'] == 347


def test_dry_run_all_promoted():
    locator = PL217InnocentLocator()
    res = locator.run(dry_run=True, force_all=False)
    # Post-apply state: all items have been promoted to with_destinations
    assert res['total_items'] == 0
    assert res['promoted'] == 0
