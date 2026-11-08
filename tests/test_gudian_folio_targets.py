from pathlib import Path
import pytest
from tools.indexing.gudian_folio_targets import (
    build_map, locate, diagnose, OPENING, CLOSING, NOTICE, MARKER,
)


def page(root, n, body, extra=''):
    folder = root / 'teste/PG091/text'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'test-{n}.txt'
    target.write_text(f'<bloco tipo="texto_principal">{body}</bloco>{extra}')
    return target


def sample(root):
    page(root, 1, OPENING + ' (110 a) λογος')
    page(root, 2, 'λογος (110 b) συνεχεια')
    page(root, 3, 'κειμενον')
    page(root, 4, '(112 a) λογος ' + CLOSING + ' (900 b)')
    page(root, 5, NOTICE + ' (110 a) INDEX')
    return build_map(root)


def entry(*atoms):
    return {'volume': 'PG091', 'reference_namespace': 'gudian_folio_side',
            'locator_atoms': [{'number': n, 'side': s, 'raw': f'{n} {s}'} for n, s in atoms]}


def test_direct_and_passage_continuation(tmp_path):
    mapping = sample(tmp_path)
    found = locate(entry((110, 'b')), mapping)
    assert {x['physical'] for x in found} == {2, 3, 4}
    assert found[0]['physical'] == 2 and found[0]['score'] > found[1]['score']
    assert any(x['evidence'][0]['kind'] == 'gudian_passage_before_next_marker' for x in found)


def test_missing_side_bracketed_interval(tmp_path):
    mapping = sample(tmp_path)
    found = locate(entry((111, 'a')), mapping)
    assert {x['physical'] for x in found} == {2, 3, 4}
    assert all('bounded_page_interval' in x['uncertainties'] for x in found)


def test_no_extrapolation_or_namespace_guess(tmp_path):
    mapping = sample(tmp_path)
    assert not locate(entry((99, 'a')), mapping)
    bad = entry((110, 'a')); bad['reference_namespace'] = 'pg_column'
    assert not locate(bad, mapping)
    assert diagnose(entry((99, 'a')), mapping)['located_locator_count'] == 0


def test_multiple_atoms_completeness_and_file_dedup(tmp_path):
    mapping = sample(tmp_path)
    found = locate(entry((110, 'a'), (110, 'b'), (900, 'b')), mapping)
    assert len({x['file'] for x in found}) == len(found)
    report = diagnose(entry((110, 'a'), (900, 'b')), mapping)
    assert report['locator_count'] == 2
    assert report['located_locator_count'] == 1
    assert report['completeness'] == 'partial_or_unlocated'
    assert '900b' not in mapping['numbers']  # after work closing
    assert all(a['physical'] != 5 for a in mapping['anchors'])  # index excluded


def test_wrappers_not_footnote_anchors(tmp_path):
    page(tmp_path, 1, OPENING + ' (110 a)', '<bloco tipo="rodape">(180 b)</bloco>')
    page(tmp_path, 2, '(110 b)' + CLOSING)
    page(tmp_path, 3, NOTICE)
    mapping = build_map(tmp_path)
    assert set(mapping['numbers']) == {'110a', '110b'}
    for anchor in mapping['anchors']:
        assert Path(anchor['file']).read_text()[slice(*anchor['span'])] == anchor['raw']


def test_marker_typography_variants(tmp_path):
    page(tmp_path, 1, OPENING + ' (104 b **) (105 α) [105 β] (106 a.)')
    page(tmp_path, 2, CLOSING)
    page(tmp_path, 3, NOTICE)
    mapping = build_map(tmp_path)
    assert set(mapping['numbers']) == {'104b', '105a', '105b', '106a'}
    assert mapping['numbers']['105a'][0]['side_raw'] == 'α'
    assert not MARKER.search('(105 A)')
    assert not MARKER.search('(105 a commentary)')
    assert not MARKER.search('(105)')


def test_duplicate_scan_runs_preserved_and_not_crossed(tmp_path):
    page(tmp_path, 1, OPENING + '(110 a)')
    page(tmp_path, 2, '(110 b)')
    page(tmp_path, 3, OPENING + '(110 a)')
    page(tmp_path, 4, '(110 b) ' + CLOSING)
    page(tmp_path, 5, NOTICE)
    mapping = build_map(tmp_path)
    found = locate(entry((110, 'a')), mapping)
    assert {x['physical'] for x in found} == {1, 2, 3, 4}
    for candidate in found:
        for proof in candidate['evidence']:
            if proof['kind'] == 'gudian_passage_before_next_marker':
                assert proof['left']['run'] == proof['right']['run']


def test_isolated_wrong_numeral_low_rank_not_interval_anchor(tmp_path):
    page(tmp_path, 1, OPENING + '(110 a)')
    page(tmp_path, 2, '(450 b)')
    page(tmp_path, 3, '(111 b)' + CLOSING)
    page(tmp_path, 4, NOTICE)
    mapping = build_map(tmp_path)
    wrong = mapping['numbers']['450b'][0]
    assert not wrong['sequence_supported']
    found = locate(entry((450, 'b')), mapping)
    assert len(found) == 1 and found[0]['score'] == .35
    assert 'numeral_outside_local_sequence' in found[0]['uncertainties']
    assert not locate(entry((449, 'a')), mapping)


def test_boundary_evidence_required(tmp_path):
    page(tmp_path, 1, OPENING + '(110 a)')
    page(tmp_path, 2, CLOSING)
    with pytest.raises(ValueError, match='notice'):
        build_map(tmp_path)


def test_real_pg091_markers_and_missing_119b():
    root = Path(__file__).resolve().parents[1]
    if not (root / 'teste/PG091/text').exists():
        pytest.skip('Corpus not available')
    mapping = build_map(root)
    found = locate(entry((119, 'b')), mapping)
    assert 589 in {x['physical'] for x in found}
    found = locate(entry((120, 'a')), mapping)
    assert found[0]['physical'] == 590
    found = locate(entry((213, 'b')), mapping)
    assert {698, 699}.issubset({x['physical'] for x in found})


def test_tail_not_extrapolated_into_preface_before_repeated_opening(tmp_path):
    page(tmp_path, 1, OPENING + '(110 a)')
    page(tmp_path, 2, '(110 b)')
    page(tmp_path, 3, 'PRAEFATIO')
    page(tmp_path, 4, 'PRAEFATIO continua')
    page(tmp_path, 5, OPENING + '(110 a)')
    page(tmp_path, 6, '(110 b)' + CLOSING)
    page(tmp_path, 7, NOTICE)
    found = locate(entry((110, 'b')), build_map(tmp_path))
    assert {x['physical'] for x in found} == {2, 6}
