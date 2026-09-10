from __future__ import annotations

import pytest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.scripture_keyword_parser_v2 import parse_scripture_keyword


@pytest.mark.parametrize(
    ("raw", "book_key", "segments"),
    [
        ("João 3,16", "joao", ((3, 16, 3, 16),)),
        ("Ioan. III, 16", "joao", ((3, 16, 3, 16),)),
        ("II Cor 5:15", "2 corintios", ((5, 15, 5, 15),)),
        ("João 7,53-8,11", "joao", ((7, 53, 8, 11),)),
        ("Atos 6,8-7,60", "atos", ((6, 8, 7, 60),)),
        ("Gênesis 2,25-3,24", "genesis", ((2, 25, 3, 24),)),
    ],
)
def test_parses_simple_and_cross_chapter_references(raw, book_key, segments):
    result = parse_scripture_keyword(raw)

    assert not result.issues
    assert len(result.references) == 1
    reference = result.references[0]
    assert reference.book_key == book_key
    assert tuple(
        (
            segment.start_chapter,
            segment.start_verse,
            segment.end_chapter,
            segment.end_verse,
        )
        for segment in reference.segments
    ) == segments


@pytest.mark.parametrize(
    "raw",
    [
        "Jean 3,16",
        "John 3:16",
        "Giovanni 3,16",
        "Ioannes III,16",
    ],
)
def test_recognizes_deterministic_french_english_italian_and_latin_book_names(raw):
    reference = parse_scripture_keyword(raw).references[0]

    assert reference.book_key == "joao"
    assert reference.normalized == "São João 3:16"


@pytest.mark.parametrize(
    "raw",
    [
        "1 Timothée 6,13",
        "1 Timothy 6:13",
        "1 Timoteo 6,13",
        "I Timotheum VI,13",
    ],
)
def test_recognizes_numbered_book_names_across_supported_languages(raw):
    reference = parse_scripture_keyword(raw).references[0]

    assert reference.book_key == "1 timoteo"
    assert reference.normalized == "1 Timóteo 6:13"


def test_parses_discontinuous_same_book_list_as_one_reference():
    result = parse_scripture_keyword("João 3,14-16, 18; 4,1-2")

    assert not result.issues
    assert len(result.references) == 1
    reference = result.references[0]
    assert reference.granularity == "list"
    assert reference.normalized == "São João 3:14-16, 18; 4:1-2"
    assert tuple(segment.canonical_token() for segment in reference.segments) == (
        "3:14-3:16",
        "3:18",
        "4:1-4:2",
    )


def test_parses_repeated_chapter_numbers_in_a_discontinuous_list():
    result = parse_scripture_keyword("Carta aos Efésios 4,31, 4,32 e 5,1")

    assert not result.issues
    assert result.references[0].normalized == "Efésios 4:31, 32; 5:1"
    assert tuple(segment.canonical_token() for segment in result.references[0].segments) == (
        "4:31",
        "4:32",
        "5:1",
    )


def test_parses_period_separated_verse_ranges_without_truncating_the_second_range():
    result = parse_scripture_keyword("Zacarias (Lucas 1,5-25.57-80)")

    assert not result.issues
    assert result.references[0].normalized == "São Lucas 1:5-25, 57-80"
    assert tuple(segment.canonical_token() for segment in result.references[0].segments) == (
        "1:5-1:25",
        "1:57-1:80",
    )


def test_keeps_plain_comma_separated_verses_in_the_current_chapter():
    reference = parse_scripture_keyword("João 3,16,18,20").references[0]

    assert reference.normalized == "São João 3:16, 18, 20"


def test_does_not_misread_a_verse_list_as_a_cross_chapter_range():
    reference = parse_scripture_keyword(
        "Deuteronômio 12,13-14,17-18,26; 14,22-25"
    ).references[0]

    assert reference.normalized == "Deuteronômio 12:13-14, 17-18, 26; 14:22-25"


def test_parses_multi_book_thematic_keyword_without_merging_books():
    raw = (
        "Pôncio Pilatos (Mateus 27,2; Lucas 3,1; "
        "Atos dos Apóstolos 4,27; 1 Timóteo 6,13)"
    )
    result = parse_scripture_keyword(raw)

    assert not result.issues
    assert [reference.book_key for reference in result.references] == [
        "mateus",
        "lucas",
        "atos",
        "1 timoteo",
    ]
    assert {reference.source_kind for reference in result.references} == {"embedded"}


def test_marks_a_pure_multi_book_expression_as_standalone():
    result = parse_scripture_keyword("João 3,16; Romanos 8,1")

    assert len(result.references) == 2
    assert {reference.source_kind for reference in result.references} == {"standalone"}


def test_preserves_alternate_psalm_number_and_open_end_flags():
    psalm = parse_scripture_keyword("Salmo 110 (109)").references[0]
    open_end = parse_scripture_keyword("João 3,16ss.").references[0]

    assert psalm.alternate_chapter == 109
    assert psalm.flags == ("alternate_numbering",)
    assert open_end.flags == ("open_end",)


@pytest.mark.parametrize("raw", ["João VI Cantacuzeno", "João VI (papa)", "João VI"])
def test_quarantines_ambiguous_joao_person_ordinals(raw):
    result = parse_scripture_keyword(raw)

    assert not result.references
    assert [issue.code for issue in result.issues] == ["ambiguous_person_ordinal"]


