import hashlib

import pytest

from tools.indexing.numeric_owner_context import repair_numeric_owner


def group(raw, owner, reference, *, occurrence=0):
    start = -1
    for _ in range(occurrence+1):
        start = raw.index(reference, start+1)
    return {'id': 'example', 'source': 'example.txt', 'owner_raw': owner,
            'raw': reference, 'char_start': start, 'char_end': start+len(reference),
            'source_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def test_pg002_aaron_literal_prefix_and_all_offsets():
    raw = '<bloco tipo="texto_principal">Ἀαρών 66, 350, 405.</bloco>'
    g = group(raw, 'Ἀαρών 66', '350, 405')
    p = repair_numeric_owner(raw, g)
    assert p['status'] == 'restored_candidate'
    assert p['corrected_owner_raw'] == 'Ἀαρών'
    assert p['expanded_reference_raw'] == '66, 350, 405'
    assert p['prefix_reference_raw'] == '66, '
    for value, span in [('corrected_owner_raw', 'corrected_owner_span'),
                        ('expanded_reference_raw', 'expanded_reference_span'),
                        ('prefix_reference_raw', 'prefix_reference_span'),
                        ('original_reference_raw', 'original_reference_span')]:
        a, b = p[span]
        assert raw[a:b] == p[value]
    assert p['prefix_reference_span'][1] == p['original_reference_span'][0]
    assert p['hypotheses'][0]['owner_raw'] == 'Ἀαρών 66'
    assert p['namespace_status'] == 'uninterpreted'


def test_multiple_prefix_numbers_and_wrapped_whitespace():
    raw = '<texto_principal>Long\n label, 66,\n 70, 350, 405.</texto_principal>'
    p = repair_numeric_owner(raw, group(raw, 'Long label, 66, 70', '350, 405'))
    assert p['status'] == 'restored_candidate'
    assert p['corrected_owner_raw'] == 'Long\n label'
    assert p['expanded_reference_raw'] == '66,\n 70, 350, 405'


@pytest.mark.parametrize('raw,owner,ref,warning', [
    ('Gen. 1, 4, 5, 7', 'Gen. 1', '4, 5, 7', 'owner_has_prose_or_title_number_cue'),
    ('Synodi anno 362, 615, 616', 'Synodi anno 362', '615, 616', 'owner_has_prose_or_title_number_cue'),
    ('Henry 4, 25, 26', 'Henry 4', '25, 26', 'owner_has_prose_or_title_number_cue'),
    ('Psalmus 66, 350, 405', 'Psalmus 66', '350, 405', 'owner_has_prose_or_title_number_cue'),
    ('Name 2, 350, 405', 'Name 2', '350, 405', 'small_unseparated_number_may_belong_to_owner'),
    ('Earlier 145.\n  Ἄβελ 53, 91, 102', 'Earlier 145. Ἄβελ 53', '91, 102', 'owner_contains_previous_numbers'),
    ('Name 66 350, 405', 'Name 66', '350, 405', 'numeric_list_continuation_separator_missing'),
    ('Name 66. . . 350, 405', 'Name 66', '350, 405', 'numeric_list_continuation_separator_missing'),
    ('Name 66, 350', 'Name 66', '350', 'unseparated_owner_number_without_long_list'),
    ('Name, II, 66, 350, 405', 'Name, II, 66', '350, 405', 'owner_has_structural_number_cue'),
    ('p 216, 222, 250', 'p 216', '222, 250', 'owner_is_only_reference_marker'),
    ('[ilegivel] 66, 350, 405', '[ilegivel] 66', '350, 405', 'owner_contains_illegible_text'),
    ('Name 66,\n\n350, 405', 'Name 66', '350, 405', 'context_crosses_paragraph_boundary'),
])
def test_ambiguous_numbers_preserve_alternative(raw, owner, ref, warning):
    p = repair_numeric_owner(raw, group(raw, owner, ref))
    assert p['status'] == 'ambiguous_numeric_owner'
    assert warning in p['warnings']
    assert len(p['hypotheses']) == 2


def test_left_separator_allows_short_list_but_not_year_cue():
    raw = 'Entry, 7, 42'
    assert repair_numeric_owner(raw, group(raw, 'Entry, 7', '42'))['status'] == 'restored_candidate'
    raw = 'Anno, 362, 42'
    assert repair_numeric_owner(raw, group(raw, 'Anno, 362', '42'))['status'] == 'ambiguous_numeric_owner'


def test_punctuation_in_owner_is_literal_even_when_whitespace_normalized():
    raw = 'Ἀαρών 66 , 350, 405'
    p = repair_numeric_owner(raw, group(raw, 'Ἀαρών 66,', '350, 405'))
    assert p['status'] == 'restored_candidate'
    assert p['prefix_reference_raw'] == '66 , '
    assert repair_numeric_owner(raw, group(raw, 'Ἀαρών 66;', '350, 405'))['status'] == 'evidence_mismatch'


def test_occurrence_local_matching_does_not_borrow_from_other_entry():
    raw = 'Ἀαρών 66, 350, 405. Other, 350, 405.'
    p = repair_numeric_owner(raw, group(raw, 'Ἀαρών 66', '350, 405', occurrence=1))
    assert p['status'] == 'evidence_mismatch'
    assert p['expanded_reference_span'] is None


def test_cross_block_and_owner_markup_rejected():
    raw = '<texto_principal>Name 66, </texto_principal><nota>350, 405</nota>'
    assert repair_numeric_owner(raw, group(raw, 'Name 66', '350, 405'))['status'] == 'evidence_mismatch'
    raw = '<texto_principal>Name 66, 350, 405</texto_principal>'
    assert repair_numeric_owner(raw, group(raw, '<texto_principal>Name 66', '350, 405'))['status'] == 'evidence_mismatch'


@pytest.mark.parametrize('owner', ['66', 'Name66', 'Name', 'Name, IX'])
def test_no_numeric_only_or_attached_digit_owner_rewrite(owner):
    raw = owner + ', 350, 405'
    assert repair_numeric_owner(raw, group(raw, owner, '350, 405'))['status'] == 'not_applicable'


def test_stale_offsets_fail_but_unrelated_source_change_keeps_local_provenance():
    raw = 'Ἀαρών 66, 350, 405'
    g = group(raw, 'Ἀαρών 66', '350, 405')
    original = g.copy()
    assert repair_numeric_owner(raw.replace('350', '351'), g)['status'] == 'evidence_mismatch'
    p = repair_numeric_owner(raw + '. More.', g)
    assert p['status'] == 'restored_candidate'
    assert p['original_source_sha256'] == g['source_sha256']
    assert 'source_changed_but_local_spans_match' in p['warnings']
    assert g == original
    assert 'volume' not in p and 'destination' not in p
