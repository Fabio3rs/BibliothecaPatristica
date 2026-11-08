import json
import sqlite3

from tools.indexing import pl_corpus_author_targets as p


def setup(tmp_path, headers, number=13, body=True):
    folder = tmp_path/'teste/PL178/text'
    folder.mkdir(parents=True)
    cache = tmp_path/'cache.sqlite'
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE file_observation_cache(volume_id,file_path,observation_json,collection)')
        for physical, header in headers.items():
            path = folder/f'scan-{physical:03d}.txt'
            text = '<bloco tipo="cabecalho">'+header+'</bloco>'
            if body:
                text += '<bloco tipo="texto_principal">Textus historicus satis longus ad probationem.</bloco>'
            path.write_text(text)
            db.execute('INSERT INTO file_observation_cache VALUES(?,?,?,?)', (
                'PL178',str(path),json.dumps(dict(file_seq=physical,header_text=header)), 'PL'))
    e = dict(section_id='PL218:index-iii-alphabetic-authors', grammar='pl_corpus_author_date_tome_column',
             reference_namespace=p.NAMESPACE, locators=[dict(target_volume='PL178', column_value=number)])
    return e, p.build_map(tmp_path,[e],cache_path=cache), folder


def test_bounded_missing_header(tmp_path):
    e,m,_ = setup(tmp_path,{10:'11 AUTHOR 12',11:'AUTHOR',12:'15 AUTHOR 16'})
    candidates = p.locate(e,m)
    assert {c['physical'] for c in candidates} == {11}
    assert candidates[0]['score'] == .82
    assert p.diagnose(e,m)['located_locator_count'] == 1
    assert candidates[0]['evidence'][0]['kind'] == 'local_pl_header_sequence'


def test_companion_column_requires_other_page_support(tmp_path):
    e,m,_ = setup(tmp_path,{10:'11 AUTHOR 12',11:'AUTHOR 14'})
    assert {c['physical'] for c in p.locate(e,m)} == {11}
    m['neighbor_numbers'].pop('PL178:11');m['neighbor_numbers'].pop('PL178:12')
    assert not p.locate(e,m)


def test_short_edge_extrapolation_requires_two_pairs_and_body(tmp_path):
    e,m,folder = setup(tmp_path,{10:'OPERA OMNIA',11:'AUTHOR',12:'13 AUTHOR 14',13:'15 AUTHOR 16'},number=9)
    assert {c['physical'] for c in p.locate(e,m)} == {10}
    assert p.locate(e,m)[0]['score'] == .64
    (folder/'scan-010.txt').write_text('<bloco tipo="cabecalho">OPERA OMNIA</bloco>')
    assert not p.locate(e,m)


def test_no_long_extrapolation(tmp_path):
    e,m,_ = setup(tmp_path,{8:'AUTHOR',9:'AUTHOR',10:'AUTHOR',11:'AUTHOR',12:'13 AUTHOR 14',13:'15 AUTHOR 16'},number=5)
    assert not p.locate(e,m)


def test_numbering_restart_blocks_bridge(tmp_path):
    e,m,_ = setup(tmp_path,{10:'11 AUTHOR 12',11:'1 NEW WORK 2',12:'15 AUTHOR 16'})
    assert not p.locate(e,m)


def test_disagreeing_slope_not_interpolated(tmp_path):
    e,m,_ = setup(tmp_path,{10:'11 AUTHOR 12',11:'AUTHOR',12:'17 AUTHOR 18'})
    assert not p.locate(e,m)


def test_stale_anchor_is_rechecked(tmp_path):
    e,m,folder = setup(tmp_path,{10:'11 AUTHOR 12',11:'AUTHOR',12:'15 AUTHOR 16'})
    assert p.locate(e,m)
    (folder/'scan-012.txt').write_text('<bloco tipo="cabecalho">NEW WORK 101 102</bloco>')
    assert not p.locate(e,m)


def test_independent_physical_alternatives_preserved(tmp_path):
    e,m,_ = setup(tmp_path,{10:'11 A 12',11:'AUCTOR',12:'15 A 16',
                          100:'11 A 12',101:'AUCTOR',102:'15 A 16'})
    assert {c['physical'] for c in p.locate(e,m)} == {11,101}
