from copy import deepcopy
import json
import random
import threading
from pathlib import Path

import pytest
from PIL import Image

from tools.knowledge_graph.playground.common import digest, dumps, readonly
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.experiment_runner import prepare, execute_experiment
from tools.knowledge_graph.playground.ab_runner import prepare_ab, execute_ab, check_schedule, verify_source
from tools.knowledge_graph.playground.ab_reporting import write_report
from tools.knowledge_graph.playground.validation_replay import replay, historical_fingerprint


@pytest.fixture
def frozen(tmp_path):
    store=Store(tmp_path/'playground.db')
    campaign=store.experiment('Synthetic campaign',{})
    image=tmp_path/'page.png';Image.new('RGB',(24,32),'white').save(image)
    config={'provider':'meta','model':'muse-spark-1.3-contributor','max_output_tokens':32768}
    directory=tmp_path/'audit';directory.mkdir()
    cases=[];schedule=[]
    for i in range(24):
        snap=demo_snapshot();snap['source']['physical_page']=i+1
        snap['ocr_raw']='\n'.join(b['text'] for b in snap['ocr']['blocks'])
        snap['source']['ocr_sha256']=digest(snap['ocr_raw'])
        snap['facsimile']={'available':True,'path':str(image),'sha256':digest(image.read_bytes())}
        case=store.case(snap)
        p=prepare(case,config,variant='concepts-v2.2-confidence',facsimile='on')
        rid=store.add_run(campaign,case,p,1)
        store.start(rid)
        store.finish(rid,'completed',{'raw_response':dumps(empty()),'cost_usd':.003,'usage':{}})
        cases.append({'run_id':rid,'page':case['page'],'input_hash':p['input_hash'],
                      'snapshot_hash':case['snapshot_hash'],'generation_id':snap['source']['v2_generation_id'],
                      'ocr_sha256':snap['source']['ocr_sha256'],'image_sha256':snap['facsimile']['sha256']})
        for arm in ('A','B'):
            for rep in (1,2):schedule.append({'page':case['page'],'baseline_run_id':rid,'arm':arm,'repetition':rep})
    random.Random(20261101).shuffle(schedule)
    plan={'seed':20261101,'cases':cases,'schedule':schedule,'paid_requests_if_authorized':96,
          'schema_hash':p['schema_hash'], 'arms':{'A':{'system_hash':p['system_hash'],'variant':'concepts-v2.2-confidence'},
                                              'B':{'system_hash':'historical_proposal'}},
          'config':dict(config,mode='concepts_relations',facsimile='on',temperature=None)}
    (directory/'stored_system_prompt.txt').write_text(p['system_prompt'])
    (directory/'ab_plan.json').write_text(dumps(plan))
    return store,campaign,directory,plan


def empty():
    return dict(schema_version='kg-extraction-v2',concepts=[],entities=[],claims=[],events=[],relations=[],evidence=[],warnings=[])


def fake(config,request):
    assert 'reasoning_effort' not in request['body']
    return {'raw_response':dumps(empty()),'usage':{'input_tokens':100,'output_tokens':40,'reasoning_tokens':20},
            'latency_seconds':.01,'finish_reason':'stop','attempts':1,'provider_response':{}}


def build(frozen,**kwargs):
    store,campaign,directory,_=frozen
    return prepare_ab(store,directory,campaign=campaign,benchmark_dir=directory/'absent',**kwargs)


@pytest.fixture
def stance_sources(frozen):
    store,campaign,directory,audit=frozen
    prior=build(frozen)['experiment']
    execute_ab(store,prior,concurrency=4,caller=fake)
    (directory/'sample_manifest.json').write_text(dumps({'pages':audit['cases'],'excluded_holdout_volumes':[]}))
    original=store.get_run(audit['cases'][0]['run_id'])
    old=store.get_case(original['case_id'])
    for number in range(100,108):
        snap=deepcopy(old['snapshot']);snap['source']['physical_page']=number
        case=store.case(snap)
        p=prepare(case,original['config'],variant='concepts-v2.2-confidence',facsimile='on')
        run=store.add_run(campaign,case,p,1)
        store.start(run)
        store.finish(run,'invalid' if number%2 else 'completed',dict(fake(p['config'],p['request']),cost_usd=.003))
    return store,campaign,directory,prior


