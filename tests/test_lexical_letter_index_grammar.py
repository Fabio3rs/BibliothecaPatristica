from pathlib import Path

import pytest

from tools.indexing.lexical_letter_index_grammar import NOTICE, parse_region_page


def parse(text, **kwargs):
    return parse_region_page(text, source='fixture.txt', volume='TEST', physical=1,
                             scopes=[{'span':[0,len(text)], 'section_id':'section'}], **kwargs)


def test_multiple_locators_and_wrapped_reference_are_one_row():
    raw = 'ἀσύγκριτος, 122 b. 123 a.\n135 b. et sæpius.\nἀσύγχυτος, 107 a.'
    result = parse(raw)
    assert len(result['entries']) == 2
    first = result['entries'][0]
    assert first['owner_raw'] == 'ἀσύγκριτος'
    assert [x['number'] for x in first['locator_atoms']] == [122,123,135]
    assert first['reference_namespace'] == 'number_lowercase_side_uninterpreted'
    assert raw[slice(*first['reference_span'])] == first['reference_raw']


def test_wrapped_owner_and_side_preserve_original_spans():
    raw = 'συνεκτικός, (συνεκτικώτερος, συνε-\nκτικώτατος), 124 b. 148\nb.\nλόγος, 15 a.'
    result = parse(raw)
    assert len(result['entries']) == 2
    assert 'συνε-\nκτικώτατος' in result['entries'][0]['owner_raw']
    assert result['entries'][0]['locator_atoms'][1]['number'] == 148


def test_namespace_requires_literal_notice_not_volume_id():
    raw = 'λόγος, 15 a.'
    assert parse(raw)['entries'][0]['reference_namespace'] != 'gudian_folio_side'
    result = parse(raw,section_context={'section':{'notice':NOTICE}})
    assert result['entries'][0]['reference_namespace'] == 'gudian_folio_side'


@pytest.mark.parametrize('raw', [
    'A Phoca devictus, 17. A', 'A Latin sentence 15 a.', 'λόγος, 15 A.',
    'λόγος, 15 q. 20 a.', 'λόγος, 15 a. 20.', '15 a. 20 b.',
    'INDEX VERBORUM. 15 a.',
])
def test_unsupported_material_stays_residual(raw):
    result = parse(raw)
    assert not result['entries']
    assert result['residuals']


def test_illegible_row_does_not_poison_or_inherit_previous_owner():
    result = parse('λόγος, 15 a.\n[ilegivel], 16 b.\nθεός, 20 a.')
    assert [e['owner_raw'] for e in result['entries']] == ['λόγος','θεός']
    assert any('[ilegivel]' in r['raw'] for r in result['residuals'])


def test_incomplete_owner_does_not_absorb_next_independent_entry():
    result = parse('ἀσύγχυτος\nλόγος, 15 a.')
    assert [e['owner_raw'] for e in result['entries']] == ['λόγος']
    assert result['residuals'][0]['raw'] == 'ἀσύγχυτος'


def test_column_continuation_not_attached_without_proof():
    raw = '<bloco tipo="texto_principal">λόγος, 15 a.</bloco><bloco tipo="texto_principal">20 b.\nθεός, 25 a.</bloco>'
    result = parse(raw)
    assert result['entries'][0]['reference_raw'] == '15 a.'
    assert any(r['raw']=='20 b.' for r in result['residuals'])


def test_scopes_reject_overlap():
    with pytest.raises(ValueError):
        parse_region_page('λόγος, 15 a.',source='x',volume='x',physical=1,
                          scopes=[{'span':[0,10]},{'span':[3,11]}])


def test_actual_pg091_823_wrapped_rows_and_spans():
    path = Path('teste/PG091/text/c61c5555-0694-4bd8-8f43-937ede070ce9-823.txt')
    if not path.exists():
        pytest.skip('Corpus fixture absent')
    raw = path.read_text()
    result = parse(raw)
    assert len(result['entries']) == 209
    target = next(e for e in result['entries'] if e['owner_raw'] == 'ἄσχετος')
    assert len(target['locator_atoms']) == 12
    for entry in result['entries']:
        for field in ('owner','reference'):
            assert raw[slice(*entry[field+'_span'])] == entry[field+'_raw']
        for atom in entry['locator_atoms']:
            assert raw[slice(*atom['span'])] == atom['raw']


def test_declared_repeated_scans_preserve_rows_and_provenance():
    result = parse('λόγος, 15 a.', section_context={'repeated_scan_groups':[[1,3]]})
    entry = result['entries'][0]
    assert entry['repeated_scan_physical_pages'] == [1,3]
    assert 'reviewed_repeated_printed_spread' in entry['flags']
