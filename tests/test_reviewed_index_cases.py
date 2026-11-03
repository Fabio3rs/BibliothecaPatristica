from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest
from test_jev_index_results import bundle
from scripts import repair_reviewed_index_cases as repair
from scripts import validate_index_jev as jev
from scripts.reviewed_index_evidence import identity, validate_reviewed_editorial
from scripts.resolve_alphabetical_unresolved import repair_block_reason


def make_plan(bundle,tmp_path,child=False):
    args,spec,_=bundle
    with closing(jev.connect_readonly(args.db)) as c:
        ref=dict(c.execute('SELECT * FROM alphabetical_refs').fetchone())
        entry=dict(c.execute('SELECT * FROM alphabetical_entries').fetchone())
    source=entry['index_entry_source_ocr_file'] or spec['files'][0]['file']
    raw={'reviewed_editorial':{'method':repair.METHOD,'reason':'verified source','block_reason':None}}
    p={'ref_id':ref['ref_id'],'volume_id':'PG001','old_ref':ref,'old_entry':entry,'raw':raw,
       'sources':[repair.source_proof(source)],'fields':{'ref_kind':'editorial_page_line','ref_raw':'101, 3',
            **repair.location_fields(101,3)},'child':None}
    if child:
        p['child']={'lemma':'Athanasius','text':'Athanasius contra Arianos disputavit.','source_file':source,
                    'line_start':1,'line_end':1,'text_sha256':repair.checksum(source)}
    path=tmp_path/'plan.json'
    repair.write_new(path,{'method':repair.METHOD,'changes':[p]})
    return args,path,p


def test_transaction_archive_and_replay(bundle,tmp_path):
    args,path,p=make_plan(bundle,tmp_path,child=True)
    result=repair.apply_plan(args.db,path,tmp_path/'before.sqlite',tmp_path/'apply.json')
    assert result['applied_refs']==result['new_entries']==1
    with sqlite3.connect(args.db) as c:
        row=c.execute('SELECT ref_raw,entry_key,ref_kind,cited_editorial_line_start_number FROM alphabetical_refs').fetchone()
        assert row[0]=='101, 3' and ':review:' in row[1] and row[2:] == ('editorial_page_line',3)
        archived=json.loads(c.execute('SELECT original_json FROM alphabetical_reviewed_patches').fetchone()[0])
        assert archived['ref']==p['old_ref'] and not c.execute('PRAGMA foreign_key_check').fetchall()
    again=repair.apply_plan(args.db,path,tmp_path/'before2.sqlite',tmp_path/'again.json')
    assert again['applied_refs']==0 and again['already_applied']==1


@pytest.mark.parametrize('change',['source','row','entry'])
def test_changed_inputs_abort_before_backup(bundle,tmp_path,change):
    args,path,p=make_plan(bundle,tmp_path)
    if change=='source':Path(p['sources'][0]['file']).write_text('changed')
    else:
        with sqlite3.connect(args.db) as c:
            c.execute("UPDATE alphabetical_refs SET ref_raw='different'" if change=='row' else
                      "UPDATE alphabetical_entries SET lemma_raw='different'")
    before=args.db.read_bytes()
    with pytest.raises(ValueError):repair.apply_plan(args.db,path,tmp_path/'before.sqlite',tmp_path/'apply.json')
    assert args.db.read_bytes()==before and not (tmp_path/'before.sqlite').exists()


def test_failure_rolls_back_all_changes(bundle,tmp_path,monkeypatch):
    args,path,_=make_plan(bundle,tmp_path)
    def fail(*a):raise ValueError('quality failed')
    monkeypatch.setattr(repair,'refresh_volume_quality',fail)
    with pytest.raises(ValueError):repair.apply_plan(args.db,path,tmp_path/'before.sqlite',tmp_path/'apply.json')
    with sqlite3.connect(args.db) as c:
        assert c.execute('SELECT ref_raw FROM alphabetical_refs').fetchone()[0]=='101'
    assert (tmp_path/'before.sqlite').exists() and not (tmp_path/'apply.json').exists()


