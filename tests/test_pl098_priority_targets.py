from pathlib import Path

from tools.indexing import pl098_priority_targets as p


def entry(raw='10', section='259e710d3f1ab9578674a55e', physical=223):
    return dict(volume='PL098', section_id=section,
                source=f'/corpus/PL098/text/id-{physical}.txt',
                reference_namespace='internal_text_number', reference_raw=raw,
                reference_span=[1, 3])


def mapping():
    partitions = {}
    for tome, base in [('I', 100), ('II', 300)]:
        pages = [dict(file=f'/corpus/{i}.txt', physical=i, volume='PL098')
                 for i in range(base, base+3)]
        anchors = [dict(number=10, **pages[0], span=[10, 12], raw='10'),
                   dict(number=12, **pages[2], span=[10, 12], raw='12')]
        partitions[tome] = dict(pages=pages, anchors=anchors,
                                numbers={'10': [anchors[0]], '12': [anchors[1]]})
    return dict(partitions=partitions, namespace='cenni_tome_internal_number',
                notices={'I': {'raw': 'notice I'}, 'II': {'raw': 'notice II'}})


def test_exact_marker_uses_section_namespace():
    assert [c['physical'] for c in p.locate(entry(), mapping())] == [100]
    e = entry(section='239c5e711775e66e16e76303', physical=441)
    assert [c['physical'] for c in p.locate(e, mapping())] == [300]


def test_bounded_missing_marker_keeps_every_physical_file():
    out = p.locate(entry('11'), mapping())
    assert [c['physical'] for c in out] == [100, 101, 102]
    assert all('marker_missing_or_corrupt' in c['uncertainties'] for c in out)


def test_no_extrapolation_past_supported_sequence():
    assert not p.locate(entry('13'), mapping())
    assert p.diagnose(entry('10,13'), mapping())['unmatched'] == [13]


def test_no_other_section_or_wrong_namespace():
    assert not p.locate(entry(section='other'), mapping())
    assert not p.locate(entry(physical=500), mapping())
    e = entry(); e['reference_namespace'] = 'printed_column'
    assert not p.locate(e, mapping())


def test_no_ibid_guess_and_no_open_ended_claim():
    assert not p.locate(entry('ibid'), mapping())
    diagnostic = p.diagnose(entry('10 seqq., ibid.'), mapping())
    assert diagnostic['locator_count'] == 3
    assert diagnostic['located_locator_count'] == 1


def test_multiple_references_preserved_without_duplicate_page():
    out = p.locate(entry('10, 12, 10'), mapping())
    assert [c['physical'] for c in out] == [100, 102]
    assert p.diagnose(entry('10,12'), mapping())['located_locator_count'] == 2


def test_build_boundary_excludes_preliminary_numbers_and_index(monkeypatch, tmp_path):
    folder = tmp_path / 'teste/PL098/text'; folder.mkdir(parents=True)
    # A long fake sequence before the body must not contaminate its map.
    before = ' '.join(str(n) for n in range(200, 240))
    body = ' '.join(f'Verbum {n} ratio' for n in range(1, 20))
    raw = f'<bloco tipo="texto_principal">{before}\nBODY\n{body}\n</bloco>\nINDEX\nNOTICE\n'
    path = folder / 'id-1.txt'; path.write_text(raw)
    monkeypatch.setattr(p, 'SCOPES', [('I', 1, 1, 'BODY', 'NOTICE')])
    result = p.build_map(tmp_path)
    assert set(result['partitions']['I']['numbers']) == {str(n) for n in range(1, 20)}
    for a in result['partitions']['I']['anchors']:
        assert raw[slice(*a['span'])] == a['raw']


def test_quote_evidence_reanchors_unrelated_edits():
    proof = p.quote_evidence(Path('x'), 'prefix NEW notice suffix', 'notice')
    assert proof['span'] == [11, 17]
