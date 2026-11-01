"""Lexical location is separate from judging a historical interpretation."""
import pytest

from tools.knowledge_graph.playground.evidence_matching import locate_quote


@pytest.mark.parametrize('quote,text,method', [
    # Actual discrepancies from PG026 physical page 71, run 8, blocks b3/b4.
    ('άλλος μὲν τῶν γενητῶν, ἴδιος δὲ τοῦ Πατρός',
     'ἄλλος μὲν τῶν γενητῶν, ἴδιος δὲ τοῦ Πατρός', 'normalized'),
    ('Moyses quoque dixit : Si alicui filii facti',
     'Moyses quoque dixit: Si alicui filii facti', 'normalized'),
    ('solusque proprius est naturæ Patris fetus,',
     'solusque proprius est naturæ Patris fœtus,', 'fuzzy'),
    ('Athanasius refutat', 'Atha-\nnasius refutat', 'normalized'),
    ('aeternitas Filii', 'æternitas Filii', 'normalized'),
    ('filius', 'ﬁlius', 'normalized'),
    ('λόγος', 'λόγος', 'normalized'),
    ('λόγος', 'λο­γος', 'normalized'),
    ('divindade do Filho', 'divindade\n do Filho', 'normalized'),
    ('Athanasius refutal sententiam eorum', 'Athanasius refutat sententiam eorum', 'fuzzy'),
    ('Athanasius refutat', 'Athanasius refutat', 'exact'),
])
def test_locates_actual_source_span_without_replacing_the_model_quote(quote, text, method):
    source = '📜 Contexto anterior. ' + text + ' Contexto posterior.'
    match = locate_quote(quote, source)
    assert match['accepted'] and match['method'] == method
    assert source[match['start']:match['end']] == match['matched_quote']
    assert match['matched_quote'] in text
    assert match['score'] >= .9


@pytest.mark.parametrize('quote,text', [
    ('Athanasius refutat sententiam eorum invented', 'Athanasius refutat sententiam eorum'),
    ('Athanasius refutat sententiam eorum', 'Athanasius non refutat sententiam eorum'),
    ('Athanasius non refutat sententiam eorum', 'Athanasius refutat sententiam eorum'),
    ('Athanasius never rejects the stated position', 'Athanasius ever rejects the stated position'),
    ('Abraham vero centum annorum erat 101', 'Abraham vero centum annorum erat 100'),
    ('Filius', 'Filium'),
    ('sententiam eorum', 'sententiam aliorum'),
    ('Son is created', 'Filius est creatus'),
    ('Athanasius accepts the argument', 'Athanasius refutes the argument'),
    ('Atanásio afirma a plena divindade do Filho', 'Atanásio rejeita a plena divindade do Filho'),
    ('23,5', '23.5'),
    ('x > 100', 'x < 100'),
    ('grátia', 'gratiarum'),
    ('...', 'Some text.'),
    ('', 'Some text.'),
    (' ', 'Some text.'),
])
def test_rejects_changed_content_or_insufficient_context(quote, text):
    result = locate_quote(quote, text)
    assert not result['accepted']
    assert result['matched_quote'] is None


def test_ambiguous_normalized_locations_require_review():
    result = locate_quote('átanásio refuta', 'Atanasio refuta. Atanasio refuta.')
    assert result['method'] == 'ambiguous' and not result['accepted']
    assert result['candidate_count'] == 2


def test_competing_similar_passages_require_review():
    result = locate_quote('Athanasius refutat sententiam eorum',
                          'Athanasius refutal sententiam eorum. Athanasius refutan sententiam eorum.')
    assert result['method'] == 'ambiguous' and not result['accepted']


def test_exact_match_has_priority_and_reports_repeated_occurrences():
    result = locate_quote('Atanásio', 'Atanasio. Atanásio. Atanásio.')
    assert result['method'] == 'exact' and result['candidate_count'] == 2


def test_missing_locator_is_not_a_search_across_sources():
    assert locate_quote('Atanásio', None)['method'] == 'unknown_locator'


def test_ambiguous_large_source_stops_fuzzy_search():
    result = locate_quote('Athanasius refutal sententiam eorum',
                          'Athanasius refutat sententiam eorum. ' * 1000)
    assert not result['accepted'] and 'Too many' in result['reason']


