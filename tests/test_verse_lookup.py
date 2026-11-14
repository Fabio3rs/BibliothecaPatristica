"""Tests for tools.scripture.verse_lookup."""

from pathlib import Path
import pytest

from tools.scripture.verse_lookup import (
    CanonicalBibleLookup,
    parse_reference,
    parse_roman,
    resolve_book_name,
)


def test_resolve_book_name():
    assert resolve_book_name("Gen") == "Genesis"
    assert resolve_book_name("Gn") == "Genesis"
    assert resolve_book_name("Ps") == "Psalms"
    assert resolve_book_name("Psalmi") == "Psalms"
    assert resolve_book_name("Psaumes") == "Psalms"
    assert resolve_book_name("Mt") == "Matthew"
    assert resolve_book_name("Matth") == "Matthew"
    assert resolve_book_name("Matthieu") == "Matthew"
    assert resolve_book_name("I Cor") == "I Corinthians"
    assert resolve_book_name("1 Cor") == "I Corinthians"
    assert resolve_book_name("Apoc") == "Revelation of John"
    assert resolve_book_name("Sirach") == "Sirach"
    assert resolve_book_name("Eccli") == "Sirach"
    assert resolve_book_name("Sap") == "Wisdom"


def test_parse_roman():
    assert parse_roman("I") == 1
    assert parse_roman("IV") == 4
    assert parse_roman("V") == 5
    assert parse_roman("IX") == 9
    assert parse_roman("XIV") == 14
    assert parse_roman("XXII") == 22
    assert parse_roman("CII") == 102
    assert parse_roman("invalid") is None


def test_parse_reference():
    # Standard format
    assert parse_reference("Gen 1:1") == ("Genesis", 1, 1, 1)
    # Range format
    assert parse_reference("Ps 22:1-5") == ("Psalms", 22, 1, 5)
    # Roman numeral chapter and comma verse
    assert parse_reference("Matth. V 9") == ("Matthew", 5, 9, 9)
    # Whole chapter
    assert parse_reference("Rom 8") == ("Romans", 8, None, None)
    # French abbreviation with hyphen
    assert parse_reference("I Cor. 13:4-8") == ("I Corinthians", 13, 4, 8)


def test_verse_lookup_vulg_clementine():
    engine = CanonicalBibleLookup()
    try:
        # Ps 22:1
        res = engine.lookup("Psalms", 22, 1, 1, version="VulgClementine")
        assert len(res) == 1
        assert "Dominus regit me" in res[0]["text"]

        # Gen 1:1-3
        res_range = engine.lookup("Genesis", 1, 1, 3, version="VulgClementine")
        assert len(res_range) == 3
        assert res_range[0]["verse"] == 1
        assert res_range[1]["verse"] == 2
        assert res_range[2]["verse"] == 3
    finally:
        engine.close()


def test_verse_compare_multi_version():
    engine = CanonicalBibleLookup()
    try:
        comp = engine.compare("Matth 5:9", versions=["VulgClementine", "StatResGNT", "FreCrampon"])
        assert "parsed" in comp
        assert comp["parsed"]["book"] == "Matthew"
        assert comp["parsed"]["chapter"] == 5
        assert comp["parsed"]["start_verse"] == 9

        vers = comp["versions"]
        assert "VulgClementine" in vers
        assert "pacifici" in vers["VulgClementine"][0]["text"].lower()
        assert "StatResGNT" in vers
        assert "εἰρηνοποιοί" in vers["StatResGNT"][0]["text"]
        assert "FreCrampon" in vers
        assert "pacifiques" in vers["FreCrampon"][0]["text"].lower()
    finally:
        engine.close()


def test_verse_search():
    engine = CanonicalBibleLookup()
    try:
        results = engine.search("In principio creavit", version="VulgClementine", limit=5)
        assert len(results) >= 1
        assert results[0]["book"] == "Genesis"
        assert results[0]["chapter"] == 1
        assert results[0]["verse"] == 1
    finally:
        engine.close()
