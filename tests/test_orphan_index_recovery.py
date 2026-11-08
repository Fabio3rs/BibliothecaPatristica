import argparse
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing import orphan_index_recovery as recovery
from tools.indexing.editorial_regions_experiment import sha
from tools.indexing.entry_parser_bulk import open_experiment


def block(text, kind='texto_principal'):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def page(title='INDEX RERUM', body='Aaron, 12.\nAbbas, 13.'):
    return block('100 ' + title + ' 101', 'cabecalho') + block(body)


def fixture(tmp_path, texts, seed_pages=(2,), volume='PG001'):
    root = tmp_path / 'teste'
    directory = root / volume / 'text'
    directory.mkdir(parents=True)
    seeds, parsed = [], {}
    for n, raw in texts.items():
        path = directory / f'fixture-{n}.txt'
        path.write_bytes(raw.encode())
        if n not in seed_pages:
            continue
        evidence = recovery.detector.units(raw)
        entries = []
        for row in evidence:
            if row['block_type'] != 'texto_principal':
                continue
            a, b = row['span']
            entries.append({'id': f'{n}:{a}', 'char_start': a, 'char_end': b,
                            'text_sha256': sha(raw[a:b].encode()), 'entry_raw': row['quote']})
        parsed[str(path)] = {'sha256': sha(raw.encode()), 'entries': entries}
        seeds.append({'source': str(path), 'source_sha256': sha(raw.encode()), 'volume': volume,
                      'physical': n, 'candidate_ids': [e['id'] for e in entries], 'candidate_count': len(entries)})
    return root, seeds, parsed


def recover(data, cap=64, volume='PG001'):
    root, seeds, parsed = data
    return recovery.recover_volume(volume, seeds, parsed, root, cap)


def test_expands_matching_headers_and_keeps_opening_unknown(tmp_path):
    result = recover(fixture(tmp_path, {1: page(), 2: page(), 3: page(), 4: block('Ordinary prose.')}))
    assert len(result['sections']) == 1
    section = result['sections'][0]
    assert (section['physical_start'], section['physical_end']) == (1, 3)
    assert section['opening_status'] == 'unknown_first_observed'
    assert all(a['status'] == 'inside_index_candidate' for a in result['associations'])
    assert not section['verified'] and not section['import_ready']


def test_visually_audited_alternating_headers_are_scoped_by_volume(tmp_path):
    texts = {1:page('INDICES PATROLOGIÆ. INDEX XXV.'),
             2:page('INDEX PATRISTICO-THEOLOGICUS'),
             3:page('INDICES PATROLOGIÆ. INDEX XXV.'),
             4:page('INDICES PATROLOGIÆ. INDEX XXVI.')}
    r = recover(fixture(tmp_path,texts,volume='PL218'),volume='PL218')
    assert [(s['physical_start'],s['physical_end']) for s in r['sections']]==[(1,3)]
    r = recover(fixture(tmp_path,texts,volume='PG001'))
    assert [(s['physical_start'],s['physical_end']) for s in r['sections']]==[(2,2)]


def test_opening_with_notice_does_not_require_material_reference_on_same_page():
    raw = page('INDICES PATROLOGIÆ SPECIALES. INDEX CXIX.') + block(
        'INDEX DISCIPLINARIS.\nANALOGICE SCILICET EXHIBENS DISCIPLINAM.\nMONITUM.\n'
        'In hoc Indice titulos statuimus.')
    p = recovery.inspect_page(raw,'fixture','PL219',666)
    assert p['pieces'][0]['identity'] is None
    last = p['pieces'][-1]
    assert last['explicit_opening'] and last['audited_header_family']=='PL219:CXIX'
    assert not last['structure']['supported']