@pytest.mark.parametrize('quote,source', [
    # PG026:71, run 16 ev10: restored article and letters in corrupt Greek OCR.
    ('ὧν ὁ μὲν τοὺς ἀγγέλους ὁμογενεῖς εἴρηκε τῷ Χριστῷ',
     'ὧν  μὲν τος ἀγγέλους μογενεῖς εἶρηκε τῷ Χριστῷ'),
    # Run 19 ev8/ev10: two-letter corruption and a different Latin verb ending.
    ('De genitis autem filiis sic loquitur : Facti sunt Job filii septem et filiæ tres 20.',
     'De genitis autem illiis sic loquitur: Facti sunt Job filii septem et filiæ tres 20.'),
    ('Si ergo Filius 364 alius est a rebus factis, solusque proprius est naturæ Patris fœtus, frustra Ariani disputandi occasionem ex vocabulo, factus, arripuere.',
     'Si ergo Filius 364 alius est a rebus factis, solusque proprius est naturæ Patris fœtus, frustra Ariani disputandi occasionem ex vocabulo, factus, arriperent.'),
    ('Athanasius refutat argumentum eorum', 'Athanasius refutat argu mentum eorum'),
])
def test_levenshtein_alignment_preserves_offsets_and_explains_normalized_edits(quote, source):
    text = '📜 Contextus prior. ' + source + ' Textus posterior.'
    for model, original in [(quote, text), (source, '📜 ' + quote + ' Postea.')]:
        result = locate_quote(model, original)
        assert result['accepted'] and result['method'] == 'fuzzy'
        assert original[result['start']:result['end']] == result['matched_quote']
        assert result['edit_distance'] > 0 and result['diff']
        assert result['diff_direction'] == 'model_quote_to_source'
        rebuilt = result['normalized_quote']
        for change in reversed(result['diff']):
            assert change['quote_text'] == rebuilt[change['quote_start']:change['quote_end']]
            rebuilt = rebuilt[:change['quote_start']] + change['source_text'] + rebuilt[change['quote_end']:]
        assert rebuilt == result['normalized_source']
        assert result['score'] == round(1 - result['edit_distance'] /
                                        max(len(result['normalized_quote']), len(result['normalized_source'])), 6)


@pytest.mark.parametrize('quote,source', [
    # Run 18: a parenthesized note marker is not silently classified as dispensable.
    ('οὐ γεννητὸς ἐκ τούτων ὁ Υἱὸς σημαίνεται, ὦ Ἀρειανοί',
     'οὐ γεννητὸς (10) ἐκ τούτων ὁ Υἱὸς σημαίνεται, ὦ Ἀρειανοί'),
    ('Omnia per ipsum facta sunt, et sine ipso factum est nihil. Et, Omnia in sapientia fecisti.',
     'Omnia per ipsum facta sunt, et sine ipso factum est nihil 18. Et, Omnia in sapientia fecisti 19.'),
    ('Athanasius refutat sententiam eorum de generatione Filii',
     'Athanasius non refutat sententiam eorum de generatione Filii'),
    ('In hoc argumento de interpretatione scripturae Athanasius non refutat sententiam eorum sed recipit',
     'In hoc argumento de interpretatione scripturae Athanasius refutat sententiam eorum sed non recipit'),
    ('Abraham vero centum annorum erat 101', 'Abraham vero centum annorum erat 100'),
    ('Propositio mathematica scribitur x > 100', 'Propositio mathematica scribitur x < 100'),
])
def test_protected_edits_have_a_reviewable_candidate_but_are_not_accepted(quote, source):
    result = locate_quote(quote, source)
    assert not result['accepted'] and result['matched_quote'] is None
    assert 'manual review' in result['reason']
    candidate = result['review_candidate']
    assert not candidate['accepted']
    assert source[candidate['start']:candidate['end']] == candidate['matched_quote']
    assert candidate['diff'] and candidate['score'] >= .9


def test_variable_window_boundaries_are_one_occurrence_but_repeated_passages_are_not():
    quote = 'De genitis autem filiis sic loquitur : Facti sunt Job filii septem et filiæ tres 20.'
    source = 'De genitis autem illiis sic loquitur: Facti sunt Job filii septem et filiæ tres 20.'
    assert locate_quote(quote, 'Praefatio. ' + source + ' Postea.')['accepted']
    result = locate_quote(quote, source + ' Intermissio. ' + source)
    assert result['method'] == 'ambiguous' and not result['accepted']


def test_large_character_query_is_bounded_even_with_few_tokens():
    quote = 'a' * 4100 + ' verbum textus'
    source = 'a' * 4099 + 'x verbum textus'
    result = locate_quote(quote, source)
    assert not result['accepted'] and 'bound' in result['reason']
