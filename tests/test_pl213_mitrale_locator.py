"""
tests/test_pl213_mitrale_locator.py

Testes unitários para o localizador do Mitrale de Sicardo de Cremona em PL213.
"""

import pytest
from tools.indexing.pl213_mitrale_locator import PL213MitraleLocator

def test_pl213_locator_init():
    locator = PL213MitraleLocator()
    assert len(locator.col_to_page) > 900
    assert 13 in locator.col_to_page
    assert 83 in locator.col_to_page
    assert 372 in locator.col_to_page

def test_resolve_trailing_column():
    locator = PL213MitraleLocator()
    # Teste: "De vestibus, 83." com ref_raw "VII"
    res = locator.resolve_item("test-1", "De vestibus, 83.", "VII", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 83
    assert res['physical'] > 0
    assert res['score'] >= 45.0

def test_resolve_high_column():
    locator = PL213MitraleLocator()
    # Teste: "De Ascensione Domini, 372." com ref_raw "VIII"
    res = locator.resolve_item("test-2", "De Ascensione Domini, 372.", "VIII", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 372
    assert res['physical'] > 0
