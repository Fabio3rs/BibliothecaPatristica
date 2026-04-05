import pytest

from keywords_serial import _clean_list
from keywords_serial import looks_like_reference_keyword
from keywords_serial import normalize_keywords_payload, clean_keywords_structure
from keywords_serial import validate_keywords
from scripture_ref_normalizer import _extract_citations_from_value


def test_not_split_multiword_name():
    cleaned, issues = _clean_list(["Atanasio de Alexandria"])
    assert cleaned == ["Atanasio de Alexandria"]
    assert "split_conjoined_keywords" not in issues


def test_emphasized_pair_is_not_split_now():
    cleaned, issues = _clean_list(["**palavra 1** e **palavra 2**"])
    assert cleaned == ["palavra 1 e palavra 2"]
    assert "split_conjoined_keywords" not in issues


def test_commas_semicolons_list_not_split():
    cleaned, issues = _clean_list(["kw1, kw2; kw3"])
    assert cleaned == ["kw1, kw2; kw3"]
    assert "split_conjoined_keywords" not in issues


def test_strip_markdown_and_quotes():
    cleaned, issues = _clean_list(["> **\"Termo\"**"])
    assert cleaned == ["Termo"]
    assert issues == []


def test_keywords_with_numbers_trigger_issue():
    cleaned = {"keywords": ["4", "Ap 3", "Canon 35"]}
    issues = validate_keywords(cleaned, parse_issue=None)
    assert "keyword_isolated_number" in issues
    assert "few_keywords(3)" in issues


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ps. CXII, 13", False),
        ("ps. l, 7", False),
        ("Lucas 24,39", False),
        ("Lucas 24:39", False),
        ("Gn 3:22", False),
        ("Gn 3:22-24", False),
        ("Mt 5:3", False),
        ("2 Tm 2:19", False),
        ("Cor 5:15", False),
        ("fol. 55, r a", True),
        ("xxvii, 9", True),
        ("Ap 3", True),
    ],
)
def test_looks_like_reference_keyword_ignores_bible_citations(text, expected):
    assert looks_like_reference_keyword(text) is expected


@pytest.mark.parametrize(
    ("text", "expected_normalized"),
    [
        ("Gn 3:22", "Gênesis 3,22"),
        ("Gn 3,22", "Gênesis 3,22"),
        ("Gn 3:22-24", "Gênesis 3,22-24"),
        ("Gen 3:22", "Gênesis 3,22"),
        ("Mt 5:3", "Mateus 5,3"),
        ("Ap 21:2-4", "Apocalipse 21,2-4"),
        ("2 Tm 2:19", "2 Timóteo 2,19"),
        ("2 Tm 2,19", "2 Timóteo 2,19"),
        ("1 Cor 5:15", "1 Coríntios 5,15"),
        ("II Cor 5:15", "2 Coríntios 5,15"),
        ("Ps. CXII, 13", "Salmos 112,13"),
        ("Lucas 24:39", "Lucas 24,39"),
    ],
)
def test_extract_citations_supports_conservative_book_abbreviations(
    text, expected_normalized
):
    records = _extract_citations_from_value(
        text,
        source_kind="keywords",
        source_path="test",
        support_mode=False,
    )

    assert records
    assert records[0]["normalized"] == expected_normalized


@pytest.mark.parametrize(["text"], [("Gn 3",), ("cf. Gn 3:22",)])
def test_extract_citations_keeps_conservative_abbreviations_out_of_scope(text):
    records = _extract_citations_from_value(
        text,
        source_kind="keywords",
        source_path="test",
        support_mode=False,
    )

    assert records == []


def test_normalize_keywords_payload_strips_markdown():
    payload = '{"keywords": ["**santidade**", "`Cristo`", "*porta da justiça*"]}'
    parsed, issue = normalize_keywords_payload(payload)
    cleaned, issues = clean_keywords_structure(parsed)
    assert issue is None
    assert cleaned["keywords"] == ["santidade", "Cristo", "porta da justiça"]
    assert not issues


