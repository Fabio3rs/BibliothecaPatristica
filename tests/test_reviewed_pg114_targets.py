from pathlib import Path

import pytest

from tools.indexing.reviewed_pg114_targets import build_map, locate


def _xml(text, kind='texto_principal'):
    return f'<pagina><bloco tipo="{kind}">{text}</bloco></pagina>'


def _map(tmp_path):
    folder = tmp_path/'teste/PG114/text'
    folder.mkdir(parents=True)
    for physical, text in [(15, 'SCRIPTORUM SYMEONIS METAPHRASTÆ [98] Alpha.'),
                           (16, '[99] Beta.'), (17, '[101] Delta.'),
                           (18, 'CERTI AUCTORES GRÆCI, [999] not a work marker')]:
        (folder/f'source-{physical:03}.txt').write_text(_xml(text))
    return build_map(tmp_path)


def _entry(reference, saints=False):
    return dict(volume='PG114', reference_raw=reference,
                reference_namespace='bracketed_text_number' + ('_with_secondary_locator' if saints else ''))


def test_literal_marker_and_following_file_are_separate_evidence(tmp_path):
    mapping = _map(tmp_path)
    candidates = locate(_entry('98'), mapping)
    assert [c['physical'] for c in candidates] == [15, 16]
    assert candidates[0]['score'] > candidates[1]['score']
    assert candidates[0]['evidence'][0]['raw'] == '[98]'
    assert '999' not in mapping['numbers']


def test_missing_marker_bounded_without_extrapolation(tmp_path):
    mapping = _map(tmp_path)
    candidates = locate(_entry('100'), mapping)
    assert {c['physical'] for c in candidates} == {16, 17}
    assert all('marker_missing_or_corrupt' in c['uncertainties'] for c in candidates)
    assert locate(_entry('180'), mapping) == []
    assert locate(_entry('23'), mapping) == []


def test_secondary_numbers_retained_as_lower_score_alternatives(tmp_path):
    mapping = _map(tmp_path)
    marker = dict(mapping['anchors'][0], number=24)
    mapping['anchors'].append(marker)
    mapping['numbers']['24'] = [marker]
    candidates = locate(_entry('99, 24', saints=True), mapping)
    assert candidates[0]['physical'] == 16
    assert candidates[-1]['physical'] == 15
    assert 'possible_secondary_locator_alternative' in candidates[-1]['uncertainties']


def test_ibid_evidence_and_namespace_isolation(tmp_path):
    mapping = _map(tmp_path)
    entry = _entry('ibid.')
    entry['inherited_reference_evidence'] = {'raw': '99', 'source': 'index', 'span': [1, 3]}
    assert locate(entry, mapping)[0]['physical'] == 16
    entry['volume'] = 'PG115'
    assert locate(entry, mapping) == []
    entry['volume'] = 'PG114'
    entry['reference_namespace'] = 'pg_column'
    assert locate(entry, mapping) == []


def test_ambiguous_boundary_fails_and_correction_rebuilds(tmp_path):
    mapping = _map(tmp_path)
    p = Path(mapping['pages'][1]['file'])
    old_hash = mapping['pages'][1]['sha256']
    p.write_text(p.read_text().replace('[99]', '[100]'))
    rebuilt = build_map(tmp_path)
    assert '99' not in rebuilt['numbers']
    assert rebuilt['pages'][1]['sha256'] != old_hash
    p.write_text(p.read_text() + _xml('CERTI AUCTORES GRÆCI,'))
    with pytest.raises(ValueError):
        build_map(tmp_path)


def test_current_corpus_replay_and_target_files_exist():
    root = Path(__file__).resolve().parents[1]
    mapping = build_map(root)
    assert mapping == build_map(root)
    candidates = locate(_entry('99'), mapping)
    assert candidates[0]['physical'] == 53
    for candidate in candidates:
        assert Path(candidate['file']).is_file()
        for evidence in candidate['evidence']:
            if evidence['kind'] == 'literal_bracket_marker':
                text = Path(evidence['file']).read_text()
                a, b = evidence['span']
                assert text[a:b] == evidence['raw']


def test_ibid_mixed_keeps_explicit_numbers_and_reports_pending(tmp_path):
    from tools.indexing.reviewed_pg114_targets import diagnose
    mapping = _map(tmp_path)
    entry = _entry('ibid., 101, 180 et seq.')
    entry['inherited_reference_evidence'] = {'raw': '98', 'source': 'index', 'span': [1, 3]}
    assert {c['physical'] for c in locate(entry, mapping)} == {15, 16, 17}
    report = diagnose(entry, mapping)
    assert report['numbers_without_anchor'] == [180]
    assert report['status'] == 'candidate'
    assert 'following_extent_not_fully_determined' in report['reasons']
    assert diagnose(_entry('ibid'), mapping)['reasons'] == ['ibid_without_prior_reference_evidence']
