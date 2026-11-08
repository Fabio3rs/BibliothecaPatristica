import json
import sqlite3
from pathlib import Path

from tools.indexing.pl_corpus_author_targets import build_map, diagnose, locate


def fixture(tmp_path):
    root = tmp_path
    folder = root/'teste/PL178/text'
    folder.mkdir(parents=True)
    path = folder/'scan-005.txt'
    path.write_text('<bloco tipo="cabecalho">9 PETRUS ABÆLARDUS. 10</bloco><bloco tipo="texto_principal">1142</bloco>')
    cache = root/'cache.sqlite'
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE file_observation_cache(volume_id TEXT,file_path TEXT,observation_json TEXT,collection TEXT)')
        db.execute('INSERT INTO file_observation_cache VALUES(?,?,?,?)', ('PL178',str(path),json.dumps({'file_seq':5,'header_pairs':[[9,10]],'header_singles':[], 'header_text':'9 PETRUS. 10'}),'PL'))
    e = {'section_id':'PL218:index-iii-alphabetic-authors','grammar':'pl_corpus_author_date_tome_column','reference_namespace':'pl_corpus_tome_column',
         'locators':[{'target_volume':'PL178','column_value':9,'raw':'CLXXVIII, 9'}]}
    return root, cache, path, e


def test_reuses_cache_and_validates_current_literal_header(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    before = cache.read_bytes()
    mapping = build_map(root, [e], cache_path=cache)
    result = locate(e, mapping)
    assert len(result) == 1 and result[0]['file'] == str(path)
    proof = result[0]['evidence'][0]['current_header_evidence'][0]
    assert path.read_text()[slice(*proof['span'])] == '9'
    assert diagnose(e,mapping)['located_locator_count'] == 1
    assert cache.read_bytes() == before


def test_changed_source_does_not_hash_block_but_stale_number_does_not_link(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    mapping = build_map(root,[e],cache_path=cache)
    path.write_text('<bloco tipo="cabecalho">9 NEW HEADING. 10</bloco>')
    assert locate(e,mapping)
    path.write_text('<bloco tipo="cabecalho">11 NEW HEADING. 12</bloco>')
    assert not locate(e,mapping)
    assert diagnose(e,mapping)['unmatched'][0]['reason'] == 'cached_file_missing_or_current_header_not_literal'


def test_body_numbers_never_become_column_headers(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    mapping = build_map(root,[e],cache_path=cache)
    path.write_text('<bloco tipo="cabecalho">11 NAME 12</bloco><bloco tipo="texto_principal">9</bloco>')
    assert not locate(e,mapping)


def test_unknown_range_and_vide_stay_pending(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    mapping = build_map(root,[e],cache_path=cache)
    e['locators'].append({'relation':'tome_range_without_column','raw':'CLXXV-CLXXVII'})
    e['textual_cross_reference']={'raw':'Vide Petrus'}
    d=diagnose(e,mapping)
    assert d['locator_count']==3 and d['located_locator_count']==1
    assert len(d['unmatched'])==2


def test_all_duplicate_header_candidates_preserved(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    other=path.with_name('copy-006.txt');other.write_text(path.read_text())
    with sqlite3.connect(cache) as db:
        db.execute('INSERT INTO file_observation_cache VALUES(?,?,?,?)',('PL178',str(other),json.dumps({'file_seq':6,'header_pairs':[[9,10]],'header_singles':[], 'header_text':'9 OTHER 10'}),'PL'))
    assert len(locate(e,build_map(root,[e],cache_path=cache)))==2


def test_internal_numeric_punctuation_never_enumerates_fragments(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    for header in ['9:9 EXPOSITIO 910', '9.9 EXPOSITIO 910', '9,9 EXPOSITIO 910', '9/9 EXPOSITIO 910']:
        path.write_text(f'<bloco tipo="cabecalho">{header}</bloco>')
        with sqlite3.connect(cache) as db:
            db.execute('UPDATE file_observation_cache SET observation_json=?', (json.dumps({'file_seq':5,'header_pairs':[[9,910]],'header_singles':[9], 'header_text':header}),))
        mapping=build_map(root,[e],cache_path=cache)
        assert 'PL178:9' not in mapping['numbers']
        assert not locate(e,mapping)


def test_current_fragment_rejected_even_with_preexisting_good_cache(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    mapping=build_map(root,[e],cache_path=cache)
    path.write_text('<bloco tipo="cabecalho">9:9 EXPOSITIO 910</bloco>')
    assert not locate(e,mapping)


def test_pair_boost_requires_current_consecutive_columns(tmp_path):
    root, cache, path, e = fixture(tmp_path)
    mapping=build_map(root,[e],cache_path=cache)
    path.write_text('<bloco tipo="cabecalho">9 EXPOSITIO 910</bloco>')
    candidates=locate(e,mapping)
    assert candidates[0]['score']==0.76
    assert candidates[0]['evidence'][0]['current_consecutive_pair'] is False
    path.write_text('<bloco tipo="cabecalho">9 EXPOSITIO 10</bloco>')
    assert locate(e,mapping)[0]['score']==0.90


def test_real_pl178_462_fragment_is_not_column_nine():
    from tools.indexing.pl_corpus_author_targets import _current_header
    files=list(Path('teste/PL178/text').glob('*-462.txt'))
    if not files:
        return
    path=files[0].resolve();stat=path.stat()
    raw=path.read_text()
    if '9:9' in raw:
        assert 9 not in _current_header(str(path),stat.st_mtime_ns,stat.st_size)
