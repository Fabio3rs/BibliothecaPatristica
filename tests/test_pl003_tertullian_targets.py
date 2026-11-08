from tools.indexing import pl003_tertullian_targets as p


def entry(raw, physical=832, start=10):
    return dict(volume='PL003', source=f'/corpus/id-{physical}.txt',
                reference_raw=raw, reference_span=[start, start+len(raw)])


def fake_mapping():
    return dict(closing={'source': '/corpus/id-849.txt', 'span': [100, 120]})


def fake_candidates(atom, mapping):
    return ([{'file': 'target'}] if atom['tome'] in {'I', 'II'} and
            atom['number'] in {10, 11, 480, 903} else [])


def test_partial_range_is_not_fully_covered(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    result = p.diagnose(entry('II, 10-14'), fake_mapping())
    assert result['locator_count'] == 5
    assert result['located_locator_count'] == 2
    assert [a['number'] for a in result['unmatched']] == [12, 13, 14]


def test_numeric_tail_is_reported(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    result = p.diagnose(entry('I, 480 A; II 1034 A.'), fake_mapping())
    assert result['locator_count'] == 2
    assert result['located_locator_count'] == 1
    assert result['unparsed'][0]['raw'] == '1034'


def test_atypical_tome_not_reinterpreted(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    result = p.diagnose(entry('III, 517 B'), fake_mapping())
    assert result['locator_count'] == 1
    assert result['located_locator_count'] == 0
    assert result['unmatched'][0]['tome'] == 'III'
    result = p.diagnose(entry('II, 903, C, 735 B.'), fake_mapping())
    assert result['locator_count'] == 2
    assert result['located_locator_count'] == 1
    assert result['unmatched'][0]['tome'] == 'C'


def test_ibid_residue_and_following_are_pending(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    result = p.diagnose(entry('II, 10 sq.; text ibid.'), fake_mapping())
    assert result['locator_count'] == 3
    assert result['located_locator_count'] == 1
    assert {x['reason'] for x in result['unparsed']} == {
        'unparsed_locator_material', 'following_extent_not_expanded'}


def test_limits_and_closing_apply_to_diagnostics(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    for e in [entry('II, 10', 806), entry('II, 10', 850), entry('II, 10', 849, 150)]:
        result = p.diagnose(e, fake_mapping())
        assert result['reason'] == 'outside_reviewed_section'
        assert result['located_locator_count'] == 0


def test_supported_list_and_note_qualifier(monkeypatch):
    monkeypatch.setattr(p, '_candidates', fake_candidates)
    result = p.diagnose(entry('I, 10, 11 not. 2; II, 480'), fake_mapping())
    assert result['locator_count'] == 3
    assert result['located_locator_count'] == 3


def test_legacy_reference_does_not_fabricate_owner_or_reanchor_numerals():
    legacy = dict(ref_raw='I, 12', source_span=[6, 11], source_sha256='old')
    current, status = p.current_reference(legacy, 'Owner I, 12 new footer')
    assert status == 'reference_span_current_owner_unverified'
    assert 'owner_span' not in current
    assert current['reference_span'] == [6, 11]
    assert p.current_reference(legacy, 'new Owner I, 12')[0] is None
    assert p.current_reference(legacy, 'Owner I, 13')[0] is None


def test_header_pair_sequence_and_bounded_gap(tmp_path):
    files = {}
    for physical, head in [(100, '387 TERTULLIANI 388'), (101, '389 TERTULLIANI 390'),
                           (102, 'TERTULLIANI'), (103, '393 TERTULLIANI 394')]:
        path = tmp_path / f'p-{physical}.txt'
        path.write_text(f'<bloco tipo="cabecalho">{head}</bloco><bloco tipo="texto_principal">body</bloco>')
        files[physical] = [path]
    mapping = dict(tomes={'I': p.column_series(files)})
    direct = p._candidates(dict(tome='I', number=387, range_end=None), mapping)
    assert {c['physical'] for c in direct} == {100}
    gap = p._candidates(dict(tome='I', number=392, range_end=None), mapping)
    assert {c['physical'] for c in gap} == {102}
    assert all('target_header_not_read_directly' in c['uncertainties'] for c in gap)
    assert p._candidates(dict(tome='III', number=387, range_end=None), mapping) == []


def test_numbers_in_opening_notice_not_routed():
    mapping = fake_mapping()
    mapping['notice'] = dict(source='/corpus/id-807.txt', span=[0, 400])
    assert not p.within_body(entry('II, 10', 807, 20), mapping)
    assert p.within_body(entry('II, 10', 807, 450), mapping)