def test_stance_prompt_isolation_and_modes():
    from tools.knowledge_graph.playground.stance_study import study_templates,VARIANTS
    from tools.knowledge_graph.playground.prompt_registry import freeze_template,MODES
    templates=study_templates()
    assert templates['A']['system_hash']=='1ad5574d6d6c686769ec26514408282fdded3b4686147e3dbf65790560626c61'
    assert len({t['schema_hash'] for t in templates.values()})==1
    for arm,t in templates.items():
        text=t['system_prompt']
        assert text.count('FINAL CHECK\n')==text.count('MODEL-REPORTED CONFIDENCE —')==text.count('EXPERIMENT MODE\n')==1
        assert ('SYNTHETIC CONTRASTIVE EXAMPLES' in text)==(arm=='C')
        for name in ('Dodwell','Apollophanes','Agrippa','PG001','PG004'):
            assert name not in text
    for variant in VARIANTS.values():
        for mode,suffix in MODES.items():
            assert freeze_template(variant,mode)['system_prompt'].endswith(suffix)


def test_semantics_variant_preserves_schema_and_legacy_modes():
    from tools.knowledge_graph.playground.prompt_registry import freeze_template,MODES
    from tools.knowledge_graph.playground.semantics_study import semantics_templates,VARIANT
    from tools.knowledge_graph.playground.common import ROOT
    templates=semantics_templates();a,d=templates['A'],templates['D']
    modes=json.loads((ROOT/'tools/knowledge_graph/prompts/semantics_v2.5_modes.json').read_text())
    assert a['system_hash']=='1ad5574d6d6c686769ec26514408282fdded3b4686147e3dbf65790560626c61'
    assert templates['B']['system_hash']=='e70b555cf3541bb2fe43eb0b38187d27b442910b3734d3bf6d49653c67b61541'
    assert templates['C']['system_hash']=='663be04d0719e3ef322957e688c9fa11f017fac24d80dd1d6200c4b671ad971e'
    assert d['schema']==a['schema'] and d['schema_version']=='kg-extraction-v2'
    assert d['system_prompt'].startswith('PURPOSE AND OUTPUT SEMANTICS')
    assert 'SYNTHETIC CONTRASTIVE EXAMPLES' not in d['system_prompt']
    assert a['system_prompt'].split('\n\nEXPERIMENT MODE\n')[0] in d['system_prompt']
    for mode in MODES:
        text=freeze_template(VARIANT,mode)['system_prompt']
        assert text.count('EXPERIMENT MODE\n')==text.count('MODEL-REPORTED CONFIDENCE —')==1
        assert text.endswith(modes.get(mode,MODES[mode]))
    assert not d['variant']['few_shot']


@pytest.fixture
def prepared_abc(stance_sources):
    from tools.knowledge_graph.playground.stance_study import prepare_stance_study
    store,campaign,directory,prior=stance_sources
    abc=prepare_stance_study(store,directory,source_experiment=prior,benchmark_dir=directory/'absent')
    return store,campaign,directory,prior,abc


def frozen_experiment_state(store,exp):
    with readonly(store.path) as con:
        return digest({'experiment':dict(con.execute('SELECT * FROM experiments WHERE id=?',(exp,)).fetchone()),
                       'runs':[dict(r) for r in con.execute('SELECT * FROM runs WHERE experiment_id=? ORDER BY id',(exp,))],
                       'cases':[dict(r) for r in con.execute('''SELECT DISTINCT c.* FROM cases c JOIN runs r
                           ON r.case_id=c.id WHERE r.experiment_id=? ORDER BY c.id''',(exp,))]})


