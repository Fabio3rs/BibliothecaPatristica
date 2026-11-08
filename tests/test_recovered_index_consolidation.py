from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.recovered_index_consolidation import adapt_entry, merge_page, run
from tools.indexing.structured_index_experiment import encoded, SELECTION_VERSION


def entry(raw, start, text, ref, source):
    end = start+len(text)
    x = raw.index(ref, start)
    return {'id': str(start), 'source': str(source), 'physical_page': 1,
            'entry_raw': text, **source_span(raw,start,end), 'text_raw': text.split(',')[0],
            'reference_span': source_span(raw,x,x+len(ref)), 'references_raw': ref}


def test_legacy_owner_requires_exact_prefix_and_keeps_original(tmp_path):
    raw='Alpha, 17.'; e=entry(raw,0,raw,'17',tmp_path/'page')
    a=adapt_entry(e,raw,str(tmp_path/'page'),1)
    assert a['reference_groups'][0]['owner_text_raw']=='Alpha'
    assert a['original_extraction']==e
    e['text_raw']='Reconstructed owner'
    a=adapt_entry(e,raw,str(tmp_path/'page'),1)
    assert not a['reference_groups']
    assert a['pending_reference_groups'][0]['raw']=='17'


def test_duplicate_literals_at_different_positions_survive_and_overlap_fails(tmp_path):
    raw='Alpha, 17.\nAlpha, 17.'; source=str(tmp_path/'page')
    old=entry(raw,0,'Alpha, 17.','17',source)
    new=entry(raw,11,'Alpha, 17.','17',source)
    new['source_sha256']=digest(raw.encode())
    page,_=merge_page(raw,source,1,{'entries':[old]},[new])
    assert len(page['entries'])==2
    with pytest.raises(ValueError,match='overlaps'):
        merge_page(raw,source,1,{'entries':[new]},[new])


def test_residual_subtraction_preserves_unclaimed_prefix_and_suffix(tmp_path):
    raw='before Alpha, 17. after'; source=str(tmp_path/'page')
    e=entry(raw,7,'Alpha, 17.','17',source); e['source_sha256']=digest(raw.encode())
    baseline={'entries':[], 'residuals':[{'raw':raw,**source_span(raw,0,len(raw)), 'reason':'pending'}]}
    page,_=merge_page(raw,source,1,baseline,[e])
    assert [r['raw'] for r in page['residuals']]==['before ',' after']


def fixture(tmp_path):
    source=tmp_path/'teste/PG001/text/page-1.txt';source.parent.mkdir(parents=True)
    raw='Alpha, 17.\nBeta, 19.';source.write_text(raw)
    old=entry(raw,0,'Alpha, 17.','17',source)
    baseline={'source':str(source),'sha256':digest(raw.encode()),'physical_page':1,
              'entries':[old],'residuals':[],'events':[]}
    db=tmp_path/'parser.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE tasks(fingerprint TEXT,result_json TEXT);CREATE TABLE run_pages(run_id TEXT,source TEXT,fingerprint TEXT);')
        c.execute('INSERT INTO tasks VALUES(?,?)',('fp',json.dumps({'page':baseline})))
        c.execute('INSERT INTO run_pages VALUES(?,?,?)',('run',str(source),'fp'))
    audit=tmp_path/'audit';audit.mkdir()
    (audit/'manifest.json').write_text(json.dumps({'parser_db':str(db),'parser_run':'run'}))
    campaign=tmp_path/'campaign';campaign.mkdir()
    (campaign/'manifest.json').write_text(json.dumps({'audit':str(audit),'parser_run':'run'}))
    meta={'source':str(source),'source_sha256':baseline['sha256'],'physical':1,'status':'evaluated',
          'old_entries_sha256':digest(encoded([old]).encode()),'old_entries':1}
    (campaign/'page_comparison.jsonl').write_text(json.dumps(meta)+'\n')
    new=entry(raw,11,'Beta, 19.','19',source);new['source_sha256']=baseline['sha256']
    (campaign/'proposals.jsonl').write_text(json.dumps(new)+'\n')
    return campaign,source,baseline,db


def test_fresh_sqlite_and_explicit_page_override_do_not_union_versions(tmp_path):
    campaign,source,baseline,db=fixture(tmp_path)
    before=source.read_bytes(),db.read_bytes()
    result=run(campaign,tmp_path/'merged',root=tmp_path)
    assert result['units']==2 and result['effective_reference_groups']==2
    baseline['entries']=[adapt_entry(e,source.read_text(),str(source),1) for e in baseline['entries']]
    artifact=tmp_path/'review.json';artifact.write_text(json.dumps(baseline))
    manifest=tmp_path/'selection.json';manifest.write_text(json.dumps({'version':SELECTION_VERSION,'sources':[
        {'source':str(source),'sha256':baseline['sha256'],'physical':1,'artifact':str(artifact)}]}))
    result=run(campaign,tmp_path/'selected',overrides=manifest,root=tmp_path)
    assert result['units']==1 and result['decisions']=={'explicit_page_override':1}
    with sqlite3.connect(tmp_path/'selected/structured/index.sqlite') as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert before==(source.read_bytes(),db.read_bytes())


def test_stale_source_refused_before_output(tmp_path):
    campaign,source,_,_=fixture(tmp_path);source.write_text('modified')
    with pytest.raises(ValueError,match='Stale source'):
        run(campaign,tmp_path/'out',root=tmp_path)
    assert not (tmp_path/'out').exists()


def test_regional_accounting_mismatch_blocks_consolidation(tmp_path):
    campaign,source,_,_=fixture(tmp_path)
    path=campaign/'page_comparison.jsonl'
    meta=json.loads(path.read_text())
    meta['section_stats']=[{'span':[0,len(source.read_text())]}]
    meta['after']={'consumed_by_candidate':999999}
    path.write_text(json.dumps(meta)+'\n')
    with pytest.raises(ValueError,match='regional character accounting'):
        run(campaign,tmp_path/'out',root=tmp_path)
    assert not (tmp_path/'out').exists()


def test_unproven_owner_survives_in_pending_table(tmp_path):
    campaign,source,_,db=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        data=json.loads(c.execute('SELECT result_json FROM tasks').fetchone()[0])
        data['page']['entries'][0]['text_raw']='unproven'
        c.execute('UPDATE tasks SET result_json=?',(json.dumps(data),))
    path=campaign/'page_comparison.jsonl';meta=json.loads(path.read_text())
    meta['old_entries_sha256']=digest(encoded(data['page']['entries']).encode())
    path.write_text(json.dumps(meta)+'\n')
    result=run(campaign,tmp_path/'out',root=tmp_path)
    assert result['units']==2 and result['pending_reference_groups']==1
    assert result['reference_groups']==1
    with sqlite3.connect(tmp_path/'out/structured/index.sqlite') as c:
        pending=json.loads(c.execute('SELECT evidence_json FROM pending_reference_groups').fetchone()[0])
        assert pending['raw']=='17' and pending['reason']=='legacy_owner_span_not_proven'
