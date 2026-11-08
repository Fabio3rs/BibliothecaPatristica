from pathlib import Path

import pytest

from tools.indexing.page_line_candidates import detect_page_line_convention, interpret_page_line_entry


def parse(raw, **kw):
    return interpret_page_line_entry({'entry_raw': raw, 'char_start': 123, 'entry_type': 'material',
                                     'flags': ['numeric_text_before_reference_group']}, **kw)


def primary(result):
    return [r for r in result['interpreted_references'] if r['candidate_role'] == 'primary']


def test_simple_pair_is_not_two_pages():
    result = parse('ἐξαρχος 770, 9.')
    assert [(r['number'], r['line_raw']) for r in primary(result)] == [(770, '9')]
    assert result['query_text'] == 'ἐξαρχος'
    assert result['input_flags'] == ['numeric_text_before_reference_group']


def test_multiple_lines_and_parallel_namespaces_preserved():
    raw = 'ἀλλάγιον 791, 8, Leo 282, 17,\n806, 10, 14, Leo 294, 12, 15.'
    result = parse(raw)
    assert [(r['number'], r['lines_raw'], r['series_key']) for r in primary(result)] == [
        (791, ['8'], 'petropolitana'), (282, ['17'], 'leo'),
        (806, ['10', '14'], 'petropolitana'), (294, ['12', '15'], 'leo')]
    assert any(r['number'] == 806 and r['series_key'] == 'leo'
               and r['candidate_role'] == 'alternative_namespace' for r in result['interpreted_references'])
    for ref in result['interpreted_references']:
        a,b = ref['source_span']
        assert raw[a-123:b-123] == ref['ref_raw']
        a,b = ref['page_span']
        assert int(raw[a-123:b-123]) == ref['number']
        for value, (a,b) in zip(ref['lines_raw'], ref['line_spans']):
            assert raw[a-123:b-123] == value
    assert 'line_grouping_by_numeric_scale' in primary(result)[2]['uncertainties']


def test_small_page_alternative_is_searchable_not_hidden():
    refs = parse('λόγος 5, 14, 7, 19.')['interpreted_references']
    assert any(r['number'] == 7 and r['line_raw'] == '19'
               and r['candidate_role'] == 'alternative_page_line_grouping' for r in refs)
    assert primary(parse('λόγος 5, 14, 7, 19.'))[0]['lines_raw'] == ['14','7','19']


def test_note_pages_and_lines_with_explicit_new_page_separator():
    refs = primary(parse('πάκτα pacta 399 Nota, 501, 8, 15; 613, 20.'))
    assert [r['number'] for r in refs] == [399,501,613]
    assert refs[0]['qualifier_raw'] == 'Nota'
    assert refs[0]['line_raw'] is None
    assert refs[1]['lines_raw'] == ['8','15']


def test_parenthetical_leo_and_no_unbounded_inheritance():
    result = parse('(προβάλλομαι εἰς Leo 130, 18, 137, 14).')
    assert primary(result)[0]['series_key'] == 'leo'
    assert {r['series_key'] for r in result['interpreted_references'] if r['number']==137} == {'leo','petropolitana'}
    assert primary(parse('ἄλλος 137, 14.'))[0]['series_key'] == 'petropolitana'


def test_line_range_preserved_no_page_expansion():
    refs = primary(parse('ἄμερ 721, 19 — 722, 6. Leo 229, 11 — 15.'))
    assert [r['number'] for r in refs] == [721,722,229]
    assert refs[-1]['lines_raw'] == ['11','15']
    assert 'line_range_preserved_not_expanded' in refs[-1]['uncertainties']
    assert 'range_endpoint_not_expanded' in refs[1]['uncertainties']


@pytest.mark.parametrize('raw', ['λόγος χωρίς ἀριθμόν.', 'λόγος 0, 9.', 'λόγος 123.', '123, 9.'])
def test_non_locators_or_missing_owner(raw):
    assert parse(raw) is None


def test_real_declaration_and_source_spans():
    root = Path(__file__).resolve().parents[1]
    path = next((root/'teste/PG110/text').glob('*-660.txt'))
    text = path.read_text()
    assert detect_page_line_convention(text)
    assert not detect_page_line_convention('Φίλιππος βασιλεύς 181, 226, 325.')
    raw = 'ἀλλάγιον 791, 8, Leo 282, 17,\n806, 10, 14, Leo 294, 12, 15.'
    start = text.index(raw)
    result = interpret_page_line_entry({'entry_raw':raw, 'char_start':start})
    for ref in result['interpreted_references']:
        a,b = ref['source_span']
        assert text[a:b] == ref['ref_raw']


def test_concatenated_input_preserves_local_owner_for_each_numeric_run():
    raw = '(διμοιρία Leo 99, 13).\nδιοικέω 563, 19, 29, 771, 16, Leo 257, 3.'
    result = parse(raw)
    refs = primary(result)
    assert refs[0]['query_text'] == 'διμοιρία'
    assert {r['query_text'] for r in refs[1:]} == {'διοικέω'}
    for ref in refs:
        a,b = ref['owner_span']
        assert raw[a-123:b-123] == ref['owner_raw']


@pytest.mark.parametrize('raw,page,series', [
    ('μάγγανον 879, 900, 17.', 900, 'petropolitana'),
    ('κόμης comes 725, 8, Leo 232, 734, 4.', 734, 'petropolitana'),
])
def test_high_line_adds_page_alternative_without_deleting_previous(raw,page,series):
    refs = parse(raw)['interpreted_references']
    assert any('line_above_heuristic_ceiling' in r['uncertainties'] for r in refs)
    alternative = next(r for r in refs if r['number']==page and r['series_key']==series)
    assert alternative['candidate_role'] == 'alternative_page_line_grouping'
    assert alternative['line_raw'] == ('17' if page == 900 else '4')
    assert 'high_line_or_new_page' in alternative['uncertainties']
    for r in refs:
        a,b = r['source_span']
        assert raw[a-123:b-123] == r['ref_raw']


def test_high_line_alternative_preserves_ambiguous_leo_namespace():
    refs = parse('σύμφωνα 832, 10, Leo 316, 323; 834, 10.')['interpreted_references']
    alt = [r for r in refs if r['number']==323]
    assert {r['series_key'] for r in alt} == {'petropolitana','leo'}
    assert all(r['line_raw'] is None for r in alt)


def test_owner_with_ignored_number_remains_searchable_and_flagged():
    refs = parse('κορωνᾶς προσφορά 223. κορμίν corpus 861, 26.')['interpreted_references']
    assert refs[0]['number']==861
    assert 'owner_contains_unparsed_numbers' in refs[0]['uncertainties']
    assert '223' in refs[0]['owner_raw']


@pytest.mark.parametrize('marker', ['Cedreno', 'Ced.', 'Cedr.'])
def test_explicit_external_citation_never_inherits_petropolitana(marker):
    raw = f'τζουκανίσμα in {marker} 11, 480.'
    refs = parse(raw)['interpreted_references']
    assert {r['series_key'] for r in refs} == {'cedrenus'}
    assert all('external_locator_units_uninterpreted' in r['uncertainties'] for r in refs)
    for r in refs:
        a,b = r['external_marker_span']
        assert raw[a-123:b-123] == r['external_marker_raw']
