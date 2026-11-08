from pathlib import Path

import pytest

from tools.indexing.po_locator_candidates import parse_po_locators, locate_po_candidates
from tools.indexing.entry_parser_po import parse_po_page


def pages(raw, notation='page_subscript'):
    return [x['page'] for x in parse_po_locators(raw, notation)['locators']]


def test_page_line_lists_preserve_page_only_and_spans():
    raw = '28, 2; 29, 3, 4; 33, 14, 34, 8'
    result = parse_po_locators(raw, 'page_line', base=100)
    assert [x['page'] for x in result['locators']] == [28, 29, 33]
    assert 'ambiguous_line_list_or_missing_page_separator' in result['locators'][-1]['warnings']
    for item in result['locators']:
        assert raw[item['char_start'] - 100:item['char_end'] - 100] == item['raw']
        span = item['page_span']
        assert raw[span['char_start'] - 100:span['char_end'] - 100] == span['raw']


def test_lost_comma_is_candidate_without_pretending_line_certainty():
    result = parse_po_locators('79 10.', 'page_line')['locators']
    assert [x['page'] for x in result] == [79]
    assert result[0]['detail_raw'] == '10'
    assert result[0]['warnings'] == ['missing_page_detail_separator']


def test_subscripts_and_notes_are_never_extra_pages():
    assert pages('29₈ 48₁₄ 58₆₋₁₀ 65₇₋') == [29, 48, 58, 65]
    assert pages('16₃ n. 1; cf. 45₁₃; 66 n. 3; 73 n. 1.') == [16, 45, 66, 73]
    assert pages('61,12') == [61]
    assert pages('94₁₁ 94₁₁') == [94, 94]


def test_cross_page_range_retains_only_printed_endpoints():
    result = parse_po_locators('69_13-70_1 76_11-12', 'page_subscript')['locators']
    assert [r['page'] for r in result] == [69, 70, 76]
    assert [r['range_endpoint'] for r in result] == ['start', 'end', None]
    assert result[1]['page_span']['raw'] == '70'


def test_remission_numbers_and_unknown_tail_not_reinterpreted():
    assert pages('98, 11, 12 (cf. Πτολεμαῖος 9ᵃ; 11ᵃ).', 'page_line') == [98]
    assert pages('9ᵃ', 'page_line') == []
    assert pages('29₈ (cf. 9ᵃ)') == [29]
    assert parse_po_locators('n. 1', 'page_subscript')['residuals']
    assert pages('0₈') == []
    with pytest.raises(ValueError):
        parse_po_locators('29,8', 'unknown')


def test_literal_marker_survives_without_sequence_and_keeps_alternatives():
    series = {'spec': {'id': 'bounded-fascicle'}, 'nodes': {61: {239, 260}},
              'supported': {(61, 239): 2}, 'pages': {
                  239: {'file': 'first.txt', 'sha256': 'a', 'header': '[61]'},
                  260: {'file': 'second.txt', 'sha256': 'b', 'header': '[61]'}}}
    result = locate_po_candidates(series, 61)
    assert [f['physical'] for f in result['files']] == [239, 260]
    assert result['files'][1]['evidence_strength'] == 'literal_marker_without_sequence'
    assert result['status'] == 'ambiguous_header_candidates'
    assert not result['verified']
    assert locate_po_candidates(series, 62)['files'] == []


@pytest.mark.parametrize('physical', [313, 314, 315, 317, 318])
def test_real_po022_preserves_every_explicit_page_base(physical):
    paths = list(Path('teste/PO022/text').glob(f'*-{physical}.txt'))
    if not paths:
        pytest.skip('Corpus not present')
    raw = paths[0].read_text()
    parsed = parse_po_page(raw, volume='PO022', physical=physical)
    count = 0
    for entry in parsed['entries']:
        if not entry.get('references_raw'):
            continue
        refs = entry['references_raw']
        base = entry['reference_span']['char_start']
        result = parse_po_locators(refs, 'page_subscript', base=base)
        for candidate in result['locators']:
            span = candidate['page_span']
            assert raw[span['char_start']:span['char_end']] == str(candidate['page'])
            count += 1
    assert count > 10


def test_real_po015_aaron_multiple_lines_and_alexander_remission():
    paths = list(Path('teste/PO015/text').glob('*-297.txt'))
    if not paths:
        pytest.skip('Corpus not present')
    raw = paths[0].read_text()
    entries = parse_po_page(raw, volume='PO015', physical=297)['entries']
    aaron = next(e for e in entries if e['text_raw'] == 'Ἀαρών')
    result = parse_po_locators(aaron['references_raw'], 'page_line',
                               base=aaron['reference_span']['char_start'])
    assert [c['page'] for c in result['locators']] == [28, 34, 50, 78, 79, 83]
    for c in result['locators']:
        assert raw[c['char_start']:c['char_end']] == c['raw']
    alexander = next(e for e in entries if e['text_raw'] == 'Ἀλέξανδρος' and 'cf.' in e['references_raw'])
    assert pages(alexander['references_raw'], 'page_line') == [98]


def test_unicode_line_ranges_do_not_hide_following_pages():
    parsed = parse_po_locators('109₁₁-₁₃ 117₁₂ 118₆; 174₁-₅-₁₀ 175₁₀', 'page_subscript')
    assert [x['page'] for x in parsed['locators']] == [109,117,118,174,175]
    assert parsed['locators'][0]['detail_raw'] == '₁₁-₁₃'
    assert not parsed['residuals']


def test_etc_preserves_uncertainty_and_continues_known_pages():
    parsed = parse_po_locators('173₁₀ etc. 174₁-₅', 'page_subscript')
    assert [x['page'] for x in parsed['locators']] == [173,174]
    assert parsed['residuals'][0]['reason'] == 'unspecified_continuation'
