import json
from pathlib import Path

import pytest

from tools.indexing.pg114_index_grammar import parse_region_page
from tools.indexing.unrepresented_region_recovery import valid_entry


def parse(raw, family='allatian_authors', context=None):
    return parse_region_page(raw, source='fixture.txt', volume='fixture', physical=1,
                             scopes=[{'span': [0, len(raw)], 'section_id': 's'}],
                             section_context=context or {'s': {'family': family}})


def test_distinct_clauses_and_literal_provenance():
    raw = 'Alexander monachus in S. Barnabam apostolum, 99. De\nInventione crucis, 110.'
    result = parse(raw)
    assert [(e['owner_raw'], e['reference_raw']) for e in result['entries']] == [
        ('Alexander monachus in S. Barnabam apostolum', '99'), ('De\nInventione crucis', '110')]
    assert all(valid_entry(raw, e) for e in result['entries'])
    assert result['entries'][1]['inherited_owner_evidence']['raw'].startswith('Alexander')


def test_ibid_retains_previous_literal_not_invented_number():
    first = parse('Asterius in Phocam, 96.')
    raw = 'Athanasius, S. Antonii, ibid.'
    second = parse(raw, context=first['context'])
    assert second['entries'][0]['reference_raw'] == 'ibid.'
    assert second['entries'][0]['inherited_reference_evidence']['raw'] == '96'


def test_saints_secondary_numbers_are_not_flat_page_list():
    raw = 'Andreæ apostoli, 90, 43, 94, 17, 97, 5, 109, 4.'
    entry = parse(raw, 'allatian_saints')['entries'][0]
    assert entry['reference_raw'] == '90, 43, 94, 17, 97, 5, 109, 4'
    assert entry['reference_namespace'] == 'bracketed_text_number_with_secondary_locator'
    assert 'page_candidates_numbers' not in entry


def test_numeric_continuation_and_cross_reference_remain_residual():
    raw = '82, 6, 83, 22.\nVisitatio, 89, 3.\nJoannis Chrysostomi. Vide Chrysostomi.\nJoannis Damasceni, 112, 4.'
    result = parse(raw, 'allatian_saints')
    assert [e['owner_raw'] for e in result['entries']] == ['Visitatio', 'Joannis Damasceni']
    assert {'orphan_numeric_continuation', 'textual_cross_reference'} <= {r['reason'] for r in result['residuals']}


def test_ordinals_and_unreferenced_prior_paragraph_not_attributed():
    raw = 'Est Theodoti Ancyrani.\n\nMacarius, Dominica 3. Jejuniorum, 99. In festum, 102.'
    result = parse(raw)
    assert [e['reference_raw'] for e in result['entries']] == ['99', '102']
    assert result['entries'][0]['owner_raw'] == 'Macarius, Dominica 3. Jejuniorum'
    assert any(r['raw'] == 'Est Theodoti Ancyrani.' for r in result['residuals'])


def test_rejects_overlapping_scopes():
    with pytest.raises(ValueError):
        parse_region_page('Alpha, 99.', source='x', volume='v', physical=1,
                          scopes=[{'span': [0, 8]}, {'span': [2, 10]}])


def test_corpus_sections_boundaries_literal_and_deterministic():
    from tools.corpus_utils import page_files
    root = Path(__file__).resolve().parents[1]
    directory = root / 'teste/PG114/text'
    if not directory.is_dir():
        pytest.skip('local six-page corpus fixture unavailable')
    pages = page_files(directory, suffixes=['.txt'])
    spec = json.loads((root / 'docs/fixtures/index_rebuild/pg114_index_sections.json').read_text())
    counts = []
    for section in spec['sections']:
        context = {section['id']: section}
        total = 0
        for physical in range(section['first'], section['last'] + 1):
            source = pages[physical][0]
            raw = source.read_text()
            a = raw.index(section['opening_quote']) if physical == section['first'] else 0
            b = raw.index(section['end_quote']) if physical == section['last'] and section.get('end_quote') else len(raw)
            kwargs = dict(source=str(source), volume='PG114', physical=physical,
                          scopes=[{'span': [a, b], 'section_id': section['id']}], section_context=context)
            result = parse_region_page(raw, **kwargs)
            assert result == parse_region_page(raw, **kwargs)
            assert all(valid_entry(raw, e) and a <= e['entry_span'][0] < e['entry_span'][1] <= b
                       for e in result['entries'])
            assert all(raw[r['span'][0]:r['span'][1]] == r['raw'] for r in result['residuals'])
            total += len(result['entries'])
            context = result['context']
        counts.append(total)
    assert counts == [392, 190, 91]


def test_ibid_without_period_before_clause_preserves_reference_chain():
    raw = 'Asterius in Phocam, 96. In Christum, ibid. De Pascha, ibid. In festum, 99.'
    entries = parse(raw)['entries']
    assert [e['reference_raw'] for e in entries] == ['96', 'ibid', 'ibid', '99']
    assert entries[1]['inherited_reference_evidence']['raw'] == '96'
    assert entries[2]['inherited_reference_evidence']['raw'] == '96'