def test_mixed_prefix_does_not_inherit_next_index_header_or_series_notice():
    raw = page('INDICES PATROLOGIÆ. INDEX XXVI.')+block('SERIES TERTIA.','cabecalho')+block(
        'INDEX POETARUM GENTILIUM.\nTAM LATINORUM QUAM GRAECORUM.\nAFRANIUS, 709.\nALBINUS, 80.')
    p = recovery.inspect_page(raw,'fixture','PL218',672)
    first = p['pieces'][0]
    assert first['identity'] is None
    assert first['following_boundary']['kind']=='editorial_series'
    assert raw[slice(*first['span'])].find('SERIES TERTIA')<0


def test_operational_cap_does_not_claim_closing(tmp_path):
    result = recover(fixture(tmp_path, {n: page() for n in range(1, 9)}, (4,)), cap=1)
    section = result['sections'][0]
    assert (section['physical_start'], section['physical_end']) == (3, 5)
    assert section['left_stop'] == section['right_stop'] == 'expansion_cap_unknown_boundary'


@pytest.mark.parametrize('ambiguous', [False, True])
def test_missing_or_ambiguous_neighbor_is_not_crossed(tmp_path, ambiguous):
    data = fixture(tmp_path, {2: page(), 4: page()}, (2,))
    if ambiguous:
        directory = data[0] / 'PG001/text'
        (directory / 'a-3.txt').write_text(page())
        (directory / 'b-3.txt').write_text(page())
    result = recover(data)
    assert result['sections'][0]['physical_end'] == 2
    assert result['sections'][0]['right_stop'] == ('ambiguous_source' if ambiguous else 'missing_source')


def test_stale_source_rejects_all_candidates(tmp_path):
    data = fixture(tmp_path, {2: page()})
    Path(data[1][0]['source']).write_text(page(body='Aaron, 99.'))
    result = recover(data)
    assert not result['sections']
    assert {a['status'] for a in result['associations']} == {'seed_source_changed'}


def test_invalid_candidate_span_is_not_recovered(tmp_path):
    data = fixture(tmp_path, {2: page()})
    next(iter(data[2].values()))['entries'][0]['text_sha256'] = 'stale'
    assert recover(data)['associations'][0]['status'] == 'invalid_entry_span'


def test_po_subscript_preserved_and_different_indices_separated(tmp_path):
    result = recover(fixture(tmp_path, {
        2: page('TABLE DES NOMS PROPRES', 'Ἀβραάμ 47₁₂\nΑἰγύπτιοι 38₇'),
        3: page('TABLE ALPHABÉTIQUE DES MATIÈRES', 'ancillae 3₁₃\nangelus 32₅'),
        4: block('TABLE ANALYTIQUE DES MATIÈRES', 'cabecalho') + block('Préface 1.\nTable 62.'),
    }, (2, 3), 'PO006'), volume='PO006')
    assert [(s['physical_start'], s['physical_end']) for s in result['sections']] == [(2, 2), (3, 3)]
    assert '47₁₂' in result['sections'][0]['spans'][0]['quote']
    assert all('target_file' not in a for a in result['associations'])


def test_calendar_index_dates_support_region_without_becoming_page_numbers(tmp_path):
    result = recover(fixture(tmp_path, {2: page('INDEX AD USUARDI AUCTARIA',
                                               'Theodorus, 9 oct.\nUrsulæ et sociarum commemoratio, 12 aug.')}))
    assert len(result['sections']) == 1
    assert all(a['status'] == 'inside_index_candidate' for a in result['associations'])
    assert result['sections'][0]['spans'][1]['quote'].endswith('12 aug.')
    assert all('page_ref' not in a and 'target_file' not in a for a in result['associations'])


def test_generic_header_cannot_bridge_two_named_indices(tmp_path):
    result = recover(fixture(tmp_path, {1: page('INDEX IN PETRI CANTORIS'), 2: page('INDEX'),
                                       3: page('INDEX IN CEDRENUM')}, (1, 3)))
    assert len(result['sections']) == 2
    assert result['rejected_links']


