import hashlib
import json
import sqlite3

import pytest

from scripts.audit_index_destination_gaps import audit, notation_shape


@pytest.mark.parametrize('raw,shape', [
    ('ibid.', 'ibid_only'), ('IBIDEM', 'ibid_only'), ('ibid., 257', 'ibid_mixed'),
    ('XV, 104.', 'roman_and_arabic'), ('125 n. 1', 'note_locator'),
    ('90 et seqq.', 'following_pages_qualifier'), ('7–9', 'numeric_range'),
    ('74₉', 'subscript_detail'), ('104', 'simple_numeric'),
    ('1, 075', 'zero_or_zero_prefixed_numeric'), ('Suppl. 121', 'supplement_locator'),
    ('Registr. de neg. imp. 26', 'register_locator'), ('240 a, b.', 'letter_subdivision'),
])
def test_notation_classes_preserve_semantic_uncertainty(raw,shape):
    assert notation_shape(raw) == shape


def test_audit_distinguishes_missing_marker_filtered_marker_and_stale_source(tmp_path):
    db = tmp_path/'search.sqlite'; source = tmp_path/'ocr.txt'; source.write_text('changed')
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
        for ident, group, status, raw in [('1','g1','no_header_coincidence','10'),
                ('2','g2','no_header_coincidence','20'), ('3','g3','source_or_span_invalid','30')]:
            c.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', (ident,group,'PG001','lemma',raw,str(source),'source_only',
                json.dumps({'status':status,'source_span':[0,2],'source_sha256':'old'})))
        c.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',('4','g1','PG001','lemma','11',str(source),'with_destinations','{}'))
    d = tmp_path/'survey/volumes/PG001'; d.mkdir(parents=True)
    (d/'sources.jsonl').write_text(json.dumps({'source':str(source)})+'\n')
    (d/'header_markers.jsonl').write_text(json.dumps({'number':20,'path':str(source)})+'\n')
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    r = audit(db,tmp_path/'survey',tmp_path/'out')
    assert r['totals']['source_only_items'] == 3
    assert r['totals']['groups_also_having_destinations'] == 1
    assert r['causes'] == {'no_literal_header_marker_in_survey':1,
        'header_marker_only_in_selected_index_sources':1,'source_text_changed':1}
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert source.read_text() == 'changed'


def test_overlays_remove_only_navigable_items_and_preserve_group_distinction(tmp_path):
    db=tmp_path/'search.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
        for ident,group,raw in [('a','shared','I, 1.'),('b','shared','I, 2.'),('c','other','II, 3.')]:
            con.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',(ident,group,'PL217','lemma',raw,
                'source.txt','source_only',json.dumps({'status':'notation_pending'})))
    overlay=tmp_path/'overlay.sqlite'
    with sqlite3.connect(overlay) as con:
        con.executescript('CREATE TABLE metadata(key,json); CREATE TABLE candidates(item_id,navigable);'
                          'CREATE TABLE relations(item_id,group_id,status,evidence_json);')
        con.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps({'catalog':str(db)})))
        con.executemany('INSERT INTO candidates VALUES(?,?)',[('a',1),('c',0)])
        for ident in ('b','c'):
            con.execute('INSERT INTO relations VALUES(?,?,?,?)',(ident,'shared','locator_without_opening',
                json.dumps({'ref_raw':'I, 2.'})))
    digest=hashlib.sha256(db.read_bytes()).hexdigest()
    r=audit(db,tmp_path/'survey',tmp_path/'out',relations_db=[overlay,overlay])
    assert r['totals']['source_only_items']==2
    assert r['totals']['recovered_by_supplied_overlays']==1
    assert r['totals']['groups_also_having_destinations']==1
    assert r['totals']['groups_without_any_destination']==1
    assert r['causes']=={'epistle_locator_without_recognized_opening':2}
    assert hashlib.sha256(db.read_bytes()).hexdigest()==digest


def test_group_trace_with_different_raw_does_not_explain_another_interpretation(tmp_path):
    db=tmp_path/'search.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
        con.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',('a','g','PL217','lemma','I, 1.',
            'source.txt','source_only',json.dumps({'status':'notation_pending'})))
    overlay=tmp_path/'overlay.sqlite'
    with sqlite3.connect(overlay) as con:
        con.executescript('CREATE TABLE metadata(key,json); CREATE TABLE candidates(item_id,navigable);'
                          'CREATE TABLE relations(group_id,status,evidence_json);')
        con.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps({'catalog':str(db)})))
        con.execute('INSERT INTO relations VALUES(?,?,?)',('g','relation_without_destination',json.dumps(
            {'raw':'ibid.','relations':[{'reason':'no_local_antecedent'}]})))
    r=audit(db,tmp_path/'survey',tmp_path/'out',relations_db=[overlay])
    assert r['causes']=={'notation:roman_and_arabic':1}
    assert r['processing_stages']=={'reference_grammar_pending_no_applicable_saved_recovery':1}
    with sqlite3.connect(overlay) as con:
        con.execute('UPDATE metadata SET json=?',(json.dumps({'catalog':str(tmp_path/'wrong.sqlite')}),))
    with pytest.raises(ValueError,match='another catalog'):
        audit(db,tmp_path/'survey',tmp_path/'wrong_out',relations_db=[overlay])
