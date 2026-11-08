import hashlib

import pytest

from tools.indexing.roman_owner_context import repair_roman_owner


def group(raw, owner, reference, *, occurrence=0):
    start = -1
    for _ in range(occurrence+1):
        start = raw.index(reference, start+1)
    return {'id': 'example', 'owner_raw': owner, 'raw': reference,
            'char_start': start, 'char_end': start+len(reference),
            'source_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def test_restore_exact_literal_and_absolute_character_spans():
    raw = '<texto_principal>Æthiopia, II, 766, 767.</texto_principal>'
    g = group(raw, 'Æthiopia, II', '766, 767')
    p = repair_roman_owner(raw, g)
    assert p['status'] == 'restored_candidate'
    assert p['corrected_owner_raw'] == 'Æthiopia'
    assert p['expanded_reference_raw'] == 'II, 766, 767'
    for field, span in [('corrected_owner_raw', 'corrected_owner_span'),
                        ('expanded_reference_raw', 'expanded_reference_span')]:
        a, b = p[span]
        assert raw[a:b] == p[field]
    assert p['namespace_status'] == 'uninterpreted'
    assert p['hypotheses'][0]['owner_raw'] == g['owner_raw']


def test_whitespace_normalization_keeps_exact_source_offsets():
    raw = '<bloco tipo="texto_principal">Quid\n  significat, II,\n  515.</bloco>'
    p = repair_roman_owner(raw, group(raw, 'Quid significat, II', '515'))
    assert p['status'] == 'restored_candidate'
    assert p['corrected_owner_raw'] == 'Quid\n  significat'
    assert p['expanded_reference_raw'] == 'II,\n  515'


@pytest.mark.parametrize('text,owner,warning', [
    ('Henry IV, 25', 'Henry IV', 'roman_may_belong_to_owner_missing_left_separator'),
    ('Joramus successor I, 118', 'Joramus successor I', 'roman_may_belong_to_owner_missing_left_separator'),
    ('Quid, I, 109; II, 522', 'Quid, I, 109; II', 'owner_contains_previous_numeric_references'),
    ('Æthiopia, II 766', 'Æthiopia, II', 'roman_to_number_separator_missing'),
])
def test_ambiguity_preserves_both_hypotheses(text, owner, warning):
    ref = text.split()[-1]
    p = repair_roman_owner(text, group(text, owner, ref))
    assert p['status'] == 'ambiguous_roman_owner'
    assert warning in p['warnings']
    assert len(p['hypotheses']) == 2


def test_does_not_borrow_owner_from_earlier_occurrence():
    raw = 'Æthiopia, II, 766. Other, 766.'
    p = repair_roman_owner(raw, group(raw, 'Æthiopia, II', '766', occurrence=1))
    assert p['status'] == 'evidence_mismatch'
    assert p['expanded_reference_span'] is None


def test_rejects_cross_block_owner():
    raw = '<texto_principal>Æthiopia, II, </texto_principal><nota>766</nota>'
    p = repair_roman_owner(raw, group(raw, 'Æthiopia, II', '766'))
    assert p['status'] == 'evidence_mismatch'


def test_rejects_changed_reference_without_blocking_other_source_changes():
    raw = 'Æthiopia, II, 766.'
    g = group(raw, 'Æthiopia, II', '766')
    p = repair_roman_owner(raw.replace('766', '498'), g)
    assert p['status'] == 'evidence_mismatch'
    p = repair_roman_owner(raw+' Extra text.', g)
    assert p['status'] == 'restored_candidate'
    assert 'source_changed_but_local_spans_match' in p['warnings']


def test_no_noncanonical_roman_or_missing_lexical_owner():
    for raw, owner in [('Word, IIII, 21', 'Word, IIII'), ('II, 21', 'II')]:
        p = repair_roman_owner(raw, group(raw, owner, '21'))
        assert p['status'] == 'not_applicable'


def test_block_tag_cannot_be_part_of_owner():
    raw = '<texto_principal>Æthiopia, II, 766.</texto_principal>'
    p = repair_roman_owner(raw, group(raw, '<texto_principal>Æthiopia, II', '766'))
    assert p['status'] == 'evidence_mismatch'


def test_owner_punctuation_and_word_boundaries_must_match():
    raw = 'Name, II, 766'
    p = repair_roman_owner(raw, group(raw, 'Name, II;', '766'))
    assert p['status'] == 'evidence_mismatch'
    p = repair_roman_owner(raw, group(raw, 'Name, II,', '766'))
    assert p['status'] == 'restored_candidate'
    a, b = p['owner_span']
    assert raw[a:b] == 'Name, II,'
    raw = 'HenryII, 25'
    p = repair_roman_owner(raw, group(raw, 'Henry II', '25'))
    assert p['status'] == 'evidence_mismatch'


def test_no_input_mutation_and_no_roman_namespace_conversion():
    raw = 'Name, II, 23'
    g = group(raw, 'Name, II', '23')
    original = g.copy()
    p = repair_roman_owner(raw, g)
    assert g == original
    assert 'volume' not in p and 'destination' not in p
    assert p['roman_raw'] == 'II'
