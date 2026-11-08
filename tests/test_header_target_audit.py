import json
from pathlib import Path
import sqlite3
import pytest

from tools.indexing.entry_parser_experiment import digest
from tools.indexing.header_target_audit import classify,run,validate_registry,stratum,column_evidence


def test_rule_is_regional_and_does_not_propagate_past_boundary():
    rule={'id':'r','kind':'internal_text_numbering'};region={'char_start':20,'char_end':40}
    assert classify({'char_start':25,'char_end':30,'owner_raw':'Alpha'},[(rule,region)])['issues']==['internal_numbering_conflicts_with_header_route']
    assert classify({'char_start':40,'char_end':42,'owner_raw':'Alpha'},[(rule,region)])['status']=='namespace_unverified'
    assert classify({'char_start':18,'char_end':22,'owner_raw':'Alpha'},[(rule,region)])['status']=='namespace_unverified'


def test_roman_in_owner_is_review_not_automatic_volume_conversion():
    for owner in ['Opera, CXXXIX','Opus, II,']:
        r=classify({'char_start':0,'char_end':4,'owner_raw':owner},[])
        assert r['issues']==['roman_locator_in_owner_requires_review']
        assert not r['automatic_destination_mapping']
    assert not classify({'char_start':0,'char_end':4,'owner_raw':'Gregorius IV'},[])['issues']


def test_multiple_file_strata_and_stale_files_remain_separate():
    assert [stratum(n) for n in [0,1,2,5,21]]==['none','single','2_4','5_20','over20']
    r=classify({'char_start':0,'char_end':1,'owner_raw':'Alpha'},[],False,False)
    assert r['issues']==['source_changed','target_changed'] and not r['verified']


def test_audit_is_separate_and_source_database_unchanged(tmp_path):
    survey=tmp_path/'survey';survey.mkdir()
    source=tmp_path/'source.txt';source.write_text('Alpha 12')
    target=tmp_path/'target.txt';target.write_text('header 12')
    g={'id':'g','source':str(source),'source_sha256':digest(source.read_bytes()),'volume':'PG001',
       'owner_raw':'Alpha','char_start':6,'char_end':8}
    o={'id':'o','group_id':'g','number':12,'char_start':6,'char_end':8,
       'files':[{'path':str(target),'physical':2,'sha256':digest(target.read_bytes())}]}
    db=survey/'survey.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE groups(id TEXT,volume TEXT,evidence_json TEXT); CREATE TABLE occurrences(id TEXT,group_id TEXT,tier TEXT,evidence_json TEXT); CREATE TABLE candidates(occurrence_id TEXT);')
        c.execute('INSERT INTO groups VALUES(?,?,?)',('g','PG001',json.dumps(g)))
        c.execute('INSERT INTO occurrences VALUES(?,?,?,?)',('o','g','unmapped_namespace_screen',json.dumps(o)))
        c.execute('INSERT INTO candidates VALUES(?)',('o',))
    (survey/'config.json').write_text('{}')
    rules=tmp_path/'rules.json';rules.write_text(json.dumps({'version':'header-target-audit-rules-v1','rules':[]}))
    before=db.read_bytes();result=run(survey,rules,tmp_path,tmp_path/'audit',False)
    assert result['by_candidate_count']['single']=={'total':1,'namespace_unverified':1}
    assert before==db.read_bytes() and result['verified_links']==0
    with sqlite3.connect(tmp_path/'audit/audit.sqlite') as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'


def test_changed_rules_cannot_silently_unblock_targets(tmp_path):
    p=tmp_path/'source';p.write_text('notice')
    rules={'version':'header-target-audit-rules-v1','rules':[{'proofs':[{'path':str(p),'sha256':'bad','char_start':0,'char_end':6}],'source_regions':[{}]}]}
    f=tmp_path/'rules.json';f.write_text(json.dumps(rules))
    with pytest.raises(ValueError,match='Stale rule evidence'): validate_registry(f)


def test_current_columns_stop_at_internal_transition_and_conflicts_win():
    positive={'id':'p','kind':'current_edition_columns'}
    negative={'id':'n','kind':'internal_text_numbering'}
    rules=[(positive,{'char_start':0,'char_end':40}),
           (negative,{'char_start':40,'char_end':100})]
    group={'char_start':20,'char_end':25,'owner_raw':'Alpha'}
    r=classify(group,rules)
    assert r['status']=='documented_column_namespace' and not r['verified']
    assert classify({**group,'char_start':41,'char_end':45},rules)['status']=='review_required'
    assert classify(group,rules+[(negative,{'char_start':0,'char_end':100})])['status']=='review_required'
    assert classify({**group,'owner_raw':'Opera, III'},rules)['status']=='review_required'


def test_column_candidates_preserve_alternatives_and_require_header_support():
    group={'raw':'12, a; 13, b','flags':['unvalidated_profile']}
    occurrence={'number':12,'files':[{'path':'a','sha256':'h'},{'path':'b','sha256':'h2'}]}
    marker={'zone':'cabecalho','sha256':'h','sequence_support':{'previous':True}}
    markers={(12,'a'):[marker],(12,'b'):[{**marker,'sha256':'h2'}]}
    r=column_evidence(group,occurrence,markers)
    assert r['status']=='documented_namespace_candidate' and len(r['column_candidates'])==2
    assert r['source_assignment_status']=='not_certified'
    markers[(12,'b')][0]['zone']='rodape'
    r=column_evidence(group,occurrence,markers)
    assert r['status']=='documented_namespace_marker_review'
    assert len(r['column_candidates'])==2
    assert column_evidence({**group,'raw':'12-19'},occurrence,markers)['status']=='documented_namespace_notation_pending'
    assert column_evidence(group,{**occurrence,'files':[]},markers)['status']=='documented_namespace_no_candidate'