def test_attested_inflection_and_numbering_notice(tmp_path):
    notice = 'Revocetur Lector ea numerales notas crassioribus typis textui insertas.'
    first = block('INDEX IN PETRI CANTORIS', 'cabecalho') + block(notice, 'cabecalho') + block('Abbas, 9.\nAbel, 10.')
    result = recover(fixture(tmp_path, {1: first, 2: page('INDEX IN PETRUM CANTOREM')}))
    section = result['sections'][0]
    assert section['physical_start'] == 1
    assert section['opening_status'] == 'title_with_numbering_notice_candidate'
    assert section['declarations'][0]['namespace_hint'] == 'internal_emphasized_numbers_edition_unknown'


def test_numeric_prose_does_not_extend_an_index(tmp_path):
    data = fixture(tmp_path, {2: page(), 3: block('First discussion, 14.\nSecond discussion, 15.'),
                              4: block('Ordinary prose.')})
    assert recover(data)['sections'][0]['physical_end'] == 2


def test_single_headerless_bridge_requires_matching_neighbor(tmp_path):
    data = fixture(tmp_path, {2: page(), 3: block('Abel, 14.\nAdam, 15.'), 4: page()})
    assert recover(data)['sections'][0]['physical_end'] == 4


def test_internal_ordo_boundary_preserves_index_prefix(tmp_path):
    mixed = block('101 ORDO RERUM 102', 'cabecalho') + block('Abel, 14.\nAdam, 15.')
    mixed += block('ORDO RERUM\nQUAE IN HOC TOMO CONTINENTUR.', 'rodape') + block('Notitia 9.')
    data = fixture(tmp_path, {2: page(), 3: mixed})
    section = recover(data)['sections'][0]
    assert section['physical_end'] == 3
    assert section['right_stop'] == 'internal_boundary'
    assert not any(s['quote'].startswith('Notitia') for s in section['spans'])


def test_running_title_alias_connects_actual_opening_without_taking_prior_prose(tmp_path):
    first = block('1633 INDEX IN CYRILLUM 1634', 'cabecalho') + block('Prior work, 99.')
    first += block('INDEX RERUM', 'cabecalho')
    first += block('Paginæ sunt editionis Benedictinæ quarum seriem typis crassioribus expressimus in textu.', 'nota')
    first += block('Aaron, 26.\nAbbas, 29.')
    result = recover(fixture(tmp_path, {1: first, 2: page('INDEX IN CYRILLUM')}))
    section = result['sections'][0]
    assert section['physical_start'] == 1
    assert section['opening_status'] == 'internal_title_candidate'
    assert section['declarations'][0]['namespace_hint'] == 'Benedictina'
    assert not any('Prior work' in s['quote'] for s in section['spans'])


def test_distinct_display_title_under_running_header_is_opening_candidate(tmp_path):
    raw = block('1277 INDEX IN WOLBERONEM 1278', 'cabecalho')
    raw += block('INDEX RERUM, VERBORUM\nET\nSENTENTIARUM MEMORABLIUM', 'cabecalho')
    raw += block('Aaron, 12.\nAbbas, 13.')
    result = recover(fixture(tmp_path, {2: raw}))
    assert len(result['sections']) == 1
    assert result['sections'][0]['opening_status'] == 'internal_title_candidate'


def test_index_preface_and_syllabus_mixed_ending(tmp_path):
    first = block('1509 INDEX GENERALIS 1510', 'cabecalho')
    first += block('INDEX GENERALIS.\nIN OPERA TERTULLIANI.\nMONITUM.', 'cabecalho')
    first += block('Multos pertæsum est hunc generalem in opera Septimii indicem. Index iste texeretur.')
    first += block('Prior nota arithmetica, charactere Romano descripta, indicat tomum; posterior, charactere Arabico, columnam;', 'nota')
    first += block('Aaron, II, 70 A.\nAbel, II, 90 B.')
    title = 'SYLLABUS RERUM QUÆ IN HOC VOLUMINE INCLUDUNTUR.'
    last = block('1593 ' + title + ' 1594', 'cabecalho') + block('Virgo, II, 90 B.\nZonarum vincula, II, 980.')
    last += block('SYLLABUS RERUM\nQUÆ IN HOC VOLUMINE INCLUDUNTUR.', 'cabecalho') + block('Notitia 9.')
    result = recover(fixture(tmp_path, {1: first, 2: page('INDEX GENERALIS'), 3: last}))
    section = result['sections'][0]
    assert (section['physical_start'], section['physical_end']) == (1, 3)
    assert section['declarations'][0]['locator_components'] == ['tome', 'column', 'letter_subdivision']
    assert not any('Notitia' in s['quote'] for s in section['spans'])
    other = first.replace('Multos pertæsum est hunc generalem in opera Septimii indicem. Index iste texeretur.', 'De vita et doctrina auctoris.')
    assert any(e['role'] == 'work_opening' for e in recovery.inspect_page(other, 'x', 'PG001', 1)['signals'])


