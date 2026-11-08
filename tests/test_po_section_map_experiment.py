import json
from pathlib import Path

import pytest

from tools.indexing.po_section_map_experiment import check_profile, index_rows, parse_material, run, sha


def test_page_line_does_not_turn_line_into_page():
    parsed = parse_material('82, 8 s.', 'page_line')
    assert parsed == [{'page_start': 82, 'page_end': None, 'page_raw': '82', 'line_raw': '8 s.'}]
    assert parse_material('82, 8, 10', 'page_line') is None
    assert parse_material('82, 0', 'page_line') is None
    assert parse_material('82, 8-4', 'page_line') is None


def test_lists_ranges_and_unrecognized_notation():
    result = parse_material('13-14, 73, 105', 'page_list')
    assert [(r['page_start'], r['page_end']) for r in result] == [(13, 14), (73, None), (105, None)]
    for value in ['14-13', '10-1000', '0', '27b', '82 s.', 'IV, 7', '12345']:
        assert parse_material(value, 'page_list') is None


def test_spans_and_biblical_left_column_are_preserved():
    raw = '<texto_principal>PSALM., CXVIII, 46 . . . . . 79\n— 46 . . . . . 79</texto_principal>'
    rows = list(index_rows(raw, 'page_list'))
    assert len(rows) == 2
    assert all(r['material'][0]['page_start'] == 79 for r in rows)
    assert all(raw[r['source_start']:r['source_end']] == r['line_raw'] for r in rows)
    assert rows[0]['source_start'] != rows[1]['source_start']
    assert rows[1]['owner_context_status'].startswith('raw_fragment')


def fixture(tmp_path):
    root = tmp_path / 'teste'
    folder = root / 'PO999' / 'text'
    folder.mkdir(parents=True)
    for n in range(1, 11):
        (folder / f'PO999-{n:03}.txt').write_text(
            f'<pagina><cabecalho>{n + 100} [ {n} ]</cabecalho><bloco tipo="texto_principal">Body 99</bloco><rodape>[99]</rodape></pagina>')
    (folder / 'PO999-011.txt').write_text('<texto_principal>INDEX\nGenèse I, 1 ..... 3-4, 7</texto_principal>')
    (folder / 'PO999-012.txt').write_text('<cabecalho>NEXT</cabecalho>')
    proofs = []
    for role, n, quote in [('title', 1, 'Body'), ('notice', 1, 'Body'),
                           ('index_opening', 11, 'INDEX'), ('index_end', 11, '7'), ('next_section', 12, 'NEXT')]:
        path = folder / f'PO999-{n:03}.txt'
        proofs.append({'role': role, 'path': str(path.relative_to(root)), 'sha256': sha(path), 'quote': quote})
    profile = {'sections': [{'id': 's', 'volume': 'PO999', 'namespace_status': 'documented_notice',
                            'notation': 'page_list', 'index_physical_scope': [11, 11], 'evidence': proofs,
                            'series': {'id': 'namespace', 'volume': 'PO999', 'physical_start': 1, 'physical_end': 10}}]}
    return root, folder, profile


def test_bounded_map_ranges_and_source_refresh(tmp_path):
    root, folder, profile = fixture(tmp_path)
    # Same printed number in another fascicle must never be included.
    (folder / 'PO999-050.txt').write_text('<pagina><cabecalho>[3]</cabecalho></pagina>')
    original = {p.name: sha(p) for p in folder.iterdir()}
    summary = run(profile, root, tmp_path / 'out1')
    records = [json.loads(x) for x in (tmp_path / 'out1/links.jsonl').read_text().splitlines()]
    assert [[f['physical'] for f in r['files']] for r in records] == [[3, 4], [7]]
    assert summary['material_occurrences'] == 2
    assert {p.name: sha(p) for p in folder.iterdir()} == original
    (folder / 'PO999-003.txt').write_text('<pagina><cabecalho>103 [broken]</cabecalho><rodape>[3]</rodape></pagina>')
    run(profile, root, tmp_path / 'out2')
    records = [json.loads(x) for x in (tmp_path / 'out2/links.jsonl').read_text().splitlines()]
    assert records[0]['missing_numbers'] == [3]
    assert records[0]['link_status'] == 'missing_or_partial_map'
    assert [f['physical'] for f in records[0]['files']] == [4]


def test_reject_stale_scope_evidence(tmp_path):
    root, folder, profile = fixture(tmp_path)
    (folder / 'PO999-012.txt').write_text('<cabecalho>NEW</cabecalho>')
    with pytest.raises(ValueError, match='Stale'):
        check_profile(profile, root)


def test_reject_duplicate_physical_sources(tmp_path):
    root, folder, profile = fixture(tmp_path)
    (folder / 'another-003.txt').write_text('<pagina><cabecalho>[3]</cabecalho></pagina>')
    with pytest.raises(ValueError, match='ambiguous physical'):
        run(profile, root, tmp_path / 'out')


def test_reject_misaligned_scope_evidence(tmp_path):
    root, folder, profile = fixture(tmp_path)
    profile['sections'][0]['index_physical_scope'] = [11, 13]
    with pytest.raises(ValueError, match='configured index boundary'):
        run(profile, root, tmp_path / 'out')


def test_corpus_pilot_preserves_known_destinations_and_line_semantics():
    path = Path('data/index_entry_parser/section_maps_po_001/maps.jsonl')
    if not path.exists():
        pytest.skip('Local experiment artifact not available')
    records = [json.loads(x) for x in path.read_text().splitlines()]
    for volume, number, physical in [('PO015', 61, 239), ('PO015', 82, 260),
                                     ('PO016', 33, 637), ('PO016', 53, 657),
                                     ('PO022', 79, 289), ('PO022', 83, 293)]:
        row = next(r for r in records if r['section_id'].startswith(volume) and r['number'] == number)
        assert [f['physical'] for f in row['files']] == [physical]
        assert row['verified'] is False
