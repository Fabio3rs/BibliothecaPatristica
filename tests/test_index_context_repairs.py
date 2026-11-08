import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.candidate_search import search_with_supplements
from tools.indexing.index_context_repairs import run, map_availability, entry_label


def catalog(path, rows):
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE items(id TEXT,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT); CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json); CREATE TABLE metadata(key,json); CREATE TABLE item_annotations(item_id,evidence_json); CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume);')
        for r in rows:
            cursor=db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',r)
            db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',(cursor.lastrowid,r[3],r[4],r[2]))


def test_real_repair_is_searchable_without_changing_base_or_ocr(tmp_path):
    raw='<bloco tipo="texto_principal">Jonathas heros, I, 152.</bloco>'
    source=tmp_path/'page-1.txt';source.write_text(raw)
    a,b=raw.index('152'),raw.index('152')+3
    checksum=hashlib.sha256(raw.encode()).hexdigest()
    group={'id':'g','volume':'PG122','physical':1,'source':str(source),'source_sha256':checksum,
           'owner_raw':'Jonathas heros, I','raw':'152','char_start':a,'char_end':b}
    survey=tmp_path/'survey';survey.mkdir()
    with sqlite3.connect(survey/'survey.sqlite') as db:
        db.execute('CREATE TABLE groups(id,evidence_json)')
        db.execute('INSERT INTO groups VALUES(?,?)',('g',json.dumps(group)))
    base=tmp_path/'base.sqlite'
    catalog(base,[('x','g','PG122','Jonathas heros, I','152',str(source),'source_only',json.dumps({'source_span':[a,b]}))])
    candidate={'file':'existing-target.txt','rank':1,'score':40,'navigable':True}
    with sqlite3.connect(base) as db:
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',('x',candidate['file'],1,40,1,json.dumps(candidate)))
    patterns=tmp_path/'patterns';patterns.mkdir()
    (patterns/'occurrences.jsonl').write_text(json.dumps({'item_id':'x','group_id':'g','extraction_diagnostics':['roman_context_in_owner_not_reference']})+'\n')
    before={p:p.read_bytes() for p in [source,base,survey/'survey.sqlite']}
    result=run(patterns,survey,base,tmp_path/'out')
    assert result['replaced_search_records']==1
    assert result['new_destination_links']==0
    records=search_with_supplements(base,'Jonathas',supplements=[tmp_path/'out/search.sqlite'])
    assert len(records)==1 and records[0]['id']=='x'
    assert records[0]['label']=='Jonathas heros'
    assert records[0]['ref_raw']=='I, 152'
    assert records[0]['candidates']==[candidate]
    assert result['preserved_candidate_links']==1
    assert all(p.read_bytes()==value for p,value in before.items())
    repeated=run(patterns,survey,base,tmp_path/'second',previous=tmp_path/'out/search.sqlite')
    comparison=repeated['previous_comparison']
    assert comparison['previous_replacements_retained']==1
    assert comparison['additional_replacements']==0
    assert comparison['previous_rows_not_reissued']==[]


def test_corrected_search_does_not_match_other_owners_from_original_concat(tmp_path):
    base=tmp_path/'base.sqlite';supplement=tmp_path/'supplement.sqlite'
    catalog(base,[('x','g','PG110','WrongName CorrectName','10','s','source_only','{}')])
    catalog(supplement,[('x','g','PG110','CorrectName','10','s','source_only','{}')])
    with sqlite3.connect(supplement) as db:
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps({'catalog':str(base)})))
        db.execute('CREATE TABLE item_replacements(item_id,original_label,original_ref,source)')
        db.execute('INSERT INTO item_replacements VALUES(?,?,?,?)',('x','WrongName CorrectName','10','s'))
    assert search_with_supplements(base,'WrongName',supplements=[supplement])==[]
    assert search_with_supplements(base,'CorrectName',supplements=[supplement])[0]['label']=='CorrectName'
    with sqlite3.connect(base) as db:
        db.execute('UPDATE items SET ref_raw="11"')
    with pytest.raises(ValueError,match='no longer matches'):
        search_with_supplements(base,'CorrectName',supplements=[supplement])


def test_profile_not_extended_across_sections(tmp_path):
    folder=tmp_path/'volumes/PG110';folder.mkdir(parents=True)
    (folder/'profiles.json').write_text(json.dumps([{'profile':{'index_physical_start':661,'index_physical_end':665,'notation':'page_line'}}]))
    result=map_availability('PG110',648,tmp_path,'55, 168')
    assert result['status']=='outside_existing_profile_scope'
    assert result['candidates']==[]


