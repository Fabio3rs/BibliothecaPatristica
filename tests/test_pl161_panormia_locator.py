"""
Unit tests for tools.indexing.pl161_panormia_locator
"""

import pytest
from tools.indexing.pl161_panormia_locator import (
    roman_to_int,
    parse_panormia_reference,
    build_panormia_chapter_map,
    resolve_pl161_catalog
)


def test_roman_to_int():
    assert roman_to_int('I') == 1
    assert roman_to_int('IV') == 4
    assert roman_to_int('VIII') == 8
    assert roman_to_int('CLIII') == 153
    assert roman_to_int('CLIV') == 154


def test_parse_panormia_reference_explicit():
    b, caps, is_ibid = parse_panormia_reference("Episcopus aut presbyter religionis causa, cap. 113, lib. iii")
    assert b == 3
    assert caps == [113]
    assert not is_ibid


def test_parse_panormia_reference_multiple_chapters():
    b, caps, is_ibid = parse_panormia_reference("Spoliato ante litem contestatam, omnia sunt restituenda, capp. 47, 48 et 49, lib. iv")
    assert b == 4
    assert caps == [47, 48, 49]
    assert not is_ibid


def test_parse_panormia_reference_cap_ultimo():
    b, caps, is_ibid = parse_panormia_reference("Incendii pestis, et vastitas hostilis, cap. ultimo, lib. viii")
    assert b == 8
    assert caps == [154]
    assert not is_ibid


def test_parse_panormia_reference_ibid():
    b, caps, is_ibid = parse_panormia_reference("Scriptura divina omnis rei aufert superabundantiam, ibidem.")
    assert is_ibid


def test_build_chapter_map():
    ch_map, file_map = build_panormia_chapter_map('teste/PL161/text')
    assert (1, 1) in ch_map
    assert (8, 154) in ch_map
    # Book 1 Cap 1 must point to a file with page >= 573
    assert '573' in ch_map[(1, 1)]


def test_pl161_catalog_dry_run():
    res = resolve_pl161_catalog(dry_run=True)
    assert res['total_items'] == 229
    assert res['resolved'] == 229
    assert res['unresolved'] == 0
    assert res['candidates_generated'] >= 229
