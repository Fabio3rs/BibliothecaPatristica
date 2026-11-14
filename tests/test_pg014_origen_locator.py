"""
tests/test_pg014_origen_locator.py

Testes unitários para o localizador de Orígenes em PG014.
"""

import pytest
from tools.indexing.pg014_origen_locator import PG014OrigenLocator

def test_pg014_locator_init():
    locator = PG014OrigenLocator()
    assert len(locator.col_to_page) > 1000
    assert 7 in locator.col_to_page
    assert 119 in locator.col_to_page
    assert 502 in locator.col_to_page
    assert 641 in locator.col_to_page

def test_resolve_et_seq_column():
    locator = PG014OrigenLocator()
    # Teste: "502 et seq"
    res = locator.resolve_item("test-1", "Injustitia quomodo justitiam Dei commendet", "502 et seq", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 502
    assert res['physical'] > 0
    assert res['score'] >= 45.0

def test_resolve_multi_number_et_seq():
    locator = PG014OrigenLocator()
    # Teste: "479 et seq., 479 et seq"
    res = locator.resolve_item("test-2", "Iræ dies in Scripturis, quid", "479 et seq., 479 et seq", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 479

def test_resolve_passim_with_number_in_label():
    locator = PG014OrigenLocator()
    # Teste: número no label quando ref_raw é "passim"
    label = "Valentinianorum pleroma, 194 et not., 221, 229 et alibi passim."
    res = locator.resolve_item("test-3", label, "passim", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 194
