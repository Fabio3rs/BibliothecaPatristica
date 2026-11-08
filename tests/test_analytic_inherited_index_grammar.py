from pathlib import Path
import re

import pytest

from tools.indexing.analytic_inherited_index_grammar import parse_region_page, reference_groups


def parse(raw, context=None, source='sample.txt'):
    return parse_region_page(raw, source=source, volume='TEST', physical=1,
        scopes=[{'span': [0, len(raw)], 'section_id': 's'}],
        section_context=context or {'s': {'family': 'analytic_roman_inherited'}})


def test_clauses_lists_and_preposition_are_separate():
    raw = 'Agamemnon, I, 217. Ejus expeditio, I, 218, 233. Victus, ibid., 17. A Phoca devictus, II, 34.'
    out = parse(raw)
    assert [e['owner_raw'] for e in out['entries']] == ['Agamemnon', 'Ejus expeditio', 'Victus', 'A Phoca devictus']
    assert [e['reference_raw'] for e in out['entries']] == ['I, 217', 'I, 218, 233', 'ibid., 17', 'II, 34']
    assert out['entries'][2]['inherited_reference_evidence']['raw'] == 'I, 218, 233'
    assert 'ibid_with_additional_locator_interpretation_pending' in out['entries'][2]['flags']


def test_unpunctuated_namespace_and_numeric_list_preserved():
    out = parse('Aaron mors I,\n128, 140. Alius, I, 17. 92. Frigus, I, 814; II, 341, 456.')
    assert [e['reference_raw'] for e in out['entries']] == ['I,\n128, 140', 'I, 17. 92', 'I, 814; II, 341, 456']
    assert out['context']['s']['previous_namespace']['raw'] == 'II'


def test_bare_number_inherits_only_candidate_namespace():
    out = parse('Alexander, I, 1075. Ejus obitus, 1077.')
    entry = out['entries'][1]
    assert entry['inherited_namespace_evidence']['raw'] == 'I'
    assert entry['reference_raw'] == '1077'
    assert entry['inherited_owner_evidence']['raw'] == 'Alexander'
    assert 'implicit_roman_namespace_candidate' in entry['flags']


def test_cross_page_ibid_preserves_source_chain_without_rewriting():
    first = parse('Alexander, I, 1075.', source='first.txt')
    second = parse('Ejus obitus, ibid.', context=first['context'], source='second.txt')
    assert second['entries'][0]['inherited_reference_evidence']['source'] == 'first.txt'
    assert second['entries'][0]['reference_raw'] == 'ibid.'
    assert first['context']['s']['previous_reference']['raw'] == 'I, 1075'


def test_unlabelled_body_and_dates_are_not_index():
    raw = 'Anno 755, mense Octobri papa profectus est.'
    out = parse_region_page(raw, source='x', volume='X', physical=1, scopes=[{'span': [0,len(raw)], 'section_id':'s'}])
    assert not out['entries']
    assert out['residuals'][0]['reason'] == 'missing_analytic_section_signature'
    assert list(reference_groups('Anno 755 mense Octobri')) == []


def test_crossrefs_and_orphan_numeric_continuations_remain_residual():
    out = parse('II, 341. Octavius. V. Augustus. Orus, I, 36.')
    assert [e['owner_raw'] for e in out['entries']] == ['Orus']
    assert {r['reason'] for r in out['residuals']} >= {'orphan_locator_continuation', 'textual_cross_reference'}


def test_invalid_scope_rejected():
    with pytest.raises(ValueError):
        parse_region_page('Text', source='x', volume='X', physical=1,
            scopes=[{'span':[0,4]}, {'span':[2,4]}])


def test_real_opening_and_first_three_pages_are_reproducible():
    root = Path(__file__).resolve().parents[1]
    context = {'s': {'family':'analytic_roman_inherited',
                    'notice':'Revocatur Lector ad numeros grandiores textui insertos.'}}
    counts = []
    for number in (767,768,769):
        path = next((root/'teste/PG122/text').glob(f'*-{number:03}.txt'))
        raw = path.read_text()
        kwargs = dict(source=str(path.relative_to(root)), volume='PG122',physical=number,
            scopes=[{'span':[0,len(raw)],'section_id':'s'}], section_context=context)
        out = parse_region_page(raw, **kwargs)
        assert out == parse_region_page(raw, **kwargs)
        counts.append(len(out['entries']))
        for entry in out['entries']:
            assert raw[slice(*entry['owner_span'])] == entry['owner_raw']
            assert raw[slice(*entry['reference_span'])] == entry['reference_raw']
            assert entry['reference_namespace'] == 'internal_text_number_with_roman_namespace'
            assert not re.search(r'\b[IVXLCDM]+,\s*\d', entry['owner_raw'])
        context = out['context']
    assert all(n > 100 for n in counts)


def test_corrupt_previous_locator_not_concatenated_to_following_name():
    raw = 'Abimelechus frater, II, a\n593.\nAbisaca adducta, I, 160.'
    out = parse(raw)
    assert [e['owner_raw'] for e in out['entries']] == ['Abisaca adducta']
    assert any(r['reason'] == 'unsupported_previous_numeric_clause' and 'Abimelechus' in r['raw']
               for r in out['residuals'])