def test_four_arm_frozen_preparation_execution_and_reports(prepared_abc,tmp_path,monkeypatch):
    from collections import Counter
    from tools.knowledge_graph.playground.semantics_study import prepare_semantics_study
    from tools.knowledge_graph.playground.stance_study import schedule_cases
    from tools.knowledge_graph.playground.retrieval_diagnostic import prepare_judgments,evaluate_search
    from tools.knowledge_graph.playground import ab_reporting
    store,campaign,directory,prior,abc=prepared_abc
    source=abc['experiment'];before={e:frozen_experiment_state(store,e) for e in (campaign,prior,source)}
    def no_key(*_):pytest.fail('Preparation must not read credentials')
    with monkeypatch.context() as patch:
        patch.setattr('tools.knowledge_graph.playground.ab_runner.os.getenv',no_key)
        result=prepare_semantics_study(store,source,benchmark_dir=directory/'absent',output=tmp_path/'preflight.json')
    assert result['size']==256 and result['inference_calls']==0
    assert result['cases']==abc['cases'] and result['selection']==abc['selection']
    assert result['primary_comparison']=='D_minus_A'
    assert result['arms']['A']==abc['arms']['A'] and result['arms']['B']==abc['arms']['B'] and result['arms']['C']==abc['arms']['C']
    assert Counter(t['arm'] for t in result['schedule'])==dict.fromkeys('ABCD',64)
    assert result['schedule']==schedule_cases(result['cases'],result['seed'],arms=tuple('ABCD'))
    with pytest.raises(ValueError,match='already exists'):
        prepare_semantics_study(store,source,output=tmp_path/'preflight.json')
    originals={}
    for r in store.list_runs(source):
        p=store.get_run(r['id'])['prepared'];originals[(r['case_id'],p['ab']['arm'],r['repetition'])]=p
    exp=result['experiment'];calls=[]
    for r in store.list_runs(exp):
        p=store.get_run(r['id'])['prepared'];arm=p['ab']['arm']
        reference=originals[(r['case_id'],arm if arm!='D' else 'A',r['repetition'])]
        request=deepcopy(p['request']);request['body']['messages'][0]['content']=reference['system_prompt']
        assert request==reference['request']
        assert p['user_prompt']==reference['user_prompt'] and p['schema']==reference['schema']
        assert p['image_transport_sha256']==reference['image_transport_sha256']
        if arm!='D':assert p['input_hash']==reference['input_hash']
        assert 'reasoning_effort' not in p['request']['body']
    monkeypatch.setattr(store,'cached',lambda *_:pytest.fail('Independent repetitions must bypass cache'))
    def caller(c,r):calls.append(digest(r));return fake(c,r)
    execute_ab(store,exp,concurrency=3,max_calls=7,caller=caller)
    execute_ab(store,exp,concurrency=20,caller=caller)
    assert len(calls)==256 and set(Counter(calls).values())=={2}
    assert execute_ab(store,exp,caller=caller)['submitted']==0
    assert {e:frozen_experiment_state(store,e) for e in before}==before
    monkeypatch.setattr(ab_reporting,'ROOT',tmp_path)
    report=write_report(store.path,exp)
    path=tmp_path/'.work/kg_ab_semantics_v25/report.md'
    assert Path(report['report'])==path
    assert '32 páginas × 4 braços × 2 repetições = 256' in path.read_text()
    assert 'D_minus_A' in path.read_text() and 'concepts-v2.5-semantics-confidence' in path.read_text()
    assert set(report['automated'])==set('ABCD') and report['pending']==256
    data=json.loads(path.with_suffix('.json').read_text())
    assert len(data['paired_semantics_by_comparison'])==6 and 'D_minus_A' in data['paired_semantics_by_comparison']
    assert len(data['stability'])==128
    assert all(len(c)==16 for c in data['object_comparisons'].values())
    review=json.loads(path.with_name('report.review_template.json').read_text())
    assert all('misleading_verbalized_relations' in r['object_contract_review'] for r in review['reviews'])
    target=tmp_path/'search.json';prepare_judgments(store.path,exp,target)
    search=evaluate_search(store.path,exp,target)
    assert {r['arm'] for r in search['results']}=={'summary',*'ABCD'}
    assert all(r['metrics'] is None for r in search['results'])


