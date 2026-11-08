from pathlib import Path
import sqlite3

import pytest

from tools.indexing.estimator_candidate_recovery import (
    contiguous_segments, reference_numbers, rank_candidates, source_context,
    invert_estimates, readonly,
)


def test_reference_semantics_are_not_flattened():
    assert reference_numbers({'ref_raw': '518'}, {'reference_namespace': 'internal_text_number'}) == ([], 'requires_namespace_map:internal_text_number')
    assert reference_numbers({'ref_raw': 'III, 18'}, {}) == ([], 'requires_roman_context')
    assert reference_numbers({'ref_raw': 'ibid. 18'}, {}) == ([], 'requires_editorial_inheritance')
    assert reference_numbers({'ref_raw': '12345'}, {})[0] == []
    assert reference_numbers({'ref_raw': '12-14, 18'}, {})[0] == [12, 13, 14, 18]
    assert reference_numbers({'ref_raw': '120-4'}, {})[1] == 'requires_range_interpretation'


def test_page_line_only_uses_page():
    meta = {'reference_namespace': 'page_line', 'page_line_tokens': [{'page_raw': '79', 'line_raw': '10', 'raw': '79,10'}]}
    assert reference_numbers({'ref_raw': '79,10'}, meta)[0] == [79]
    assert reference_numbers({'ref_raw': '79,11'}, meta)[0] == []


def test_split_at_gap_and_duplicate_physical_suffix():
    paths = [Path(n) for n in ['a-001.txt', 'a-002.txt', 'a-004.txt', 'b-004.txt', 'b-005.txt']]
    assert contiguous_segments(paths) == [paths[:2], paths[2:3], paths[3:]]


def test_multiple_physical_files_survive_with_work_ranking():
    def match(file, physical):
        return {'file': file, 'physical': physical, 'number': 12, 'hypothesis_order': 0, 'score': 5}
    mapping = {12: [match('a.txt', 1), match('b.txt', 9)]}
    works = [{'work_key': 'w', 'bounded': True, 'physical_start': 8, 'physical_end': 10}]
    result = rank_candidates([12], mapping, {'source': 'index.txt'}, [{'work_key': 'w'}], works)
    assert [r['file'] for r in result] == ['b.txt', 'a.txt']
    assert result[0]['work_scope_matches'] == ['w']


def test_mixed_page_scope_preserves_alternatives():
    sections = [{'section_key': k, 'work_key': k, 'physical_start': 10, 'physical_end': 10, 'index_kind': 'INDEX', 'heading_raw': k} for k in ['a','b']]
    assert len(source_context({'source': 'page-010.txt'}, {}, sections)) == 2


def test_body_only_number_is_not_pagination():
    estimate = {'file': 'page-010.txt', 'file_seq': 10, 'warnings': [], 'sequence_validation': {},
                'candidate_editorial_pages': [{'pages': [12], 'score': 1, 'evidence': [{'kind': 'body_single'}]},
                                             {'pages': [9,10], 'score': 2, 'evidence': [{'kind': 'neighbor_pair'}]}]}
    inverted = invert_estimates([estimate])
    assert 12 not in inverted
    assert inverted[9][0]['sequence_validation'] is None


def test_db_adapter_rejects_write(tmp_path):
    path = tmp_path / 'works.sqlite'
    conn = sqlite3.connect(path)
    conn.execute('create table works(id)');conn.close()
    conn = readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute('insert into works values (1)')
    conn.close()


def test_work_index_requires_explicit_pagination_and_existing_target():
    from collections import defaultdict
    from tools.indexing.estimator_candidate_recovery import add_catalog_anchors
    base = {'id': 1, 'section_key': 's', 'target_file': 'a.txt', 'target_physical': 10,
            'page_ref_raw': 'col. 11', 'reference_kind': 'editorial_column'}
    mapping = defaultdict(list)
    add_catalog_anchors(mapping, [base, {**base, 'reference_kind': 'chapter'},
                                 {**base, 'target_physical': None},
                                 {**base, 'page_ref_raw': '11-15'}])
    assert list(mapping) == [11]
    assert len(mapping[11]) == 1
    assert mapping[11][0]['evidence'][0]['entry_id'] == 1


def test_column_subdivision_does_not_become_roman_book():
    assert reference_numbers({'ref_raw': '123 C'}, {})[0] == [123]
    assert reference_numbers({'ref_raw': 'C, 123'}, {})[1] == 'requires_roman_context'


def test_end_to_end_reproducible_overlay_and_immutable_inputs(tmp_path):
    import argparse
    import hashlib
    import json
    from tools.indexing.estimator_candidate_recovery import run
    corpus = tmp_path / 'corpus'
    root = corpus / 'PG001' / 'text'
    root.mkdir(parents=True)
    source = root / 'page-002.txt'
    source.write_text('Index: Verbum 121')
    (root / 'page-001.txt').write_text('<pagina><bloco tipo="cabecalho">121 TITULUS 122</bloco></pagina>')
    catalog = tmp_path / 'catalog.sqlite'
    c = sqlite3.connect(catalog)
    c.execute('create table items(id,volume,label,ref_raw,source,state,metadata_json)')
    c.execute('insert into items values (?,?,?,?,?,?,?)', ('one','PG001','Verbum','121',str(source),'source_only',json.dumps({'source_span':[14,17]})))
    c.commit();c.close()
    works = tmp_path / 'works.sqlite'
    c = sqlite3.connect(works)
    c.executescript('''
        create table works(work_key,volume_id,start_file,end_file);
        create table index_sections(section_key,volume_id,work_key,file_start,file_end,index_kind,heading_raw);
        create table index_entries(id,section_key,raw_json,target_file,page_ref_raw);
    ''')
    c.commit();c.close()
    cache = tmp_path / 'missing.sqlite'
    inputs = [catalog, works, *root.glob('*.txt')]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    args = dict(catalog=catalog,works_db=works,estimator_db=cache,corpus_root=corpus,
                volume=['PG001'],all_volumes=False,window=4,limit_per_volume=0)
    first = run(argparse.Namespace(**args,workers=1,out=tmp_path/'one'))
    second = run(argparse.Namespace(**args,workers=2,out=tmp_path/'two'))
    assert first['logical_sha256'] == second['logical_sha256']
    assert first['with_candidates'] == 1
    assert not cache.exists()
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    with sqlite3.connect(tmp_path/'one'/'candidates.sqlite') as c:
        assert c.execute('pragma journal_mode').fetchone()[0] == 'wal'
    from scripts.summarize_estimated_page_candidates import summarize
    report = summarize(tmp_path/'one')
    assert report['counts']['examined'] == 1
    assert report['counts']['with_candidates'] == 1
    assert report['coverage_check']['missing_pending_ids'] == 0
    assert report['coverage_check']['missing_candidate_files'] == []


@pytest.mark.parametrize('raw,warning', [('"legacy text"','metadata_json_not_object'), ('[]','metadata_json_not_object'), ('null','metadata_json_not_object'), ('broken','invalid_metadata_json')])
def test_legacy_metadata_is_preserved_without_assuming_object(raw, warning):
    from tools.indexing.estimator_candidate_recovery import metadata_object
    assert metadata_object(raw) == ({}, warning)
