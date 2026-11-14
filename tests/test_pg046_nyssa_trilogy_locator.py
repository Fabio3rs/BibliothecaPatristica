"""
tests/test_pg046_nyssa_trilogy_locator.py

Testes unitários para o localizador da trilogia de São Gregório de Nissa (PG044/PG045/PG046).
"""

import pytest
from tools.indexing.pg046_nyssa_trilogy_locator import PG046NyssaTrilogyLocator

def test_nyssa_locator_init():
    locator = PG046NyssaTrilogyLocator()
    assert 'PG044' in locator.col_maps
    assert 'PG045' in locator.col_maps
    assert 'PG046' in locator.col_maps
    assert len(locator.col_maps['PG044']) > 1000
    assert len(locator.col_maps['PG045']) > 1000
    assert len(locator.col_maps['PG046']) > 1000

def test_resolve_tomus_i_pg044():
    locator = PG046NyssaTrilogyLocator()
    # "I, 854, 855." -> PG044
    res = locator.resolve_item("test-tomus-i", "Ager quis", "I, 854, 855.", "dummy.txt", {})
    assert res is not None
    assert res['target_volume'] == 'PG044'
    assert res['column'] == 854
    assert res['physical'] > 0
    assert res['score'] >= 45.0

def test_resolve_tomus_ii_pg045():
    locator = PG046NyssaTrilogyLocator()
    # "II, 191; " -> PG045
    res = locator.resolve_item("test-tomus-ii", "Animalia non bibentia", "II, 191, A, B;", "dummy.txt", {})
    assert res is not None
    assert res['target_volume'] == 'PG045'
    assert res['column'] == 191
    assert res['physical'] > 0

def test_resolve_tomus_iii_pg046():
    locator = PG046NyssaTrilogyLocator()
    # "450, C." -> PG046
    res = locator.resolve_item("test-tomus-iii", "Baptizatus recens", "450, C.", "dummy.txt", {})
    assert res is not None
    assert res['target_volume'] == 'PG046'
    assert res['column'] == 450
    assert res['physical'] > 0