def test_quarantines_ambiguous_joao_person_ordinal_range():
    for raw in ("Papas João I-XIX", "Papas (João IX, X e XII)"):
        result = parse_scripture_keyword(raw)

        assert not result.references
        assert [issue.code for issue in result.issues] == ["ambiguous_person_ordinal"]


def test_accepts_roman_chapter_with_a_scriptural_cue():
    result = parse_scripture_keyword("Evangelho de João VI")

    assert len(result.references) == 1
    assert result.references[0].segments[0].start_chapter == 6


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("PSALMUS XLII.", "Salmos 42"),
        ("Psal. XLII.", "Salmos 42"),
        ("Ps. XLII.", "Salmos 42"),
        ("Psalm. XLII. 3", "Salmos 42:3"),
    ],
)
def test_distinguishes_terminal_dots_from_abbreviated_chapter_verse_separators(
    raw, normalized
):
    result = parse_scripture_keyword(raw, collection="PL")

    assert not result.issues
    assert result.references[0].normalized == normalized


def test_rejects_zero_verse_as_malformed():
    result = parse_scripture_keyword("Levítico 17,00")

    assert not result.references
    assert [issue.code for issue in result.issues] == ["malformed_reference"]


def test_does_not_treat_a_prose_dash_as_an_incomplete_range():
    result = parse_scripture_keyword(
        "Parvulos Babylonis (Salmo 137,9 - filhos de Babilônia)"
    )

    assert not result.issues
    assert result.references[0].normalized == "Salmos 137:9"


def test_context_does_not_turn_job_into_john_in_pg_or_pl():
    reference = parse_scripture_keyword("Jó 22", collection="PG").references[0]

    assert reference.book_key == "jo"
    assert reference.normalized == "Jó 22"


def test_evangelho_segundo_joao_is_not_second_john():
    reference = parse_scripture_keyword(
        "Evangelho segundo João 13,23", collection="PL"
    ).references[0]

    assert reference.book_key == "joao"
    assert reference.normalized == "São João 13:23"


def test_explicit_second_john_stays_second_john():
    reference = parse_scripture_keyword("II João 1,4", collection="PL").references[0]

    assert reference.book_key == "2 joao"


@pytest.mark.parametrize(
    "raw",
    [
        "Monitum de Luc d'Achery",
        "Códice Veneziano de São Marcos 359",
        "erro sobre os 25 anos de pontificado",
        "Mar Jacques l'intercis",
    ],
)
def test_quarantines_or_ignores_person_codex_and_prose_false_positives(raw):
    result = parse_scripture_keyword(raw, collection="PL")

    assert not result.references


def test_keeps_embedded_chapter_when_parenthesized_or_scripturally_cued():
    parenthesized = parse_scripture_keyword("Samuel (1 Reis 22)")
    commentary = parse_scripture_keyword("Comentário sobre o Salmo 151")

    assert parenthesized.references[0].normalized == "1 Reis 22"
    assert commentary.references[0].normalized == "Salmos 151"


def test_paixao_segundo_joao_is_the_gospel_not_second_john():
    reference = parse_scripture_keyword(
        "Paixão segundo João 18,1-19,42", collection="PL"
    ).references[0]

    assert reference.book_key == "joao"


def test_quarantines_structurally_implausible_chapter_number():
    result = parse_scripture_keyword("Mateus 555")

    assert not result.references
    assert result.issues[0].code == "implausible_chapter"


@pytest.mark.parametrize(
    "raw",
    [
        "João 22",
        "1 João 7",
        "Eclesiastes 15,9",
        "2 Coríntios 15,11",
        "Zacarias 17",
        "1 Reis 27,1-3",
        "Provérbios 47,2",
        "Mateus 55",
        "Isaías 69,1",
        "Jonas 87,4",
    ],
)
def test_quarantines_chapters_above_the_supported_maximum_for_each_book(raw):
    result = parse_scripture_keyword(raw, collection="PO")

    assert not result.references
    assert [issue.code for issue in result.issues] == ["implausible_chapter"]


@pytest.mark.parametrize("raw", ["Salmo 151", "Joel 4,1", "Daniel 14,1"])
def test_keeps_supported_catholic_and_alternate_chapter_boundaries(raw):
    result = parse_scripture_keyword(raw)

    assert result.references
    assert not result.issues


def test_normalizes_single_chapter_book_number_as_verse():
    reference = parse_scripture_keyword("Livro de Abdias 4").references[0]

    assert reference.normalized == "Abdias 1:4"
    assert reference.flags == ("single_chapter_verse",)


def test_disambiguates_first_kings_as_first_samuel_when_chapter_requires_it():
    reference = parse_scripture_keyword("1 Reis 26,15-16", collection="PG").references[0]

    assert reference.book_key == "1 samuel"
    assert reference.normalized == "1 Samuel 26:15-16"
    assert reference.flags == ("vulgate_regum_remap",)


def test_uses_endpoint_shape_to_resolve_high_cross_chapter_range():
    reference = parse_scripture_keyword("Isaías 52,1-53,12").references[0]

    assert reference.normalized == "Isaías 52:1-53:12"
