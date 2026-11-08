import hashlib
import json
import sqlite3

from tools.indexing.catalog_parser_review import review_source, signals, run


def fixture_group(source, raw, owner='Name, II', reference='152'):
    start=raw.index(reference)
    return {'id':'g','volume':'PL001','physical':1,'source':str(source),
            'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'owner_raw':owner,'raw':reference,'char_start':start,'char_end':start+len(reference)}


def test_signal_is_not_error_or_numeric_namespace():
    flags=signals({'owner_raw':'Gen. 1','raw':'2'})
    assert flags==['numeric_tail_in_owner']
    assert 'single_entry_like_line_in_owner' in signals({'owner_raw':'Alpha 12.\nAbba','raw':'22'})


def test_changed_page_with_unchanged_local_reference_is_not_blocked(tmp_path):
    source=tmp_path/'page.txt'
    raw='<texto_principal>Name, II, 152.</texto_principal>'
    group=fixture_group(source,raw)
    source.write_text(raw+'\nNew unrelated paragraph.')
    result=review_source((str(source),[group]))
    assert result['span_states']=={'source_changed_local_reference_matches':1}
    assert result['findings'][0]['parsers']['roman']['status']=='restored_candidate'
    source.write_text(raw.replace('152','153'))
    result=review_source((str(source),[group]))
    assert result['span_states']=={'reference_span_mismatch':1}
    assert result['findings'][0]['repair_diagnostics']==[]


def test_catalog_audit_includes_already_located_and_missing_groups(tmp_path):
    source=tmp_path/'page.txt';raw='<texto_principal>Name, II, 152.</texto_principal>'
    source.write_text(raw);group=fixture_group(source,raw)
    catalog=tmp_path/'catalog.sqlite';survey=tmp_path/'survey.sqlite'
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE items(id,group_id,volume,source,state)')
        db.executemany('INSERT INTO items VALUES(?,?,?,?,?)',[
            ('a','g','PL001',str(source),'with_destinations'),
            ('b','g','PL001',str(source),'source_only'),
            ('c','missing','PL001',str(source),'source_only')])
    with sqlite3.connect(survey) as db:
        db.execute('CREATE TABLE groups(id,evidence_json)')
        db.execute('INSERT INTO groups VALUES(?,?)',('g',json.dumps(group)))
    before={p:p.read_bytes() for p in (catalog,survey,source)}
    summary=run(catalog,survey,tmp_path/'out',workers=1)
    assert summary['catalog_items']==3 and summary['catalog_groups']==2
    assert summary['groups_missing_survey']==1
    assert summary['repair_queue_items']==2
    findings=[json.loads(s) for s in (tmp_path/'out/findings.jsonl').read_text().splitlines()]
    assert findings[0]['catalog_items_with_destinations']==1
    assert all(p.read_bytes()==content for p,content in before.items())


def test_missing_source_is_explicit(tmp_path):
    source=tmp_path/'absent.txt';raw='Name, II, 152'
    result=review_source((str(source),[fixture_group(source,raw)]))
    assert result['span_states']=={'source_unavailable':1}
    assert result['error']
