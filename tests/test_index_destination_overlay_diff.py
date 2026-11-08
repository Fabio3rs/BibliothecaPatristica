from scripts.compare_index_destination_overlays import compare


def record(files, lexical=False):
    return {'files': files, 'lexical': lexical, 'evidence': {}}


def test_diff_separates_availability_ranking_and_lexical_support():
    old = {'a': record(['x', 'y']), 'b': record([]), 'c': record(['z'], True)}
    new = {'a': record(['y', 'x'], True), 'b': record(['b']), 'c': record([])}
    diff = compare(old, new)
    assert diff['counts'] == {'retained': 1, 'top_candidate_changed': 1,
                             'gained_lexical_support': 1, 'lost_lexical_support': 1,
                             'gained': 1, 'lost': 1}
    assert len(diff['changes']) == 3


def test_absent_records_are_not_silently_ignored():
    diff = compare({'old': record(['x'])}, {'new': record(['y'])})
    assert diff['counts']['gained'] == 1
    assert diff['counts']['lost'] == 1
