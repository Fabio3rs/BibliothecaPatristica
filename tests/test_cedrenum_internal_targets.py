from pathlib import Path

from tools.indexing.cedrenum_internal_targets import (
    diagnose, locate, numeric_candidates, numeric_occurrences,
    reference_locators, sequence_anchors,
)


def anchor(n, page, at=0):
    return dict(number=n, file=f'/corpus/PG121/text/page-{page}.txt', physical=page,
                volume='PG121', span=[at, at+len(str(n))], raw=str(n))


def mapping(anchors):
    numbers = {}
    for a in anchors:
        numbers.setdefault(str(a['number']), []).append(a)
    return dict(namespace='internal_text_number_with_roman_namespace', notice={},
                partitions={'I': dict(anchors=anchors, numbers=numbers,
                    pages=[dict(file=f'/corpus/PG121/text/page-{n}.txt', physical=n, volume='PG121')
                           for n in range(1, 7)])})


def entry(raw, **kwargs):
    return dict(section_id='PG122:index-in-georgium-cedrenum', reference_raw=raw, **kwargs)


def test_scanner_ignores_headers_parallel_pagination_and_notes():
    raw = '''<bloco tipo="cabecalho">123</bloco>
    <bloco tipo="texto_principal">Historia regis 22 litteris narrata [P. 23] (24) in urbe.</bloco>
    <bloco tipo="nota">25 imperator</bloco>
    <bloco tipo="texto_principal">ἀρχὴ τῆς πόλεως 26 καὶ τοῦ βασιλέως</bloco>'''
    found = numeric_occurrences(raw, file=Path('/tmp/a.txt'), physical=1, volume='PG121')
    assert [a['number'] for a in found] == [22]
    assert all(raw[s['span'][0]:s['span'][1]] == s['raw'] for s in found)
    assert numeric_occurrences(raw, file=Path('/tmp/a.txt'), physical=1, volume='PG121', cutoff=0) == []


def test_dominant_chain_rejects_incidental_repeated_numerals_and_dates():
    rows = [anchor(n, n) for n in range(1, 31)]
    rows += [anchor(n, n+50) for n in range(1, 19)]
    rows += [anchor(n+6500, n+50, 20) for n in range(1, 19)]
    found = sequence_anchors(rows)
    assert [(x['number'], x['physical']) for x in found] == [(n, n) for n in range(1, 31)]


def test_short_chain_is_not_a_numbering_system():
    assert sequence_anchors([anchor(n, n) for n in range(1, 10)]) == []


def test_direct_marker_keeps_continuation_page_and_bounds_missing_number():
    m = mapping([anchor(20, 2), anchor(21, 3), anchor(23, 4)])
    assert {c['physical'] for c in numeric_candidates('I', 20, m)} == {2, 3}
    assert {c['physical'] for c in numeric_candidates('I', 22, m)} == {3, 4}
    assert numeric_candidates('I', 19, m) == []
    assert numeric_candidates('II', 20, m) == []


def test_no_interpolation_over_large_gaps_or_reversed_anchor_order():
    assert not numeric_candidates('I', 22, mapping([anchor(10, 1), anchor(30, 2)]))
    assert not numeric_candidates('I', 22, mapping([anchor(21, 4), anchor(23, 1)]))
    assert not numeric_candidates('I', 22, mapping([anchor(21, 3, 40), anchor(23, 3, 10)]))


def test_multiple_namespaces_and_chained_ibid_with_explicit_provenance():
    assert reference_locators(entry('I, 20, 21; II, 435'))[0] == [('I', 20), ('I', 21), ('II', 435)]
    evidence = dict(raw='ibid.', inherited_reference_evidence=dict(raw='I, 20, 21', source='original', span=[1, 10]))
    refs, warnings = reference_locators(entry('ibid. 7', inherited_reference_evidence=evidence))
    assert refs == [('I', 20), ('I', 21)]
    assert 'ibid_additional_number_not_interpreted' in warnings
    assert not reference_locators(entry('ibid.'))[0]
    assert reference_locators(entry('20', inherited_namespace_evidence=dict(raw='I', source='previous', span=[2, 3])))[0] == [('I', 20)]


def test_partial_reference_coverage_not_claimed_as_complete():
    e = entry('I, 20; II, 47')
    m = mapping([anchor(20, 2), anchor(21, 3)])
    result = locate(e, m)
    assert len(result) == 2
    assert all('roman_partition_is_editorial_candidate_not_certified' in c['uncertainties'] for c in result)
    d = diagnose(e, m)
    assert d['locator_count'] == 2
    assert d['located_locator_count'] == 1
    assert d['unmatched_locators'] == [['II', 47]]
    assert d['completeness'] == 'partial_or_missing'
    assert locate(dict(e, section_id='another_index'), m) == []


def test_duplicate_number_alternatives_have_distinct_files():
    m = mapping([anchor(20, 2), anchor(20, 3), anchor(21, 3)])
    result = locate(entry('I, 20, 20'), m)
    assert len(result) == len({c['file'] for c in result}) == 2


def test_ibid_tail_explicit_namespace_and_uninterpreted_atoms_are_preserved():
    previous = dict(raw='I, 20', source='previous', span=[10, 15])
    e = entry('ibid. 7, II, 435', inherited_reference_evidence=previous)
    assert reference_locators(e)[0] == [('I', 20), ('II', 435)]
    d = diagnose(e, mapping([anchor(20, 2), anchor(21, 3)]))
    assert d['locator_count'] == 3
    assert d['located_locator_count'] == 1
    assert ['ibid_additional_uninterpreted', 7] in d['unmatched_locators']
    assert ['II', 435] in d['unmatched_locators']
    assert d['completeness'] == 'partial_or_missing'
    missing = entry('ibid., I, 20')
    assert reference_locators(missing)[0] == [('I', 20)]
    assert diagnose(missing, mapping([anchor(20, 2)]))['locator_count'] == 2


def test_ibid_without_dot_requires_lexical_boundary():
    previous = dict(raw='I, 20', source='previous', span=[10, 15])
    for raw in ('ibid', 'ibidem', 'IBID', 'ibidem, II, 435'):
        locators, _ = reference_locators(entry(raw, inherited_reference_evidence=previous))
        assert ('I', 20) in locators
    locators, warnings = reference_locators(entry('ibidemque', inherited_reference_evidence=previous))
    assert locators == []
    assert 'inherited_reference_candidate' not in warnings