def test_english_type_attribute_preserves_offsets_and_tipo_precedence():
    raw = '<bloco type="texto_principal">Aaron, 12.</bloco>'
    row = recovery.detector.units(raw)[0]
    assert row['block_type'] == 'texto_principal'
    assert raw[slice(*row['span'])] == row['quote']
    raw = '<bloco tipo="nota" type="texto_principal">Aaron, 12.</bloco>'
    assert recovery.detector.units(raw)[0]['block_type'] == 'nota'
    raw = '<bloco type="invented">Aaron, 12.</bloco>'
    assert recovery.detector.units(raw)[0]['block_type'] == 'unknown'


def options(tmp_path):
    root, seeds, parsed = fixture(tmp_path, {1: page(), 2: page()})
    source = tmp_path / 'source.sqlite'
    sqlite3.connect(source).close()
    parser_db = tmp_path / 'parser.sqlite'
    con = open_experiment(parser_db, source)
    con.execute('INSERT INTO runs VALUES(?,?,?,?)', ('old', 'today', '{}', 'completed'))
    for src, value in parsed.items():
        con.execute('INSERT INTO tasks VALUES(?,?,?)', (src, 'completed', json.dumps({'page': value})))
        con.execute('INSERT INTO run_pages VALUES(?,?,?,?,?,?,?)', ('old', src, 'PG001', 2, src, 'test', '{}'))
    con.commit()
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    manifest = tmp_path / 'seeds.jsonl'
    manifest.write_text('\n'.join(json.dumps(s) for s in seeds))
    return argparse.Namespace(root=root, volume=None, orphan_manifest=manifest, parser_db=parser_db,
                              parser_run='old', source_db=source, experiment_db=tmp_path / 'recovery.sqlite',
                              out=tmp_path / 'out', workers=2, max_expansion=3, seed=73, verbose=False)


def test_full_run_readonly_inputs_auditable_spans_and_wal(tmp_path):
    args = options(tmp_path)
    hashes = {p: sha(p.read_bytes()) for p in [args.source_db, args.parser_db]}
    result = recovery.run(args)
    assert result['counts']['inside_index_candidate'] == 2
    assert all(sha(p.read_bytes()) == digest for p, digest in hashes.items())
    with sqlite3.connect(args.experiment_db) as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert con.execute('SELECT status FROM orphan_recovery_runs').fetchone()[0] == 'completed'
    for line in (args.out / 'sections.jsonl').read_text().splitlines():
        for span in json.loads(line)['spans']:
            data = Path(span['source']).read_bytes()
            assert sha(data) == span['source_sha256']
            assert data.decode()[slice(*span['span'])] == span['quote']
    assert not result['quality_verified'] and result['production_db_updates'] == 0


@pytest.mark.parametrize('which', ['source_db', 'parser_db'])
def test_refuses_writing_to_source_databases(tmp_path, which):
    args = options(tmp_path)
    args.experiment_db = getattr(args, which)
    with pytest.raises(ValueError, match='differ'):
        recovery.run(args)


def test_manifest_physical_identity_checked(tmp_path):
    args = options(tmp_path)
    value = json.loads(args.orphan_manifest.read_text())
    value['physical'] = 700
    args.orphan_manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='Physical identity'):
        recovery.run(args)
