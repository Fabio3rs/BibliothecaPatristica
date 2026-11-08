"""Offline window planning and mocked HTTP; no paid endpoints."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from test_jev_index_results import bundle
from test_audit_pg040_concordance import table_xml
from scripts import deepscan_index_jev as deep
from scripts.jev_index_results import read_evaluations


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError('Unexpected HTTP')
    monkeypatch.setattr(requests.Session, 'post', fail)
    monkeypatch.setattr(requests.Session, 'get', fail)


@pytest.fixture
def material(bundle, tmp_path, monkeypatch):
    old,spec,_=bundle
    root=Path(spec['root'])
    (root/'page-426.txt').write_text('Numeri signant paginas editionis Antverpiensis')
    (root/'page-427.txt').write_text(table_xml())
    for p in range(253,261):
        (root/f'page-{p}.txt').write_text(
            '<pagina><bloco tipo="cabecalho">999 HEADER</bloco>'
            f'<bloco tipo="texto_principal" script="grego">λόγος {p-244} λόγος περί ανθρώπου.</bloco>'
            '<bloco tipo="texto_principal" script="latino">Athanasius contra Arianos disputavit. '
            'Non est lex talionis necessaria.</bloco></pagina>')
    with sqlite3.connect(old.db) as con:
        con.execute("UPDATE alphabetical_volumes SET volume_id='PG040'")
        con.execute("UPDATE alphabetical_sections SET volume_id='PG040',file_start=?",(str(root/'page-426.txt'),))
        con.execute("UPDATE alphabetical_entries SET entry_raw='Athanasius 11'")
        con.execute("UPDATE alphabetical_refs SET page_ref_int=11,ref_raw='11'")
    con.close()
    args=deep.parser().parse_args(['--db',str(old.db),'--root',str(root),'--controls','0',
                                 '--workers','1','--log',str(tmp_path/'deep.jsonl')])
    monkeypatch.setenv('AI_GATEWAY_API_KEY','local-test-key-never-log')
    return args


def records(args):
    return [json.loads(line) for line in args.log.read_text().splitlines()]


def fake_http(monkeypatch, values, on_call=None):
    calls=[]
    def post(session,url,**kwargs):
        calls.append(kwargs['json'])
        if on_call:
            on_call(len(calls))
        value=values.pop(0)
        if isinstance(value,Exception):
            raise value
        if isinstance(value,dict):
            status=value.get('status',200)
            body=value.get('body',{})
        else:
            status=200
            body={'answers':{'topic_present':{'probability':value}},
                  'usage':{'inputTokens':100,'outputTokens':2},
                  'providerMetadata':{'gateway':{'cost':.001,'marketCost':.001}}}
        return SimpleNamespace(status_code=status,json=lambda:body,headers={})
    monkeypatch.setattr(requests.Session,'post',post)
    return calls


def test_no_api_prepares_full_windows_without_key_or_pool(material,monkeypatch):
    args=material;args.no_api=True;args.limit=0;args.workers=4
    monkeypatch.delenv('AI_GATEWAY_API_KEY')
    monkeypatch.setattr(deep,'run_pool',lambda *a: pytest.fail('Pool must not start'))
    before=args.db.read_bytes()
    summary=deep.run(args)
    assert summary['calls']==0 and summary['prepared']==2
    assert summary['window_statuses']=={'prepared':1} and summary['db_updates']==0
    assert before==args.db.read_bytes()
    assert not read_evaluations([args.log])


def test_payload_full_ocr_one_question_and_multi_positive_no_early_stop(material,monkeypatch):
    args=material;args.include_payload=True
    calls=fake_http(monkeypatch,[.96,.88])
    before=args.db.read_bytes()
    summary=deep.run(args)
    assert summary['calls']==2 and summary['window_statuses']=={'multiple_positives':1}
    for payload,r in zip(calls,[r for r in records(args) if r['record_type']=='deepscan_association']):
        assert list(payload['questions'])==['topic_present']
        assert payload['questions']['topic_present']['type']=='boolean'
        assert payload['state'].endswith(Path(r['ocr_file']).read_text())
        assert r['needs_review'] and not r['promotion_allowed']
    assert before==args.db.read_bytes()
    assert 'local-test-key-never-log' not in args.log.read_text()


@pytest.mark.parametrize('limit',[0,1])
def test_limit_never_turns_partial_window_into_winner(material,monkeypatch,limit):
    args=material;args.limit=limit
    calls=fake_http(monkeypatch,[.99])
    summary=deep.run(args)
    window=next(r for r in records(args) if r['record_type']=='window_result')
    assert len(calls)==limit and summary['stop_reason']=='limit'
    assert not window['complete'] and window['single_positive_file'] is None


@pytest.mark.parametrize('scores,status',[([.9,.1],'single_positive'),([.1,.2],'no_positive')])
def test_window_classification(material,monkeypatch,scores,status):
    fake_http(monkeypatch,scores)
    summary=deep.run(material)
    assert summary['window_statuses']=={status:1}


def test_resume_restores_window_and_uses_no_calls_even_with_zero_limit(material,monkeypatch,tmp_path):
    args=material
    calls=fake_http(monkeypatch,[.9,.1])
    deep.run(args)
    args.resume_log=[args.log];args.log=tmp_path/'resume.jsonl';args.limit=0
    summary=deep.run(args)
    assert summary['calls']==0 and summary['cache_hits']==2 and len(calls)==2
    assert summary['window_statuses']=={'single_positive':1}


def test_strict_validation_cache_can_be_reused_but_strict_reader_excludes_deepscan(material,monkeypatch,tmp_path):
    args=material;fake_http(monkeypatch,[.9,.1]);deep.run(args)
    assert read_evaluations([args.log])=={}
    strict=tmp_path/'strict.jsonl'
    strict.write_text('\n'.join(json.dumps({**r,'record_type':'association'}) for r in records(args)
                               if r['record_type']=='deepscan_association')+'\n')
    args.resume_log=[strict];args.log=tmp_path/'from_strict.jsonl'
    summary=deep.run(args)
    assert summary['cache_hits']==2 and summary['calls']==0


def test_source_changed_during_run_invalidates_window_and_blocks_dispatch(material,monkeypatch):
    args=material
    def mutate(n):
        (args.root/'page-256.txt').write_text('Changed source')
    calls=fake_http(monkeypatch,[.99],mutate)
    summary=deep.run(args)
    assert len(calls)==1 and summary['window_statuses']=={'incomplete':1}
    window=next(r for r in records(args) if r['record_type']=='window_result')
    assert window['invalid_reason']=='source_changed_since_planning'


def test_db_changed_during_run_invalidates_window(material,monkeypatch):
    args=material
    def mutate(n):
        with sqlite3.connect(args.db) as con:
            con.execute("UPDATE alphabetical_entries SET lemma_raw='Different'")
    calls=fake_http(monkeypatch,[.99],mutate)
    summary=deep.run(args)
    assert len(calls)==1 and summary['complete_windows']==0
    assert any(r.get('invalid_reason')=='reference_changed_since_planning' for r in records(args))


def test_large_payload_is_not_truncated_or_sent(material,monkeypatch):
    args=material;args.max_input_bytes=100
    summary=deep.run(args)
    assert summary['calls']==0 and summary['statuses']=={'ocr_unavailable':2}
    assert summary['complete_windows']==0


@pytest.mark.parametrize('error',[requests.Timeout(), {'status':200,'body':{'answers':{}}}])
def test_api_failure_leaves_incomplete_window(material,monkeypatch,error):
    fake_http(monkeypatch,[error])
    summary=deep.run(material)
    assert summary['calls']==1 and summary['window_statuses']=={'incomplete':1}
    assert summary['stop_reason']=='cost_unavailable'


def test_429_retry_counts_against_global_limit(material,monkeypatch):
    args=material;args.limit=2
    retry={'status':429,'body':{'providerMetadata':{'gateway':{'cost':0}}}}
    calls=fake_http(monkeypatch,[retry,.9])
    monkeypatch.setattr(deep.jev.time,'sleep',lambda *_:None)
    summary=deep.run(args)
    assert len(calls)==2 and summary['evaluated']==1 and summary['window_statuses']=={'incomplete':1}


def test_cost_stops_serial_dispatch(material,monkeypatch):
    args=material;args.max_cost_usd=.0001
    calls=fake_http(monkeypatch,[.9])
    summary=deep.run(args)
    assert len(calls)==1 and summary['stop_reason']=='cost_budget'


def test_outer_anchor_window_and_cap_are_mechanical(material):
    args=material
    p=args.root/'page-255.txt'
    p.write_text(p.read_text().replace('λόγος 11 λόγος','λόγος λόγος'))
    windows,_,_=deep.build_plan(args)
    assert windows[0]['kind']=='relaxed_outer_anchors'
    assert [p['physical_page'] for p in windows[0]['files']]==[254,255,256]
    args.max_files=2
    windows,exclusions,_=deep.build_plan(args)
    assert not windows and exclusions[0]['reason']=='window_exceeds_cap_not_truncated'


def test_ambiguous_editorial_entry_is_excluded(material):
    args=material
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE alphabetical_refs SET ref_raw='11 (forte Aristoteles, 12)'")
    windows,exclusions,_=deep.build_plan(args)
    assert not windows and exclusions[0]['reason']=='editorial_qualification_requires_review'


def test_source_guard_detects_replacement_and_escape(material,tmp_path):
    path=material.root/'page-255.txt'
    guard=deep.SourceGuard([{'file':str(path),'sha256':deep.jev.sha(path.read_bytes())}],material.root)
    guard.check();path.write_text(path.read_text()+'changed')
    with pytest.raises(ValueError,match='changed'):
        guard.check()
    with pytest.raises(ValueError,match='outside_volume'):
        deep.SourceGuard([{'file':str(tmp_path/'elsewhere'),'sha256':'x'}],material.root).check()


def test_control_label_never_enters_payload(material):
    windows,_,_=deep.build_plan(material)
    window=windows[0];window['control']=True
    window['expected_control_file']=window['files'][1]['file']
    results=[]
    for candidate in window['files']:
        result,payload=deep.prepare(window,candidate,material)
        assert 'expected_control' not in json.dumps(payload)
        deep.jev.classify(result,.9 if candidate==window['files'][1] else .1,material)
        results.append(result)
    group=deep.aggregate(window,results,material)
    assert group['control_positive_detected'] and group['complete']


def test_mixed_sampling_supports_all_window_categories():
    windows=[{'category':c,'id':i} for i,c in enumerate(
        ['bounded_name_hint','bounded_no_literal','relaxed_outer_anchors']*4)]
    one=deep.sample_windows(windows,42,'mixed')
    assert one==deep.sample_windows(windows,42,'mixed')
    assert one!=deep.sample_windows(windows,43,'mixed')
    assert len({w['category'] for w in one[:3]})==3
    assert sorted(w['id'] for w in one)==list(range(12))


def test_expired_preparation_deadline_sends_no_http(material):
    material.max_seconds=.000001
    summary=deep.run(material)
    assert summary['calls']==0 and summary['stop_reason']=='time_budget'
    assert summary['window_statuses']=={'incomplete':1}


def test_existing_log_is_not_overwritten(material):
    material.log.write_text('preserve')
    with pytest.raises(FileExistsError):
        deep.run(material)
    assert material.log.read_text()=='preserve'


@pytest.mark.parametrize('argv',[
    ['--apply'],['--max-files','0'],['--max-references','0'],['--limit','-1'],
    ['--max-cost-usd','nan'],['--threshold','nan'],['--workers','0'],['--controls','5'],['--tome','PL010']])
def test_cli_rejects_unsafe_or_unsupported_options(argv):
    with pytest.raises(SystemExit):
        deep.main(argv)
