"""
tests/test_pg095_sacra_parallela_locator.py

Testes unitários para o localizador da Sacra Parallela de São João Damasceno (PG095 / PG096).
"""

import pytest
from tools.indexing.pg095_sacra_parallela_locator import (
    roman_to_int,
    greek_to_int,
    parse_num,
    LETTER_NORM,
    normalize_text,
    PG095SacraParallelaLocator
)

def test_numeral_parsing():
    assert roman_to_int('I') == 1
    assert roman_to_int('IV') == 4
    assert roman_to_int('XII') == 12
    assert roman_to_int('XVIII') == 18
    assert roman_to_int('XXV') == 25
    assert roman_to_int('XXX') == 30

    assert greek_to_int("Α'") == 1
    assert greek_to_int("Δ'") == 4
    assert greek_to_int("Ϛ'") == 6
    assert greek_to_int("ΙΒ'") == 12
    assert greek_to_int("ΙΗ'") == 18
    assert greek_to_int("ΚΕ'") == 25
    assert greek_to_int("ΚΘ'") == 29
    assert greek_to_int("Λ'") == 30

    assert parse_num("12") == 12
    assert parse_num("XII.") == 12
    assert parse_num("ΙΒ'.") == 12

def test_letter_normalization():
    assert LETTER_NORM['α'] == 'α'
    assert LETTER_NORM['alpha'] == 'α'
    assert LETTER_NORM['a'] == 'α'
    assert LETTER_NORM['ii'] == 'π'
    assert LETTER_NORM['pi'] == 'π'
    assert LETTER_NORM['x'] == 'ξ'
    assert LETTER_NORM['z'] == 'ζ'
    assert LETTER_NORM['zeta'] == 'ζ'
    assert LETTER_NORM['omega'] == 'ω'

def test_normalize_text():
    raw = "Περὶ ἀγάπης, καὶ εἰρήνης! De charitate..."
    norm = normalize_text(raw)
    assert "ἀγάπης" in norm
    assert "charitate" in norm
    assert "," not in norm

def test_locator_initialization():
    locator = PG095SacraParallelaLocator()
    assert len(locator.all_targets) > 200
    assert len(locator.targets_by_letter) >= 20

def test_resolve_exact_item():
    locator = PG095SacraParallelaLocator()
    # Test item: De charitate et pace (Alpha, Tit. XVIII) -> PG095
    item_id = "test-alpha-18"
    label = "De charitate et pace, deque pacificis et mansuetis hominibus."
    ref_raw = "XVIII"
    src = "teste/PG095/text/dfe8700f-3f83-49c9-b3d2-fa42db18e91f-545.txt"
    meta = {
        "internal_binding": {
            "letter": {"key": "α"},
            "numbers": [18]
        }
    }
    res = locator.resolve_item(item_id, label, ref_raw, src, meta)
    assert res is not None
    assert res['target_volume'] == 'PG095'
    assert res['letter'] == 'α'
    assert res['number'] == 18
    assert res['score'] >= 40.0

def test_resolve_pg096_sister_destination():
    locator = PG095SacraParallelaLocator()
    # Test item: De divina institutione (Theta, Tit. I) -> PG096
    item_id = "test-theta-1"
    label = "De divina initiatione."
    ref_raw = "I"
    src = "teste/PG095/text/dfe8700f-3f83-49c9-b3d2-fa42db18e91f-550.txt"
    meta = {
        "internal_binding": {
            "letter": {"key": "θ"},
            "numbers": [1]
        }
    }
    res = locator.resolve_item(item_id, label, ref_raw, src, meta)
    assert res is not None
    assert res['target_volume'] == 'PG096'
    assert res['letter'] == 'θ'
    assert res['number'] == 1
    assert res['physical'] == 13

def test_resolve_deconcatenated_sibling():
    locator = PG095SacraParallelaLocator()
    # Test item: deconcatenated tail without explicit letter in meta
    item_id = "test-deconcatenated"
    label = "ab iis qui periti sunt"
    ref_raw = "XI"
    src = "teste/PG095/text/dfe8700f-3f83-49c9-b3d2-fa42db18e91f-551.txt"
    meta = {"internal_binding": {}}
    res = locator.resolve_item(item_id, label, ref_raw, src, meta)
    assert res is not None
    assert res['target_volume'] == 'PG096'
    assert res['letter'] == 'μ'
    assert res['number'] == 11
