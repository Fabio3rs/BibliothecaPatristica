import copy
from pathlib import Path

import pytest

from tools.indexing.roman_pair_index_grammar import parse_region_page, read_reference_group, roman_value


def parse(raw, **context):
    return parse_region_page(raw, source='fixture.txt', volume='OTHER', physical=1,
                             scopes=[{'section_id': 's', 'span': [0, len(raw)]}],
                             section_context={'s': {'reference_namespace': 'book_chapter', 'maximum_roman': 7, **context}})


def test_pair_namespace_not_in_lemma():
    e = parse('Combibere, coimbibere V, 30.')['entries'][0]
    assert e['owner_raw'] == 'Combibere, coimbibere'
    assert e['reference_raw'] == 'V, 30'
    assert e['reference_namespace'] == 'book_chapter'
    assert e['locators'][0]['roman_value'] == 5
    assert e['locators'][0]['arabic_value'] == 30


def test_repeated_pairs_and_same_book_chapter_lists():
    e = parse('Cicero (M. T.) III, 6, 17; IV, 38.')['entries'][0]
    assert [(x['roman_value'], x['arabic_value']) for x in e['locators']] == [(3, 6), (3, 17), (4, 38)]
    assert e['locators'][1]['inherited_roman_evidence']['raw'] == 'III, 6'


def test_clause_ownership_and_wrapped_pairs():
    raw = 'Caput nocens VII, 38, sanctissimum IV,\n34, rerum et columen I, 29.'
    rows = parse(raw)['entries']
    assert [x['owner_raw'] for x in rows] == ['Caput nocens', 'sanctissimum', 'rerum et columen']
    assert rows[1]['inherited_owner_evidence']['raw'] == 'Caput nocens'
    assert rows[1]['reference_raw'] == 'IV,\n34'


def test_lowercase_next_line_not_assigned_previous_owner():
    rows = parse('genitura I, 11.\n genethliaca ratio II, 69.')['entries']
    assert 'inherited_owner_evidence' not in rows[1]


def test_ibid_within_section_carries_explicit_evidence():
    first = parse('Aures pertundere II, 41.')
    previous = copy.deepcopy(first['context'])
    raw = 'Aurium lamina ibid.'
    result = parse_region_page(raw, source='next.txt', volume='OTHER', physical=2,
                              scopes=[{'section_id': 's', 'span': [0, len(raw)]}], section_context=previous)
    e = result['entries'][0]
    assert e['locators'][0]['roman_value'] == 2
    assert e['locators'][0]['arabic_value'] == 41
    assert e['inherited_reference_evidence']['source'] == 'fixture.txt'
    assert previous == first['context']


def test_ibid_without_prior_reference_is_preserved_unresolved():
    e = parse('Aurium lamina ibid.')['entries'][0]
    assert 'ibid_without_prior_reference' in e['flags']
    assert 'roman_value' not in e['locators'][0]


def test_no_ocr_digit_roman_repair_or_leak_into_next_owner():
    raw = 'funesta busticeta, 1, 41.\n fundi ex alvis matrum II, 16.'
    result = parse(raw)
    assert [x['owner_raw'] for x in result['entries']] == ['fundi ex alvis matrum']
    assert any('funesta' in x['raw'] and x['reason'] == 'unsupported_previous_locator' for x in result['residuals'])


def test_out_of_work_book_is_residual_not_nearby_book():
    result = parse('pericula VIII, 40.\n Intestinæ virtutes I, 20.')
    assert [x['owner_raw'] for x in result['entries']] == ['Intestinæ virtutes']
    assert any('VIII, 40' in x['raw'] for x in result['residuals'])


def test_bare_numbers_and_dates_do_not_create_page_references():
    assert not parse('Pax 1792.\n Tyrius 10.')['entries']
    assert roman_value('IIII') is None
    assert roman_value('VII') == 7


def test_nested_xml_tags_not_parsed_as_owners():
    raw = '<pagina><bloco tipo="cabecalho">INDEX III 1310</bloco><bloco tipo="texto_principal">Combibere V, 30.</bloco></pagina>'
    e = parse(raw)['entries'][0]
    assert e['owner_raw'] == 'Combibere'
    for name in ('owner', 'reference'):
        a, b = e[f'{name}_span']
        assert raw[a:b] == e[f'{name}_raw']


def test_unknown_semantics_stay_unknown():
    raw = 'Nomen IV, 7.'
    e = parse_region_page(raw, source='x', volume='X', physical=1, scopes=[{'span': [0, len(raw)]}])['entries'][0]
    assert e['reference_namespace'] == 'roman_arabic_pair_unknown'


def test_scope_validation():
    with pytest.raises(ValueError):
        parse_region_page('abc', source='x', volume='x', physical=1,
                          scopes=[{'span': [0, 2]}, {'span': [1, 3]}])


def test_full_reviewed_sections_are_literal_and_replay_identically():
    from tools.indexing.reviewed_index_sections import build_sections
    root = Path(__file__).resolve().parents[1]
    plan = root / 'docs/fixtures/index_rebuild/pl005_roman_pair_sections.json'
    sections = build_sections([plan], root)
    totals = []
    for section in sections:
        context = {section['section_id']: section['parser_context']}
        entries = []
        for physical in sorted({x['physical'] for x in section['spans']}):
            scopes = [dict(x, section_id=section['section_id']) for x in section['spans'] if x['physical'] == physical]
            source = scopes[0]['source']
            raw = Path(source).read_text()
            args = dict(source=source, volume='PL005', physical=physical, scopes=scopes, section_context=context)
            result = parse_region_page(raw, **args)
            assert result == parse_region_page(raw, **args)
            context = result['context']
            accounted = [0] * len(raw)
            for part in result['entries'] + result['residuals']:
                a, b = part.get('entry_span', part.get('span'))
                for offset in range(a, b):
                    accounted[offset] += 1
            assert all(accounted[offset] == 1 for scope in scopes
                       for offset in range(*scope['span']) if not raw[offset].isspace())
            for e in result['entries']:
                for name in ('owner', 'reference'):
                    a, b = e[f'{name}_span']
                    assert raw[a:b] == e[f'{name}_raw']
                assert not any(x.startswith('owner_contains') for x in e['flags'])
            entries.extend(result['entries'])
        totals.append(len(entries))
    assert totals == [69, 1244, 1415]
