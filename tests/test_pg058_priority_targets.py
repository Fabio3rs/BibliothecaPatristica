from tools.indexing import pg058_priority_targets as parser


def anchor(number, order, physical=None, volume='PG057', span=(0, 3)):
    return dict(number=number, order=order, physical=physical or order+1,
                volume=volume, file=f'/{volume}/{order}.txt', span=list(span), raw=f'[{number}]')


def mapping(anchors, size=7):
    pages = [dict(file=f'/PG057/{i}.txt', order=i, physical=i+1, volume='PG057',
                  greek_main_text=i%2 == 0) for i in range(size)]
    result = dict(pages=pages, anchors=anchors, numbers={}, notice={'raw': 'inter uncos'})
    for a in anchors:
        result['numbers'].setdefault(str(a['number']), []).append(a)
    result['numeric_candidates'] = {str(i): parser._candidates(i, result) for i in range(1, 846)}
    return result


def test_dominant_chain_rejects_digits_drifting_into_other_page_namespace():
    good = [anchor(n, n-1) for n in range(1, 20)]
    bad = anchor(157, 7, span=(1, 6))
    accepted, rejected = parser.supported_sequence(good+[bad])
    assert [a['number'] for a in accepted] == list(range(1, 20))
    assert [a['number'] for a in rejected] == [157]


def test_repeated_markers_near_chain_remain_alternatives():
    good = [anchor(n, n-1) for n in range(1, 20)]
    duplicate = anchor(10, 10, span=(1, 4))
    accepted, rejected = parser.supported_sequence(good+[duplicate])
    assert sum(a['number'] == 10 for a in accepted) == 2
    assert not rejected


def test_missing_marker_keeps_every_file_inside_bounded_interval():
    m = mapping([anchor(10, 0), anchor(12, 4)])
    candidates = parser._candidates(11, m)
    assert {c['physical'] for c in candidates} == {1, 2, 3, 4, 5}
    assert all('bracket_marker_missing_or_corrupt' in c['uncertainties'] for c in candidates)
    assert candidates[1]['score'] < candidates[0]['score']
    assert not parser._candidates(13, m)


def test_literal_page_and_continuation_are_deduplicated_by_file():
    m = mapping([anchor(10, 0), anchor(11, 2)])
    entry = dict(volume='PG058', physical=394, reference_raw='10 c. m.', owner_raw='25 elsewhere')
    cs = parser.locate(entry, m)
    assert len(cs) == 3
    assert cs[0]['physical'] == 1 and cs[0]['score'] == .91
    assert parser.diagnose(entry, m)['located_locator_count'] == 1
    assert parser.reference_locators(entry)[0] == [10]


def test_unresolved_ibid_is_not_silently_inherited():
    m = mapping([anchor(10, 0), anchor(11, 2)])
    entry = dict(volume='PG058', physical=394, reference_raw='ibid.')
    assert not parser.locate(entry, m)
    assert 'ibid_context_not_reconstructed' in parser.diagnose(entry, m)['reasons']


def test_other_volume_or_page_is_ineligible():
    assert not parser.eligible(dict(volume='PG057', physical=394))
    assert not parser.eligible(dict(volume='PG058', physical=393))
    assert not parser.eligible(dict(volume='PG058', physical=423))
    assert parser.eligible(dict(volume='PG058', physical=422))


def test_gap_too_wide_does_not_create_interpolation():
    m = mapping([anchor(10, 0), anchor(12, 20)], size=21)
    assert not parser._candidates(11, m)


def test_reference_outside_reviewed_numbering_stays_pending():
    m = mapping([anchor(10, 0), anchor(11, 2)])
    entry = dict(volume='PG058', physical=394, reference_raw='856 a. m.')
    d = parser.diagnose(entry, m)
    assert d['numbers_without_anchor'] == [856]
    assert d['located_locator_count'] == 0


def test_final_page_continues_to_bounded_closing_file():
    m = mapping([anchor(845, 4)], size=7)
    candidates = parser._candidates(845, m)
    assert {c['physical'] for c in candidates} == {5, 7}


def test_not_before_next_qualified_page_does_not_turn_page_into_note_number():
    # All current occurrences of `not. N` continue with a page-position qualifier.
    entry = dict(reference_raw='608 pr. f. not. 624 pr. f.')
    assert parser.reference_locators(entry) == ([608, 624], [])


def test_irregular_position_qualifier_still_preserves_printed_page():
    assert parser.reference_locators(dict(reference_raw='127 m. init.')) == ([127], [])