@pytest.mark.parametrize('mutation',['executed','image','config','snapshot','holdout','prompt','schedule','policy'])
def test_semantics_preflight_fails_without_mutation(prepared_abc,mutation):
    from tools.knowledge_graph.playground.semantics_study import prepare_semantics_study
    store,_,directory,_,abc=prepared_abc;source=abc['experiment']
    row=next(r for r in store.list_runs(source) if store.get_run(r['id'])['prepared']['ab']['arm']=='A' and r['repetition']==1)
    run=store.get_run(row['id']);case=store.get_case(row['case_id']);p=run['prepared']
    if mutation=='executed':store.start(row['id'])
    elif mutation=='image':Path(case['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
    elif mutation=='snapshot':
        with store.connect() as con:con.execute("UPDATE cases SET snapshot_hash='changed' WHERE id=?",(case['id'],))
    elif mutation=='holdout':
        with store.connect() as con:con.execute("UPDATE cases SET split='blind_holdout' WHERE id=?",(case['id'],))
    elif mutation in ('schedule','policy'):
        plan=store.experiment_plan(source)
        if mutation=='schedule':plan['schedule'][0]=plan['schedule'][1]
        else:plan['validation']['matching_policy']='changed'
        with store.connect() as con:con.execute('UPDATE experiments SET plan_json=? WHERE id=?',(dumps(plan),source))
    else:
        if mutation=='prompt':p['system_prompt']+='changed'
        else:p['config']['reasoning_effort']='low'
        with store.connect() as con:con.execute('UPDATE runs SET prepared_json=? WHERE id=?',(dumps(p),row['id']))
    before=frozen_experiment_state(store,source);count=len(store.list_experiments())
    with pytest.raises(ValueError):prepare_semantics_study(store,source,benchmark_dir=directory/'absent')
    assert frozen_experiment_state(store,source)==before and len(store.list_experiments())==count


def test_stance_preparation_resume_reports_and_history(stance_sources,tmp_path,monkeypatch):
    from collections import Counter
    from tools.knowledge_graph.playground.stance_study import prepare_stance_study,schedule_cases
    from tools.knowledge_graph.playground.retrieval_diagnostic import prepare_judgments,evaluate_search
    store,campaign,directory,prior=stance_sources
    before=[historical_fingerprint(store.path,e) for e in (campaign,prior)]
    monkeypatch.setattr('tools.knowledge_graph.playground.ab_runner.send',lambda *_:pytest.fail('No live API'))
    preflight=prepare_stance_study(store,directory,source_experiment=prior,benchmark_dir=directory/'absent',output=tmp_path/'preflight.json')
    exp=preflight['experiment']
    assert preflight['size']==192 and preflight['inference_calls']==0
    assert Counter(c['stratum'] for c in preflight['cases'])=={'regression':24,'diagnostic_new_pages':8}
    assert preflight['schedule']==schedule_cases(preflight['cases'],preflight['seed'])
    assert Counter(t['arm'] for t in preflight['schedule'])=={'A':64,'B':64,'C':64}
    sources={c['run_id']:c for c in preflight['cases']}
    for r in store.list_runs(exp):
        p=store.get_run(r['id'])['prepared'];source=sources[p['ab']['baseline_run_id']]
        old=store.get_run(source['run_id'])['prepared']
        reconstructed=deepcopy(p['request']);reconstructed['body']['messages'][0]['content']=old['system_prompt']
        assert reconstructed==old['request']
        assert p['user_prompt']==old['user_prompt'] and p['schema']==old['schema']
        assert 'reasoning_effort' not in p['request']['body']
        if p['ab']['arm']=='A' and source['stratum']=='regression':assert p['request']==old['request']
    report=write_report(store.path,exp,tmp_path/'report.md')
    assert set(report['automated'])=={'A','B','C'} and report['pending']==192
    assert '32 páginas × 3 braços × 2 repetições = 192' in (tmp_path/'report.md').read_text()
    monkeypatch.setattr('tools.knowledge_graph.playground.ab_reporting.ROOT',tmp_path)
    default_report=write_report(store.path,exp)
    assert Path(default_report['report'])==tmp_path/'.work/kg_ab_stance_v24/report.md'
    assert not (tmp_path/'.work/kg_ab_navigation_v23/report.md').exists()
    with pytest.raises(ValueError,match='already exists'):
        prepare_stance_study(store,directory,source_experiment=prior,output=tmp_path/'preflight.json')
    calls=[];stop=threading.Event()
    def caller(c,r):
        calls.append(digest(r))
        if len(calls)==3:stop.set()
        return fake(c,r)
    monkeypatch.setattr(store,'cached',lambda *_:pytest.fail('Must bypass cache'))
    result=execute_ab(store,exp,concurrency=1,caller=caller,stop_event=stop)
    assert result['stopped'] and len(calls)==3
    execute_ab(store,exp,concurrency=20,max_calls=8,caller=caller)
    assert len(calls)==11
    execute_ab(store,exp,concurrency=20,caller=caller)
    execute_ab(store,exp,caller=caller)
    assert len(calls)==192 and set(Counter(calls).values())=={2}
    assert [historical_fingerprint(store.path,e) for e in (campaign,prior)]==before
    result=write_report(store.path,exp,tmp_path/'report.md')
    assert all(m['terminal_outputs']==64 for m in result['automated'].values())
    data=json.loads((tmp_path/'report.json').read_text())
    assert len(data['stability'])==96
    assert set(data['paired_semantics_by_comparison'])=={'B_minus_A','C_minus_A','C_minus_B'}
    assert len(data['object_comparisons'][next(iter(data['object_comparisons']))])==9
    template=json.loads((tmp_path/'report.review_template.json').read_text())
    assert len(template['reviews'])==192
    assert all('missing_supported_edges' in r['stance_review'] for r in template['reviews'])
    target=tmp_path/'judgments.json';result=prepare_judgments(store.path,exp,target)
    assert result['pending_judgments']==result['queries']*32
    retrieval=evaluate_search(store.path,exp,target)
    assert {r['arm'] for r in retrieval['results']}=={'summary','A','B','C'}
    assert all(r['metrics'] is None for r in retrieval['results'])
    from tools.knowledge_graph.playground import ab_reporting
    actual_metrics=ab_reporting.metrics
    with monkeypatch.context() as patch:
        def repetition_labels(run):
            return dict(actual_metrics(run),labels=[f"repetition {run['repetition']}"])
        patch.setattr(ab_reporting,'metrics',repetition_labels)
        write_report(store.path,exp,tmp_path/'ordered.md')
    ordered=json.loads((tmp_path/'ordered.json').read_text())
    assert all(s['only_rep1']==['repetition 1'] and s['only_rep2']==['repetition 2'] for s in ordered['stability'])


@pytest.mark.parametrize('mutation',['image','prompt','holdout','config','snapshot'])
def test_stance_preflight_fails_before_new_experiment(stance_sources,mutation):
    from tools.knowledge_graph.playground.stance_study import prepare_stance_study
    store,_,directory,prior=stance_sources
    row=next(r for r in store.list_runs(prior) if store.get_run(r['id'])['prepared']['ab']['arm']=='B' and r['repetition']==1)
    run=store.get_run(row['id']);case=store.get_case(row['case_id']);p=run['prepared']
    if mutation=='image':Path(case['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
    elif mutation=='holdout':
        with store.connect() as c:c.execute("UPDATE cases SET split='blind_holdout' WHERE id=?",(case['id'],))
    elif mutation=='snapshot':
        with store.connect() as c:c.execute("UPDATE cases SET snapshot_hash='changed' WHERE id=?",(case['id'],))
    else:
        if mutation=='prompt':p['system_prompt']+='change'
        else:p['config']['reasoning_effort']='low'
        with store.connect() as c:c.execute('UPDATE runs SET prepared_json=? WHERE id=?',(dumps(p),run['id']))
    before=len(store.list_experiments())
    with pytest.raises(ValueError):prepare_stance_study(store,directory,source_experiment=prior,benchmark_dir=directory/'absent')
    assert len(store.list_experiments())==before


def test_diagnostic_selection_excludes_audit_neighbors_and_holdout():
    from tools.knowledge_graph.playground.stance_study import select_diagnostic
    rows=[dict(id=n,page=f'PG001:{n}',status='completed' if n%2 else 'invalid',split=None) for n in range(1,30)]
    rows += [dict(id=100,page='PG009:100',status='completed',split=None),
             dict(id=101,page='PG001:101',status='completed',split='blind_holdout')]
    selected=select_diagnostic(rows,{'PG001:10'}, {'PG009'},8,1234)
    assert not {'PG001:9','PG001:10','PG001:11','PG009:100','PG001:101'} & {r['page'] for r in selected}
    assert selected==select_diagnostic(list(reversed(rows)),{'PG001:10'},{'PG009'},8,1234)
    assert len(selected)==8
    with pytest.raises(ValueError,match='eligible'):select_diagnostic(rows,{'PG001:10'},{'PG009'},100,1234)


def test_frozen_prepare_and_independent_execution(frozen,tmp_path,monkeypatch):
    store,campaign,_,_=frozen
    before=historical_fingerprint(store.path,campaign)
    preflight=build(frozen,output=tmp_path/'preflight.json')
    exp=preflight['experiment'];runs=store.list_runs(exp)
    assert len(runs)==96 and preflight['inference_calls']==0
    assert preflight['checks']['baseline_request_reproduced']
    grouped={}
    for r in runs:
        run=store.get_run(r['id']);p=run['prepared']
        grouped.setdefault((r['case_id'],r['repetition']),{})[p['ab']['arm']]=p
        assert 'reasoning_effort' not in p['request']['body']
        assert 'api_key' not in p['config']
        assert p['system_prompt'].count('EXPERIMENT MODE\n')==1
    for pair in grouped.values():
        a,b=pair['A'],deepcopy(pair['B'])
        b['request']['body']['messages'][0]['content']=a['system_prompt']
        assert b['request']==a['request']
        assert b['user_prompt']==a['user_prompt']
        assert b['schema']==a['schema']
    monkeypatch.setattr(store,'cached',lambda *_:pytest.fail('A/B must bypass cache'))
    with pytest.raises(ValueError,match='ab-run'):execute_experiment(store,exp)
    calls=[]
    def caller(c,r):calls.append(digest(r));return fake(c,r)
    execute_ab(store,exp,concurrency=2,max_calls=8,caller=caller)
    execute_ab(store,exp,concurrency=2,max_calls=8,caller=caller)
    assert len(calls)==16
    assert len([r for r in store.list_runs(exp) if r['status']=='planned'])==80
    execute_ab(store,exp,concurrency=3,caller=caller)
    assert len(calls)==96
    execute_ab(store,exp,caller=caller)
    assert len(calls)==96
    from collections import Counter
    assert set(Counter(calls).values())=={2}
    assert historical_fingerprint(store.path,campaign)==before
    report=write_report(store.path,exp,tmp_path/'report.md')
    assert report['automated']['A']['terminal_outputs']==48
    assert report['pending']==96
    data=json.loads((tmp_path/'report.json').read_text())
    assert len(data['page_pairs'])==24 and len(data['stability'])==48
    assert (tmp_path/'report.review_packets.jsonl').exists()
    assert 'independent' in data['conclusion']


@pytest.mark.parametrize('change',['schedule','snapshot','image','prompt','holdout','effort'])
def test_preflight_fail_closed_without_creating_experiment(frozen,change):
    store,campaign,directory,plan=frozen
    before=len(store.list_experiments())
    if change=='schedule':
        plan['schedule'][0]=plan['schedule'][1]
        (directory/'ab_plan.json').write_text(dumps(plan))
    elif change=='prompt':(directory/'stored_system_prompt.txt').write_text('changed')
    else:
        run=store.get_run(plan['cases'][0]['run_id']);case=store.get_case(run['case_id'])
        if change=='image':Path(case['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
        elif change=='holdout':
            with store.connect() as c:c.execute("UPDATE cases SET split='blind_holdout' WHERE id=?",(case['id'],))
        elif change=='snapshot':
            with store.connect() as c:c.execute("UPDATE cases SET snapshot_hash='wrong' WHERE id=?",(case['id'],))
        else:
            p=run['prepared'];p['config']['reasoning_effort']='low'
            with store.connect() as c:c.execute('UPDATE runs SET prepared_json=?,config_json=? WHERE id=?',(dumps(p),dumps(p['config']),run['id']))
    with pytest.raises(ValueError):build(frozen)
    assert len(store.list_experiments())==before


def test_graceful_interruption_and_hard_interruption(frozen):
    store,_,_,_=frozen;exp=build(frozen)['experiment'];stop=threading.Event();calls=[]
    def caller(c,r):calls.append(1);stop.set();return fake(c,r)
    result=execute_ab(store,exp,concurrency=1,caller=caller,stop_event=stop)
    assert result['stopped'] and len(calls)==1
    planned=sorted((r for r in store.list_runs(exp) if r['status']=='planned'),key=lambda r:r['id'])
    store.start(planned[0]['id'])
    execute_ab(store,exp,max_calls=1,caller=fake)
    assert store.get_run(planned[0]['id'])['status']=='interrupted'
    assert len(store.list_runs(exp))==96
    execute_ab(store,exp,max_calls=1,retry_interrupted=True,caller=fake)
    assert len(store.list_runs(exp))==97
    retried=[store.get_run(r['id']) for r in store.list_runs(exp) if r['id']>planned[-1]['id']]
    assert retried[0]['prepared']['retry_of']==planned[0]['id']
    assert retried[0]['status']=='completed'


def test_invalid_output_is_terminal_not_repaired(frozen):
    store,_,_,_=frozen;exp=build(frozen)['experiment'];calls=[]
    def caller(c,r):calls.append(1);return dict(fake(c,r),raw_response='{bad')
    execute_ab(store,exp,max_calls=1,caller=caller)
    execute_ab(store,exp,max_calls=1,caller=caller)
    assert len(calls)==2
    assert len([r for r in store.list_runs(exp) if r['status']=='invalid'])==2


def test_offline_replay_is_readonly(frozen,tmp_path):
    store,campaign,_,_=frozen;before=historical_fingerprint(store.path,campaign)
    result=replay(store.path,campaign,tmp_path/'locator.md',expected_runs=24)
    assert result['history_unchanged'] and result['runs']==24
    assert historical_fingerprint(store.path,campaign)==before
    with readonly(store.path) as c:
        assert c.execute('SELECT count(*) FROM run_validation').fetchone()[0]==0


def test_search_judgments_and_report_review_are_explicit(frozen,tmp_path):
    from tools.knowledge_graph.playground.retrieval_diagnostic import evaluate_search, rank, retrieval_metrics, prepare_judgments
    store,_,_,plan=frozen;exp=build(frozen)['experiment']
    form=tmp_path/'form.json'
    assert prepare_judgments(store.path,exp,form)['pending_judgments']==96
    with pytest.raises(ValueError,match='overwrite'):prepare_judgments(store.path,exp,form)
    records=[{'page':c['page'],'snapshot_hash':c['snapshot_hash'],'status':'pending','grade':None,
              'reviewer':'','source_checked':False,'independent_of_generator':False} for c in plan['cases']]
    fixture={'pages':[c['page'] for c in plan['cases']],
             'queries':[{'id':'q1','text':'divindade Filho','judgments':records}]}
    path=tmp_path/'judgments.json';path.write_text(dumps(fixture))
    data=evaluate_search(store.path,exp,path)
    assert all(r['metrics'] is None for r in data['results'])
    execute_ab(store,exp,caller=fake,concurrency=4)
    for i,j in enumerate(records):
        j.update(status='adjudicated',grade=int(i<2),reviewer='synthetic-test',source_checked=True,
                 independent_of_generator=True,rationale='Synthetic test judgment, not corpus evidence')
    path.write_text(dumps(fixture));data=evaluate_search(store.path,exp,path)
    assert all(r['metrics'] is not None for r in data['results'])
    assert len({dumps(r['ranking']) for r in data['results']})==1 # empty concepts: all arms identical
    ranking=rank({'p1':'autenticidade escritos','p2':'eclipse solar'},'autenticidade')
    assert ranking[0]['page']=='p1'
    assert retrieval_metrics(ranking,{'p1':2,'p2':0},1)=={'precision_at_k':1.,'recall_at_k':1.,'reciprocal_rank':1.,'ndcg_at_k':1.}
    records[0]['snapshot_hash']='wrong';path.write_text(dumps(fixture))
    with pytest.raises(ValueError,match='hash'):evaluate_search(store.path,exp,path)
    write_report(store.path,exp,tmp_path/'report.md')
    template=json.loads((tmp_path/'report.review_template.json').read_text())
    first=template['reviews'][0];first.update(status='adjudicated',reviewer='synthetic-test',source_checked=True,independent_of_generator=True)
    first['dimensions']['subject_label_usefulness']=2
    decisions=tmp_path/'decisions.json';decisions.write_text(dumps(template))
    assert write_report(store.path,exp,tmp_path/'report.md',reviews=decisions)['adjudicated']==1
    first['raw_response_hash']='bad';decisions.write_text(dumps(template))
    with pytest.raises(ValueError,match='hash'):write_report(store.path,exp,tmp_path/'report.md',reviews=decisions)


def test_transport_replay_and_key_are_checked_before_execution(frozen,monkeypatch):
    import tools.knowledge_graph.playground.ab_runner as module
    store,_,_,_=frozen
    exp=build(frozen)['experiment']
    monkeypatch.delenv('META_API_KEY',raising=False)
    with pytest.raises(ValueError,match='META_API_KEY'):execute_ab(store,exp)
    assert all(r['status']=='planned' for r in store.list_runs(exp))
    run=store.get_run(store.list_runs(exp)[0]['id'])
    monkeypatch.setattr(module,'encode_facsimile_for_transport',lambda _:('Y2hhbmdlZA==','image/png'))
    with pytest.raises(ValueError,match='transport'):verify_source(store.get_case(run['case_id']),run['prepared'])


def test_no_secrets_read_during_prepare_and_wal(frozen,monkeypatch):
    import os
    store,_,_,_=frozen
    monkeypatch.setattr(os,'getenv',lambda *_:pytest.fail('Preparation cannot read API keys'))
    build(frozen)
    with readonly(store.path) as c:assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'


def test_changed_validation_policy_cannot_mix_runs(frozen,monkeypatch):
    import tools.knowledge_graph.playground.ab_runner as module
    store,_,_,_=frozen;exp=build(frozen)['experiment']
    monkeypatch.setattr(module,'policy_manifest',lambda:{'changed':True})
    with pytest.raises(ValueError,match='validation implementation'):execute_ab(store,exp,caller=fake)
    assert all(r['status']=='planned' for r in store.list_runs(exp))


def test_cli_explicit_policy_and_ab_limits():
    from scripts.playgrounds.kg_extractor_playground import parser
    args=parser().parse_args(['validate','--run','3','--matching-policy','local-quote-v3'])
    assert args.matching_policy=='local-quote-v3'
    args=parser().parse_args(['ab-run','--experiment','EXP-test','--max-calls','8'])
    assert args.max_calls==8 and args.concurrency==2 and not args.retry_interrupted


def test_stored_execution_config_cannot_drift_from_frozen_request(frozen):
    store,_,_,_=frozen;exp=build(frozen)['experiment']
    run=store.get_run(store.list_runs(exp)[0]['id']);config=run['config'];config['model']='changed-model'
    with store.connect() as con:con.execute('UPDATE runs SET config_json=? WHERE id=?',(dumps(config),run['id']))
    with pytest.raises(ValueError,match='configuration differs'):execute_ab(store,exp,caller=fake)
    assert all(r['status']=='planned' for r in store.list_runs(exp))


def test_reporting_distinguishes_schema_from_reference_integrity():
    from tools.knowledge_graph.playground.ab_reporting import metrics
    row={'id':1,'page':'DEMO:1','ab':{'arm':'A','schedule_index':0},'repetition':1,
         'status':'invalid','result':None,
         'validation':{'schema_valid':True,'structural_valid':False,'source_valid':True,
                       'structural_errors':['invalid relation target'],'source_errors':[]}}
    result=metrics(row)
    assert result['schema_valid'] and not result['structural_valid']
    assert result['validation_errors']==['invalid relation target']


@pytest.mark.parametrize('concurrency,max_calls',[(20,20),(20,3),(1000,3)])
def test_concurrency_above_16_and_bounded_by_pending_calls(frozen,monkeypatch,concurrency,max_calls):
    import tools.knowledge_graph.playground.ab_runner as module
    store,_,_,_=frozen;exp=build(frozen)['experiment']
    original_pool=module.ThreadPoolExecutor;pool_sizes=[]
    def pool(**kwargs):
        pool_sizes.append(kwargs['max_workers'])
        return original_pool(**kwargs)
    monkeypatch.setattr(module,'ThreadPoolExecutor',pool)
    barrier=threading.Barrier(max_calls,timeout=15)
    def caller(config,request):
        barrier.wait()
        return fake(config,request)
    result=execute_ab(store,exp,concurrency=concurrency,max_calls=max_calls,caller=caller)
    assert pool_sizes==[max_calls]
    assert result['submitted']==result['finished']==max_calls
    assert result['states']=={'planned':96-max_calls,'completed':max_calls}


@pytest.mark.parametrize('concurrency',[0,-1,True,1.5])
def test_invalid_concurrency_rejected_before_loading_experiment(concurrency):
    with pytest.raises(ValueError,match='positive integer'):
        execute_ab(None,'unused',concurrency=concurrency,caller=fake)
