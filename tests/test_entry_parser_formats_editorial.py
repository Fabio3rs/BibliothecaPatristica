"""Conservative contract checks for unsupported editorial index formats."""
from pathlib import Path

import pytest

from tools.indexing.entry_parser_experiment import parse_page

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('volume,physical', [
    ('PL010', 462), ('PL010', 481), ('PL010', 489),
    ('PG110', 660), ('PG110', 662), ('PG110', 664),
])
def test_real_editorial_pages_remain_unvalidated_and_traceable(volume, physical):
    paths = list((ROOT / 'teste' / volume / 'text').glob(f'*-{physical}.txt'))
    assert len(paths) == 1
    raw = paths[0].read_text()
    result = parse_page(raw, source=str(paths[0]), physical=physical,
                        volume=volume, profile='generic-literal-experimental')
    assert result['entries']
    assert result['import_ready'] is False
    for entry in result['entries']:
        assert entry['status'] == 'candidate_only'
        assert 'unvalidated_profile' in entry['flags']
        assert entry['context_candidate_id'] is None
        assert raw[entry['char_start']:entry['char_end']] == entry['entry_raw']
        assert 'target_file' not in entry
        if 'reference_span' in entry:
            span = entry['reference_span']
            assert raw[span['char_start']:span['char_end']] == entry['references_raw']


@pytest.mark.parametrize('volume,text', [
    ('PG110', 'ἐξαρχος 770, 9.'),
    ('PG110', 'ξυλὴ 627, 3, Leo 172, 1.'),
    ('PL010', 'Impietas nihil in se habet sapientiæ et prudentiæ, II, 120.'),
])
def test_unsupported_locator_grammar_is_flagged_not_promoted(volume, text):
    raw = f'<bloco tipo="texto_principal">{text}</bloco>'
    result = parse_page(raw, volume=volume, profile='generic-literal-experimental')
    assert len(result['entries']) == 1
    entry = result['entries'][0]
    assert entry['entry_raw'] == text
    assert {'numeric_text_before_reference_group', 'unsupported_editorial_namespace'} & set(entry['flags'])
    assert 'unvalidated_profile' in entry['flags']
    assert result['import_ready'] is False


def test_missing_terminal_punctuation_does_not_discard_source():
    text = 'AARON consecratio. I, 519\nABINADAB, id est, fratris mei imperium. I, 79'
    raw = f'<bloco tipo="texto_principal">{text}</bloco>'
    result = parse_page(raw, volume='PL010', profile='generic-literal-experimental')
    assert not result['entries']
    assert len(result['residuals']) == 1
    assert result['residuals'][0]['raw'] == text
