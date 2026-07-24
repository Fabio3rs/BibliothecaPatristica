import pytest

from scripture_ref_normalizer import (
    extract_citations_from_value_cached as _extract,
    keywords_cite_books,
    lookup_book,
    normalize_scripture_book_name,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 Tm 2:19", "2 Timóteo 2,19"),
        ("2 tm 2:19", "2 Timóteo 2,19"),
        ("2tm 2:19", "2 Timóteo 2,19"),
        ("II Cor 5:15", "2 Coríntios 5,15"),
        ("i cor 5:15", "1 Coríntios 5,15"),
        ("1 Cor 5:15", "1 Coríntios 5,15"),
    ],
)
def test_extractor_handles_spaced_roman_and_concatenated_abbrevs(text, expected):
    records = _extract(
        text, source_kind="keywords", source_path="test", support_mode=False
    )
    assert records, f"no records for {text!r}"
    assert records[0]["normalized"] == expected


def test_keywords_cite_books_recognizes_spaced_abbreviations():
    hits = keywords_cite_books(["2 Tm 2:19", "II Cor 5:15", "1 Cor 5:15"])
    # collect canonical names
    names = {h[1] for h in hits}
    assert "2 Timóteo" in names
    assert "2 Coríntios" in names or "2 Coríntios" in names


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Psal.", "Salmos"),
        ("Psaumes", "Salmos"),
        ("Matthieu", "Mateus"),
        ("Luc", "Lucas"),
        ("Jean", "João"),
        ("Actes", "Atos dos Apóstolos"),
        ("Actes des Apotres", "Atos dos Apóstolos"),
        ("Hebreux", "Hebreus"),
        ("I Rois", "1 Samuel"),
        ("III Rois", "1 Reis"),
        ("Épître aux Rom.", "Romanos"),
        ("aux Hébreux.", "Hebreus"),
    ],
)
def test_lookup_book_handles_index_variants_seen_in_alphabetical_indices(label, expected):
    result = lookup_book(label)
    assert result is not None, f"lookup failed for {label!r}"
    assert result[0] == expected


def test_normalize_scripture_book_name_prefers_book_raw_over_legacy_book_norm():
    assert normalize_scripture_book_name("Psaumes", "Psalms") == "Salmos"


def test_normalize_scripture_book_name_uses_legacy_book_norm_as_fallback():
    assert normalize_scripture_book_name(None, "Matthieu") == "Mateus"
