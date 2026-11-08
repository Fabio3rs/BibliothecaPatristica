import hashlib
import json
import sqlite3

import pytest

from tools.indexing.scripture_comparison import Sources, readonly, relation, run


def ref(**kwargs):
    return dict(book_key='genesis',chapter_start=1,verse_start=2,
                chapter_end=None,verse_end=None,**kwargs)


def test_reference_identity_does_not_certify_range_or_chapter():
    r=ref(); o={**r,'open_ended':0}
    assert relation(r,o)=='same_normalized_reference'
    assert relation({**r,'verse_end':4},o)=='starting_point_only'
    assert relation(r,{**o,'open_ended':1})=='starting_point_only'
    assert relation({**r,'verse_start':None},o)=='chapter_only_candidate'
    assert relation(r,{**o,'verse_start':3})=='different'
    assert relation(r,{**o,'book_key':'exodus'})=='different'


def test_evidence_offsets_are_in_visible_block_not_raw_xml(tmp_path):
    p=tmp_path/'p.txt';p.write_text('<pagina><bloco tipo="texto_principal">Gen. 1,2</bloco></pagina>')
    row={'file_path':str(p),'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
         'block_index':0,'source_start':0,'source_end':8,'citation_raw':'Gen. 1,2'}
    assert Sources().check(row)=='current_exact_span'
    assert Sources().check({**row,'citation_raw':'Exod. 1,2'})=='span_text_mismatch'
    assert Sources().check({**row,'block_index':2})=='invalid_block_span'
    assert Sources().check({**row,'file_sha256':'old'})=='changed_file'
    p.unlink()
    assert Sources().check(row)=='missing_file'


def test_readonly_cannot_create_or_mutate(tmp_path):
    p=tmp_path/'input.sqlite'
    with sqlite3.connect(p) as c:c.execute('CREATE TABLE x(a)')
    c=readonly(p)
    with pytest.raises(sqlite3.OperationalError):c.execute('INSERT INTO x VALUES(1)')
    c.close()
    with pytest.raises(sqlite3.OperationalError):readonly(tmp_path/'missing.sqlite')
    assert not (tmp_path/'missing.sqlite').exists()


def test_compare_preserves_multiple_files_and_excludes_index_and_stale(tmp_path):
    index=tmp_path/'index.sqlite'; citations=tmp_path/'citations.sqlite'
    with sqlite3.connect(index) as c:
        c.executescript('''CREATE TABLE alphabetical_sections(section_key TEXT,volume_id TEXT);
            CREATE TABLE alphabetical_entries(entry_key TEXT,section_key TEXT,lemma_raw TEXT,
                entry_raw TEXT,index_entry_source_ocr_file TEXT);
            CREATE TABLE alphabetical_scripture_refs(scripture_ref_id INTEGER,entry_key TEXT,
                ref_order INTEGER,book_key TEXT,chapter_start INTEGER,verse_start INTEGER,
                chapter_end INTEGER,verse_end INTEGER);
            CREATE TABLE alphabetical_refs(entry_key TEXT,scripture_ref_order INTEGER,
                page_ref_int INTEGER,resolved_target_ocr_file TEXT);
            INSERT INTO alphabetical_sections VALUES('s','PL001');
            INSERT INTO alphabetical_entries VALUES('e','s','Genesis','Gen. 1,2',NULL);
            INSERT INTO alphabetical_scripture_refs VALUES(1,'e',1,'genesis',1,2,NULL,NULL);''')
    with sqlite3.connect(citations) as c:
        c.executescript('''CREATE TABLE citation_files(file_id INTEGER,volume_id TEXT,file_path TEXT,
                file_sha256 TEXT,physical_file_seq INTEGER,is_index_source INTEGER,detector_version INTEGER);
            CREATE TABLE citation_groups(group_id INTEGER,file_id INTEGER,block_index INTEGER,
                block_type TEXT,source_start INTEGER,source_end INTEGER,citation_raw TEXT,
                snippet_raw TEXT,context_kind TEXT,detector_rule TEXT,evidence_json TEXT);
            CREATE TABLE citation_occurrences(occurrence_id INTEGER,group_id INTEGER,book_key TEXT,
                chapter_start INTEGER,verse_start INTEGER,chapter_end INTEGER,verse_end INTEGER,
                open_ended INTEGER,normalization_status TEXT,occurrence_key TEXT);
            CREATE TABLE citation_file_pages(file_id INTEGER,editorial_page INTEGER);
            CREATE TABLE citation_index_seeds(seed_key TEXT,source_payload TEXT,source_fingerprint TEXT,
                volume_id TEXT,seed_status TEXT,book_key TEXT,chapter_start INTEGER,verse_start INTEGER,
                chapter_end INTEGER,verse_end INTEGER);
            CREATE TABLE citation_seed_links(match_kind TEXT,seed_key TEXT,occurrence_key TEXT,score REAL);
            CREATE TABLE citation_meta(meta_key TEXT,meta_value TEXT);''')
        for i in range(1,5):
            p=tmp_path/f'{i}.txt';p.write_text('<pagina><bloco tipo="texto_principal">Gen. 1,2</bloco></pagina>')
            sha=hashlib.sha256(p.read_bytes()).hexdigest() if i!=3 else 'old'
            c.execute('INSERT INTO citation_files VALUES(?,?,?,?,?,?,?)',(i,'PL001',str(p),sha,i,int(i==4),7))
            c.execute('INSERT INTO citation_groups VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (i,i,0,'texto_principal',0,8,'Gen. 1,2','Gen. 1,2','body','test','{}'))
            c.execute('INSERT INTO citation_occurrences VALUES(?,?,?,?,?,?,?,?,?,?)',
                (i,i,'genesis',1,2,None,None,0,'normalized',str(i)))
            c.execute('INSERT INTO citation_seed_links VALUES(?,?,?,?)',('canonical_ref','seed',str(i),0.99))
        payload=tmp_path/'payload.json';payload.write_text('{}')
        c.execute('INSERT INTO citation_index_seeds VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('seed',str(payload),hashlib.sha256(payload.read_bytes()).hexdigest(),
             'PL001','valid','genesis',1,2,None,None))
    before=[p.read_bytes() for p in [index,citations]]
    out=tmp_path/'out'; result=run(index,citations,out)
    record=json.loads((out/'comparisons.jsonl').read_text())
    assert len(record['candidates'])==3 and len(record['current_candidate_files'])==2
    assert record['excluded']=={'index_source':1}
    assert not record['automatic_resolution']
    assert result['counts']['current_ocr_candidates']==1
    assert result['counts']['without_explicit_material_link']==1
    assert result['historical_link_recheck']['links']=={
        'current_ocr_candidate':2,'stale_or_invalid_ocr':1,'index_source_excluded':1}
    assert result['historical_link_recheck']['seeds']=={'with_current_ocr_candidate':1}
    assert before==[p.read_bytes() for p in [index,citations]]
    with sqlite3.connect(out/'comparison.sqlite') as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