def test_reviewed_marker_bypasses_wrong_header_but_checks_identity_and_hash(bundle):
    args,spec,_=bundle
    row=spec['row'];file=spec['files'][0]['file']
    row.update(ref_raw='51',page_ref_int=51,cited_editorial_page_start_number=51)
    proof={'method':'facsimile_review_v1','identity':identity(row),'file':file,'target_sha256':repair.checksum(file),
           'cited_number':51,'candidate_source':'internal-marker','sources':[repair.source_proof(file)]}
    row['raw_json']=json.dumps({'reviewed_location':proof})
    result,payload=jev.prepare(spec,args)
    assert payload and result['candidate_source']=='internal-marker' and not result['needs_review']
    row['lemma_raw']='changed'
    result,payload=jev.prepare(spec,args)
    assert payload is None and result['skip_reason']=='reviewed_location_identity_changed'
    row['lemma_raw']='Athanasius'
    Path(file).write_text('changed')
    result,payload=jev.prepare(spec,args)
    assert payload is None and result['skip_reason']=='reviewed_location_source_changed'


def test_reviewed_decision_supersedes_legacy_block():
    row={'raw_json':json.dumps({'deterministic_repair':{'block_reason':'old_group'},
                               'reviewed_editorial':{'block_reason':None}})}
    assert repair_block_reason(row) is None
    row['raw_json']=json.dumps({'reviewed_editorial':{'block_reason':'editorial_namespace_requires_mapping'}})
    assert repair_block_reason(row)=='editorial_namespace_requires_mapping'


def test_reviewed_editorial_rejects_changed_identity_or_source(bundle):
    _,spec,_=bundle
    row=spec['row']; source=Path(spec['files'][0]['file'])
    row['raw_json']=json.dumps({'reviewed_editorial':{
        'identity':identity(row),'sources':[repair.source_proof(source)]}})
    validate_reviewed_editorial(row)
    row['lemma_raw']='Changed'
    with pytest.raises(ValueError,match='identity_changed'):
        validate_reviewed_editorial(row)
    row['lemma_raw']='Athanasius'
    source.write_text('Changed')
    with pytest.raises(ValueError,match='source_changed'):
        validate_reviewed_editorial(row)


def test_report_refresh_preserves_other_corrections_and_clears_line_candidates(bundle,tmp_path):
    args,spec,_=bundle
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE alphabetical_volumes SET volume_id='PG003'")
        con.execute("UPDATE alphabetical_sections SET volume_id='PG003'")
        con.execute("UPDATE alphabetical_refs SET ref_id=347582,ref_kind='target_locator'")
        for ref_id in (347583,347584):
            con.execute("INSERT INTO alphabetical_refs (ref_id,entry_key,ref_order,ref_kind,ref_raw,raw_json) "
                        "VALUES (?,'e',?,'target_locator','9','{}')",(ref_id,ref_id))
    with closing(jev.connect_readonly(args.db)) as con:
        rows=[dict(r) for r in con.execute(jev.QUERY,('PG003',))]
    base=tmp_path/'base';previous=tmp_path/'previous';base.mkdir();previous.mkdir()
    old={'ref_id':347584,'reason':'unrelated_correction','proposal':None}
    original={'job':{'volume_id':'PG003','rows':rows},'records':[old]}
    (base/'PG003.json').write_text(json.dumps(original))
    prior=previous/'PG003.json';prior.write_text(json.dumps(original))
    before=prior.read_bytes()
    plan_path=tmp_path/'refresh-plan.json'
    repair.write_new(plan_path,{'changes':[]})
    result=repair.refresh_reports(args.db,plan_path,previous,base,previous)
    updated=json.loads(prior.read_text())
    records={r['ref_id']:r for r in updated['records']}
    assert records[347584]==old and result[0]['updated_refs']==2
    assert records[347582]['locator_result']['file_search']['candidates']==[]
    assert records[347583]['proposal'] is None
    assert list(previous.glob('*.before-*'))[0].read_bytes()==before


@pytest.mark.parametrize('text,accept',[('ἔξαρχος 770, 9.',True),('ἄρκα arcus 856, 6, Leo 319, 10.',True),
    ('ἔξαρχος 770, 9; alius 12, 4.',False),('770, 9.',False),('ἔξαρχος 770.',False)])
def test_whole_entry_page_line_grammar(text,accept):
    assert bool(repair.PAIR.fullmatch(text))==accept