def test_subclause_keeps_parent_in_search_label():
    assert entry_label({'owner_raw':'Ὑστάσπου','owner_span':[20,28],
                        'parent_owner_raw':'Δαρεῖος','parent_owner_span':[0,7]})=='Δαρεῖος — Ὑστάσπου'
    assert entry_label({'owner_raw':'Δαρεῖος','owner_span':[0,7],
                        'parent_owner_raw':'Δαρεῖος','parent_owner_span':[0,7]})=='Δαρεῖος'


def test_new_clauses_preserve_reference_spans_and_skip_existing_occurrences(tmp_path):
    raw='<bloco tipo="texto_principal">Δάονον 553.\nΔάξυμον 735.\nΔάραι 621.</bloco>'
    source=tmp_path/'page.txt';source.write_text(raw)
    a=raw.index('621');b=a+3
    owner=raw[raw.index('Δάονον'):a].rstrip()
    group={'id':'g','volume':'PG110','physical':651,'source':str(source),
           'raw':'621','owner_raw':owner,'char_start':a,'char_end':b}
    survey=tmp_path/'survey';survey.mkdir()
    with sqlite3.connect(survey/'survey.sqlite') as db:
        db.execute('CREATE TABLE groups(id,evidence_json)')
        db.execute('INSERT INTO groups VALUES(?,?)',('g',json.dumps(group)))
    base=tmp_path/'base.sqlite'
    s=raw.index('553')
    catalog(base,[('x','g','PG110',owner,'621',str(source),'source_only',json.dumps({'source_span':[a,b]})),
                  ('already','old','PG110','Δάονον','553',str(source),'source_only',json.dumps({'source_span':[s,s+3]}))])
    patterns=tmp_path/'patterns';patterns.mkdir()
    (patterns/'occurrences.jsonl').write_text(json.dumps({'item_id':'x','group_id':'g',
                  'extraction_diagnostics':['multiple_entry_like_lines_in_owner']})+'\n')
    result=run(patterns,survey,base,tmp_path/'out')
    assert result['replaced_search_records']==1
    assert result['child_counts']['additional_searchable_clauses']==1
    assert result['child_counts']['child_reference_already_represented']==1
    rows=[json.loads(line) for line in (tmp_path/'out/items.jsonl').read_text().splitlines()]
    child=next(row for row in rows if row['id'].startswith('context-child:'))
    assert child['label']=='Δάξυμον'
    start,end=child['metadata_json']['source_span']
    assert raw[start:end]==child['ref_raw']=='735'
    assert 'parser_ambiguities' in child['metadata_json']


def test_uncertain_neighbor_does_not_block_clear_original_or_create_false_child(tmp_path):
    raw='<texto_principal>Λαφφιδώθ λαμπάδες 104.\nΛέα κοπιῶσα?\nΛειχούδης 882.\nλείψανα τῶν ἁγίων 643, 654</texto_principal>'
    source=tmp_path/'page.txt';source.write_text(raw)
    start=raw.index('654');end=start+3
    owner=raw[raw.index('Λαφφιδώθ'):start].rstrip(', ')
    group={'id':'g','volume':'PG110','physical':655,'source':str(source),
           'raw':'654','owner_raw':owner,'char_start':start,'char_end':end}
    survey=tmp_path/'survey';survey.mkdir()
    with sqlite3.connect(survey/'survey.sqlite') as db:
        db.execute('CREATE TABLE groups(id,evidence_json)')
        db.execute('INSERT INTO groups VALUES(?,?)',('g',json.dumps(group)))
    base=tmp_path/'base.sqlite'
    catalog(base,[('x','g','PG110',owner,'654',str(source),'source_only',json.dumps({'source_span':[start,end]}))])
    patterns=tmp_path/'patterns';patterns.mkdir()
    (patterns/'occurrences.jsonl').write_text(json.dumps({'item_id':'x','group_id':'g',
                  'extraction_diagnostics':['multiple_entry_like_lines_in_owner']})+'\n')
    result=run(patterns,survey,base,tmp_path/'out')
    assert result['replaced_search_records']==1
    assert result['child_counts']['child_boundary_requires_review']==1
    rows=[json.loads(line) for line in (tmp_path/'out/items.jsonl').read_text().splitlines()]
    assert next(r for r in rows if r['id']=='x')['label']=='λείψανα τῶν ἁγίων'
    assert not any(r['ref_raw']=='882' for r in rows)
