import json
from pathlib import Path

import pytest

from tools.knowledge_graph.playground.common import digest
from tools.knowledge_graph.playground.evidence_locator import locate_quote as old_locate
from tools.knowledge_graph.playground.evidence_locator_v4 import locate_quote

CASES = json.loads((Path(__file__).parent/'fixtures/kg_locator_verification_v4.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: f"{c['run_id']}-{c['evidence_id']}")
def test_real_micro_differences_and_preserved_offsets(case):
    quote, source = case['quote'], case['source_text']
    assert digest(source) == case['source_sha256']
    old = old_locate(quote, source, names=case['names'])
    new = locate_quote(quote, source, names=case['names'])
    assert not old['accepted']
    assert new['accepted'] == case['expected_accepted']
    assert new['semantic_support'] == 'not_evaluated'
    assert digest(source) == case['source_sha256']
    if new['accepted']:
        assert source[new['start']:new['end']] == new['matched_quote']
        for op in new['edit_operations']:
            assert source[op['source_start']:op['source_end']] == op['source_text']
            assert quote[op['quote_start']:op['quote_end']] == op['quote_text']


@pytest.mark.parametrize('quote,source', [
    ('Filius creatus est ante omnia saecula', 'Filius non creatus est ante omnia saecula'),
    ('Filius non creatus est ante omnia saecula', 'Filius nec creatus est ante omnia saecula'),
    ('annos habuit et dies 100 in terra', 'annos habuit et dies 101 in terra'),
    ('annos habuit et dies septum in terra', 'annos habuit et dies septem in terra'),
    ('mensura a > b in hac demonstratione', 'mensura a < b in hac demonstratione'),
    ('Athanasio dixit verbum esse aeternum', 'Athanasii dixit verbum esse aeternum'),
    ('datur salus omnibus da-', 'datur salus omnibus datur'),
    ('καὶ οὐ πατήρ ἐστιν ἀγαθός', 'καὶ οὗ πατήρ ἐστιν ἀγαθός'),
    ('καὶ oὐ πατήρ ἐστιν ἀγαθός', 'καὶ οὗ πατήρ ἐστιν ἀγαθός'),
    ('ὁ υἱὸς ὁμοούσιος τῷ πατρί ἐστιν', 'ὁ υἱὸς ὁμοιούσιος τῷ πατρί ἐστιν'),
    ('ὁ υἱὸς γεννητὸς ἐν τῷ λόγῳ λέγεται', 'ὁ υἱὸς γενητὸς ἐν τῷ λόγῳ λέγεται'),
    ('numerus x est in hac demonstratione', 'numerus x² est in hac demonstratione'),
    ('ὁ λόγος ἀγαθός ἐστιν ἔτι', 'ὁ λόγος κακός ἐστιν ἔτι'),
])
def test_meaningful_differences_still_require_review(quote, source):
    assert not locate_quote(quote, source, names=['Athanasio'])['accepted']


def test_micro_copy_does_not_turn_repeated_occurrences_into_unique_location():
    source = 'Εἰ δὲ εἴποι τις λόγον ἀληθῆ.'
    quote = 'Eἰ δὲ εἴποι τις λόγον ἀληθῆ.'
    new = locate_quote(quote, source+'\n'+source)
    assert new['method'] == 'ambiguous'
    assert new['located'] and not new['accepted'] and not new['position_resolved']


def test_original_boundary_fragment_is_located_without_completion():
    quote = 'Alexandria in Palæstinam primo secessit. Bis au-'
    source = '\n'+quote+'\n  '
    new = locate_quote(quote, source)
    assert new['accepted'] and new['method'] == 'exact'
    assert new['matched_quote'] == quote
    assert new['acceptance_rule'] == 'source_boundary_fragment'
    assert not locate_quote(quote, source+'tem aliud dicitur')['accepted']
    assert not locate_quote('Bis au-', source)['accepted']


def test_canonical_unicode_and_hyphenation_continue_to_work():
    source = 'πρόλογος· τῷ σῶ-\nματι καὶ æternum œconomia.'
    new = locate_quote('τῷ σώματι καὶ aeternum oeconomia', source)
    assert new['accepted'] and new['policy'] == 'local-quote-v4'
    assert source[new['start']:new['end']] == new['matched_quote']


def test_syriac_letters_are_preserved_and_script_transliteration_is_not_micro_ocr():
    source = 'ܒܪܝܫܝܬ ܒܪܐ ܐܠܗܐ ܠܫܡܝܐ ܘܠܐܪܥܐ'
    assert locate_quote(source, source)['accepted']
    assert not locate_quote('breshit bra alaha lashmaya wlar a', source)['accepted']
