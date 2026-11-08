import hashlib
import json
from pathlib import Path

import pytest

from tools.indexing.pl_section_map_experiment import bounded_text, build_map, unique_span


def fixture_profile(tmp_path):
    root = tmp_path/'teste'
    text = root/'PL001/text'
    images = root/'PL001/images'
    text.mkdir(parents=True)
    images.mkdir()
    anchors = []
    for physical in range(1, 5):
        image = images/f'PL001-{physical:03}.png'
        image.write_bytes(b'image-fixture')
        nums = [physical*2-1,physical*2]
        body = ' '.join(f'MARK {n} end' for n in nums)
        raw = '<pagina><bloco tipo="rodape">HEADER 999</bloco><bloco tipo="texto_principal">'+body+'</bloco></pagina>'
        path = text/f'fixture-{physical:03}.txt'
        path.write_text(raw)
        for n in nums:
            anchors.append({'number':n,'physical':physical,'quote':f'MARK {n} end',
                            'image_name':image.name,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                            'ocr_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return root, {'id':'test','volume':'PL001','target':{'physical_start':1,'physical_end':4,'number_start':1,'number_end':8},'anchors':anchors}


def test_refuses_ambiguous_quote_and_reversed_region():
    with pytest.raises(ValueError):
        unique_span('x x','x')
    with pytest.raises(ValueError):
        bounded_text('start end',{'start_quote':'end','end_quote':'start'})


def test_anchor_spans_and_cross_file_window(tmp_path):
    root, profile = fixture_profile(tmp_path)
    records, series, anchors, pending = build_map(profile,root)
    assert not pending
    assert len(anchors)==8
    assert [x['physical'] for x in records[1]['files']]==[1,2]
    for page in series['pages'].values():
        raw = Path(page['file']).read_text()
        for occurrences in page['occurrences'].values():
            for marker in occurrences:
                assert raw[marker['char_start']:marker['char_end']]==marker['raw']
    assert all(not r['verified'] and not r['import_ready'] for r in records)


def test_missing_reviewed_marker_cannot_use_other_equal_number(tmp_path):
    root, profile = fixture_profile(tmp_path)
    profile['anchors'][2]['quote']='MISSING 3 end'
    records, _, _, pending = build_map(profile,root)
    assert len(pending)==1
    assert records[1]['status']=='reviewed_boundary_missing_from_ocr'
    assert records[2]['files']==[]


def test_image_change_blocks_review_reuse(tmp_path):
    root, profile = fixture_profile(tmp_path)
    (root/'PL001/images/PL001-001.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='image changed'):
        build_map(profile,root)


def test_ocr_change_reanchors_same_unique_quote(tmp_path):
    root, profile = fixture_profile(tmp_path)
    p=root/'PL001/text/fixture-001.txt'
    p.write_text(p.read_text().replace('HEADER','NEW HEADER'))
    _, _, anchors, _ = build_map(profile,root)
    assert all(a['ocr_changed'] for a in anchors if a['physical']==1)


def test_explicit_footer_region_and_internal_boundary(tmp_path):
    root, profile = fixture_profile(tmp_path)
    p=root/'PL001/text/fixture-001.txt'
    p.write_text(p.read_text().replace('tipo="texto_principal"','tipo="rodape"'))
    profile['target']['block_types']={'1':['rodape']}
    profile['target']['bounds']={'1':{'start_quote':'MARK 1 end','end_quote':'MARK 2 end'}}
    _, series, _, pending = build_map(profile,root)
    assert 1 in series['pages'][1]['occurrences']
    assert 2 not in series['pages'][1]['occurrences']
    assert any(a['number']==2 for a in pending)


def test_last_requested_number_keeps_next_boundary(tmp_path):
    root, profile = fixture_profile(tmp_path)
    profile['target']['number_end'] = 7
    records, _, _, pending = build_map(profile, root)
    assert not pending
    assert records[-1]['number'] == 7
    assert records[-1]['status'] == 'bounded_window_candidate'


def test_explicit_editorial_order_preserves_reversed_physical_files(tmp_path):
    root, profile = fixture_profile(tmp_path)
    folder = root/'PL001/text'
    a, b = folder/'fixture-001.txt', folder/'fixture-002.txt'
    first, second = a.read_text(), b.read_text()
    a.write_text(second)
    b.write_text(first)
    order = [2, 1, 3, 4]
    proofs = []
    for rank, physical in enumerate(order):
        path = folder/f'fixture-{physical:03}.txt'
        quote = f'{rank*2+1} HEADER {rank*2+2}'
        path.write_text(path.read_text().replace('<pagina>', f'<pagina><cabecalho>{quote}</cabecalho>'))
        image = root/f'PL001/images/PL001-{physical:03}.png'
        proofs.append({'physical': physical, 'header_quote': quote,
                       'editorial_start': rank*2+1, 'editorial_end': rank*2+2,
                       'image_name': image.name, 'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest()})
    for anchor in profile['anchors']:
        if anchor['physical'] in (1, 2):
            anchor['physical'] = 3 - anchor['physical']
            anchor['image_name'] = f"PL001-{anchor['physical']:03}.png"
    profile['target']['reading_order'] = proofs
    records, _, _, pending = build_map(profile, root)
    assert not pending
    assert [f['physical'] for f in records[1]['files']] == [2, 1]
    assert records[1]['file_order_basis'] == 'explicit_editorial_order'
    profile['target']['reading_order'] = proofs[:-1]
    with pytest.raises(ValueError, match='cover each'):
        build_map(profile, root)
    profile['target']['reading_order'] = proofs
    proofs[1]['editorial_start'] = 9
    with pytest.raises(ValueError, match='consecutive'):
        build_map(profile, root)


def terminal_profile(tmp_path):
    root, profile = fixture_profile(tmp_path)
    path = root/'PL001/text/fixture-004.txt'
    path.write_text(path.read_text().replace('MARK 8 end', 'MARK 8 end NEXT SECTION 99'))
    for anchor in profile['anchors']:
        anchor['visual_review'] = True
    profile['target']['bounds'] = {'4': {'end_quote': 'NEXT SECTION'}}
    profile['target']['section_end'] = {
        **profile['anchors'][-1], 'quote': 'NEXT SECTION',
        'ocr_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return root, profile


def test_terminal_section_uses_literal_not_synthetic_number(tmp_path):
    root, profile = terminal_profile(tmp_path)
    records, _, _, _ = build_map(profile, root)
    last = records[-1]
    assert last['status'] == 'bounded_window_candidate'
    assert [f['physical'] for f in last['files']] == [4]
    assert last['basis'] == 'reviewed_marker_and_section_end'
    assert last['end_boundary_number'] is None
    assert last['end_options'] == []
    proof = last['section_end']
    assert Path(proof['file']).read_text()[proof['char_start']:proof['char_end']] == 'NEXT SECTION'
    assert not last['verified'] and not last['import_ready']


@pytest.mark.parametrize('change', ['image', 'duplicate_quote', 'wrong_bound', 'not_reviewed', 'wrong_number'])
def test_terminal_section_rejects_invalid_proof(tmp_path, change):
    root, profile = terminal_profile(tmp_path)
    proof = profile['target']['section_end']
    path = root/'PL001/text/fixture-004.txt'
    if change == 'image':
        (root/'PL001/images/PL001-004.png').write_bytes(b'changed')
    elif change == 'duplicate_quote':
        path.write_text(path.read_text() + ' NEXT SECTION')
    elif change == 'wrong_bound':
        profile['target']['bounds']['4']['end_quote'] = 'MARK 8 end'
    elif change == 'not_reviewed':
        proof['visual_review'] = False
    else:
        proof['number'] = 7
    with pytest.raises(ValueError):
        build_map(profile, root)


def test_terminal_section_requires_reviewed_start_and_reanchors_ocr(tmp_path):
    root, profile = terminal_profile(tmp_path)
    path = root/'PL001/text/fixture-004.txt'
    path.write_text(path.read_text().replace('HEADER', 'CHANGED HEADER'))
    records, _, _, _ = build_map(profile, root)
    assert records[-1]['section_end']['ocr_changed']
    profile['anchors'][-1]['quote'] = 'absent 8'
    records, _, _, _ = build_map(profile, root)
    assert records[-1]['files'] == []
    assert records[-1]['status'] == 'reviewed_boundary_missing_from_ocr'


def test_terminal_section_retains_cross_file_continuation(tmp_path):
    root, profile = terminal_profile(tmp_path)
    p3, p4 = (root/f'PL001/text/fixture-{n:03}.txt' for n in (3, 4))
    p3.write_text(p3.read_text().replace('MARK 6 end', 'MARK 6 end MARK 8 end'))
    p4.write_text(p4.read_text().replace('MARK 7 end MARK 8 end', 'Continuation'))
    profile['anchors'] = [a for a in profile['anchors'] if a['number'] != 7]
    profile['anchors'][-1].update(physical=3, image_name='PL001-003.png')
    records, _, _, _ = build_map(profile, root)
    assert [f['physical'] for f in records[-1]['files']] == [3, 4]
    p4.unlink()
    with pytest.raises((ValueError, KeyError)):
        build_map(profile, root)


def test_terminal_section_cannot_hide_next_supported_marker(tmp_path):
    root, profile = terminal_profile(tmp_path)
    path = root/'PL001/text/fixture-004.txt'
    path.write_text(path.read_text().replace('NEXT SECTION', 'MARK 9 end NEXT SECTION'))
    profile['anchors'].append({**profile['anchors'][-1], 'number':9, 'quote':'MARK 9 end'})
    records, _, _, _ = build_map(profile, root)
    assert records[-1]['status'] == 'section_end_conflicts_with_next_marker'
    assert records[-1]['files'] == []