def test_clean_keywords_structure_normalizes_bible_citations_from_anchor():
    parsed = {
        "keywords": ["Lucas 24:39", "Lucas 24,39"],
        "categorias": {"obras_citadas": ["Evangelho segundo Lucas"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Lucas 24,39"]
    assert cleaned["categorias"]["obras_citadas"] == ["Lucas"]
    assert "normalized_bible_citation" in issues
    assert "removed_duplicate" in issues


def test_clean_keywords_structure_keeps_keyword_without_obras_anchor():
    parsed = {
        "keywords": ["Lucas 24:39"],
        "categorias": {"temas": ["exegese"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Lucas 24:39"]
    assert cleaned["categorias"]["temas"] == ["exegese"]
    assert "normalized_bible_citation" not in issues


def test_clean_keywords_structure_normalizes_conservative_abbreviation_with_anchor():
    parsed = {
        "keywords": ["Gn 3:22", "Gênesis 3,22"],
        "categorias": {"obras_citadas": ["Gn 3:22"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Gênesis 3,22"]
    assert cleaned["categorias"]["obras_citadas"] == ["Gênesis 3,22"]
    assert "normalized_bible_citation" in issues
    assert "removed_duplicate" in issues


def test_clean_keywords_structure_normalizes_spaced_ordinal_abbreviation_with_obras_alias():
    parsed = {
        "keywords": ["2 Tm 2:19", "2 Timóteo 2,19"],
        "categorias": {"Obras": ["2 Tm 2:19"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["2 Timóteo 2,19"]
    assert cleaned["categorias"]["Obras"] == ["2 Timóteo 2,19"]
    assert "normalized_bible_citation" in issues
    assert "removed_duplicate" in issues


def test_clean_keywords_structure_normalizes_ambiguous_abbreviation_punctuation_only():
    parsed = {
        "keywords": ["Cor 5:15"],
        "categorias": {"Obras": ["Cor 5:15"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Cor 5,15"]
    assert cleaned["categorias"]["Obras"] == ["Cor 5,15"]
    assert "normalized_bible_citation" in issues


def test_clean_keywords_structure_normalizes_abbreviation_in_obras_only():
    parsed = {
        "keywords": ["Cristologia"],
        "categorias": {"obras_citadas": ["Ap 21:2-4"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Cristologia"]
    assert cleaned["categorias"]["obras_citadas"] == ["Apocalipse 21,2-4"]
    assert "normalized_bible_citation" in issues


def test_clean_keywords_structure_does_not_normalize_embedded_abbreviation_text():
    parsed = {
        "keywords": ["Comentário sobre Gn 3:22"],
        "categorias": {"obras_citadas": ["Gênesis 3,22"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Comentário sobre Gn 3:22"]
    assert cleaned["categorias"]["obras_citadas"] == ["Gênesis 3,22"]
    assert "normalized_bible_citation" not in issues


def test_clean_keywords_structure_keeps_abbreviation_chapter_without_verse():
    parsed = {
        "keywords": ["Gn 3"],
        "categorias": {"obras_citadas": ["Gênesis"]},
    }

    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == ["Gn 3"]
    assert cleaned["categorias"]["obras_citadas"] == ["Gênesis"]
    assert "normalized_bible_citation" not in issues


@pytest.mark.parametrize(
    ("parsed", "expected_keywords", "expected_obras", "expected_issues"),
    [
        (
            {
                "keywords": ["Ps. CXII, 13", "Salmos 112,13"],
                "categorias": {"obras_citadas": ["Livro dos Salmos"]},
            },
            ["Salmos 112,13"],
            ["Salmos"],
            {"normalized_bible_citation", "removed_duplicate"},
        ),
        (
            {
                "keywords": ["Isaías 1:14", "Isaías 1,14"],
                "categorias": {"obras_citadas": ["Isaías 1:14"]},
            },
            ["Isaías 1,14"],
            ["Isaías 1,14"],
            {"normalized_bible_citation", "removed_duplicate"},
        ),
        (
            {
                "keywords": ["Salmos 112:7", "Salmos 112,7"],
                "categorias": {"obras_citadas": ["Salmos 112:7"]},
            },
            ["Salmos 112,7"],
            ["Salmos 112,7"],
            {"normalized_bible_citation", "removed_duplicate"},
        ),
        (
            {
                "keywords": ["João 14"],
                "categorias": {"obras_citadas": ["Evangelho de João (capítulo 14"]},
            },
            ["João 14"],
            ["João 14"],
            {"normalized_bible_citation"},
        ),
        (
            {
                "keywords": ["Deuteronômio 6:5", "Deuteronômio 6,5"],
                "categorias": {
                    "obras_citadas": [
                        "Livro do Deuteronômio, capítulo 6, versículo 5"
                    ]
                },
            },
            ["Deuteronômio 6,5"],
            ["Deuteronômio 6,5"],
            {"normalized_bible_citation", "removed_duplicate"},
        ),
    ],
)
def test_clean_keywords_structure_real_bible_citation_samples(
    parsed, expected_keywords, expected_obras, expected_issues
):
    cleaned, issues = clean_keywords_structure(parsed)

    assert cleaned["keywords"] == expected_keywords
    assert cleaned["categorias"]["obras_citadas"] == expected_obras
    assert expected_issues.issubset(set(issues))
