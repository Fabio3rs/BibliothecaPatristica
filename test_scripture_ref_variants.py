import pytest

from scripture_ref_normalizer import (
    extract_citations_from_value_cached as _extract,
    keywords_cite_books,
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
