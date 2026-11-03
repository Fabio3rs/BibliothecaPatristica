"""Frozen second-pass research protocol. All provider responses are mocked."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest
from PIL import Image

from tools.knowledge_graph.playground.common import ROOT, digest, dumps, readonly
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.experiment_runner import prepare, execute_experiment, execute_run
from tools.knowledge_graph.playground.validation_dispatch import evaluate_response
from tools.knowledge_graph.playground.verification_contract import (
    apply_decisions, candidate_payload, evaluate_review, review_schema,
)
from tools.knowledge_graph.playground.verification_study import prepare_verification, PAGES
from tools.knowledge_graph.playground.verification_runner import execute_verification
from tools.knowledge_graph.playground.verification_reporting import write_verification_report
from tools.knowledge_graph.playground.retrieval_diagnostic import prepare_judgments, evaluate_search


def extraction():
    return json.loads((ROOT/'tools/knowledge_graph/playground/fixtures/page-output.confidence.demo.json').read_text())


def decisions(data):
    return {'schema_version': 'kg-stance-review-v1', 'new_evidence': [], 'warnings': [],
            **{key: [{id_key: obj['id'], 'action': 'keep', 'replacements': [],
                      'reason': 'Conserva o conteúdo sustentado pelo trecho sintético.',
                      'evidence_ids': obj['evidence_ids']} for obj in data[group]]
               for group, key, id_key in (('claims','claim_decisions','claim_id'), ('relations','relation_decisions','relation_id'))}}


def fake(config, request):
    assert 'reasoning_effort' not in request['body']
    # Real Meta rejects this keyword before decoding, even with a valid JSON Schema.
    assert 'uniqueItems' not in dumps(request['body']['response_format']['json_schema']['schema'])
    payload = json.loads(request['body']['messages'][1]['content'][0]['text'])
    return {'raw_response': dumps(decisions(payload['candidate_extraction'])),
            'usage': {'input_tokens': 120, 'output_tokens': 80, 'reasoning_tokens': 40},
            'latency_seconds': .01, 'finish_reason': 'stop', 'attempts': 1, 'provider_response': '{}'}


def frozen_state(store, exp):
    with readonly(store.path) as c:
        return digest({'experiment': dict(c.execute('SELECT * FROM experiments WHERE id=?',(exp,)).fetchone()),
                       'runs': [dict(r) for r in c.execute('SELECT * FROM runs WHERE experiment_id=? ORDER BY id',(exp,))],
                       'validations': [dict(r) for r in c.execute('SELECT v.* FROM run_validation v JOIN runs r ON r.id=v.run_id WHERE r.experiment_id=? ORDER BY v.id',(exp,))],
                       'cases': [dict(r) for r in c.execute('SELECT DISTINCT c.* FROM cases c JOIN runs r ON r.case_id=c.id WHERE r.experiment_id=? ORDER BY c.id',(exp,))]})


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path/'playground.db')
    exp = store.experiment('Synthetic D source', {})
    image = tmp_path/'fixture.png'
    Image.new('RGB',(24,32),'white').save(image)
    cases, schedule = [], []
    for page in PAGES:
        snap = demo_snapshot()
        snap['source'].update(documento=page.split(':')[0], physical_page=int(page.split(':')[1]))
        snap['ocr_raw'] = '\n'.join(b['text'] for b in snap['ocr']['blocks'])
        snap['source']['ocr_sha256'] = digest(snap['ocr_raw'])
        snap['facsimile'] = {'available':True,'path':str(image),'sha256':digest(image.read_bytes())}
        case = store.case(snap)
        p = prepare(case, {'provider':'meta','model':'muse-spark-1.3-contributor'},
                    variant='concepts-v2.5-semantics-confidence',facsimile='on')
        for rep in (1,2):
            p['ab'] = {'arm':'D','schedule_index':len(schedule)}
            rid = store.add_run(exp,case,p,rep)
            result = {'raw_response':dumps(extraction()),'parsed_json':extraction(),
                      'usage':{},'cost_usd':.005,'finish_reason':'stop'}
            checked = evaluate_response(case['snapshot'],result,p)
            assert checked['structural_valid']
            store.start(rid)
            store.finish(rid,'completed',result,checked)
            schedule.append({'page':page,'arm':'D','repetition':rep})
        cases.append({'page':page,'run_id':rid,'snapshot_hash':case['snapshot_hash']})
    plan = {'kind':'ab','study':'semantics-v25','arms':{'D':{'variant':'concepts-v2.5-semantics-confidence','system_hash':p['system_hash']}},
            'schema_hash':p['schema_hash'],'selection':{'reserved_holdout_volumes':[]},'cases':cases,'schedule':schedule}
    with store.connect() as c:
        c.execute('UPDATE experiments SET plan_json=? WHERE id=?',(dumps(plan),exp))
    return store,exp,tmp_path


def build(source):
    store,exp,path = source
    return prepare_verification(store,source_experiment=exp,output=path/'preflight.json',benchmark_dir=path/'absent')


def test_preflight_frozen_source_confidence_mask_schema_and_wal(source,monkeypatch):
    store,exp,path = source
    before = frozen_state(store,exp)
    monkeypatch.setattr('tools.knowledge_graph.playground.providers.send',lambda *_:pytest.fail('No inference during preparation'))
    result = build(source)
    assert result['size']==24 and result['inference_calls']==0
    assert result['estimated_calls_usd']==pytest.approx(.12)
    assert frozen_state(store,exp)==before
    with readonly(store.path) as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    for row in store.list_runs(result['experiment']):
        p = store.get_run(row['id'])['prepared']
        src = store.get_run(p['source_run_id'])['prepared']
        assert p['config']==src['config'] and p['ocr_preparation']==src['ocr_preparation']
        assert p['request']['body']['messages'][1]['content'][1]==src['request']['body']['messages'][1]['content'][1]
        assert p['user_payload']['source']==src['user_payload']['source']
        assert p['user_payload']['v2']==src['user_payload']['v2']
        candidate = p['user_payload']['candidate_extraction']
        assert 'model_confidence' not in dumps(candidate) and 'warnings' not in candidate
        assert p['user_payload']['response_schema']==p['schema']
        assert p['schema']['properties']['schema_version']['const']=='kg-stance-review-v1'
        wire = deepcopy(p['request']['body']['response_format']['json_schema']['schema'])
        for group in ('claim_decisions', 'relation_decisions'):
            wire['properties'][group]['items']['properties']['evidence_ids']['uniqueItems'] = True
        assert wire == p['schema']
        assert len(p['request']['schema_projection']['removed_constraints']) == 2
    with pytest.raises(ValueError,match='already exists'):build(source)
    with pytest.raises(ValueError,match='verify-run'):execute_experiment(store,result['experiment'])
    with pytest.raises(ValueError,match='verify-run'):execute_run(store,store.list_runs(result['experiment'])[0]['id'])


def test_mock_execution_resume_cache_bypass_blinding_and_history(source,monkeypatch):
    store,source_exp,path = source
    exp = build(source)['experiment']
    before = frozen_state(store,source_exp)
    calls=[]
    monkeypatch.setattr(store,'cached',lambda *_:pytest.fail('No inference cache'))
    def call(c,r):calls.append(digest(r));return fake(c,r)
    stop=threading.Event();stop.set()
    assert execute_verification(store,exp,caller=call,stop_event=stop)['submitted']==0
    assert execute_verification(store,exp,caller=call,max_calls=3,concurrency=20)['submitted']==3
    assert execute_verification(store,exp,caller=call,concurrency=20)['submitted']==21
    assert execute_verification(store,exp,caller=call)['submitted']==0
    assert len(calls)==24
    assert frozen_state(store,source_exp)==before
    for row in store.list_runs(exp):
        run=store.get_run(row['id'])
        assert run['result']['derived_extraction']==run['prepared']['baseline_extraction']
        assert run['validation']['semantic_status']=='pending_independent_review'
    out=path/'report.md'
    report=write_verification_report(store.path,exp,out)
    assert report['blind_pairs_available']==24 and report['adjudicated_objects']==0
    packets=[json.loads(l) for l in (path/'blind/packets.jsonl').open()]
    for p in packets:
        assert set(p['outputs'])=={'X','Y'}
        assert not set(p)&{'source_run_id','review_run_id','arm','model'}
        assert 'model_confidence' not in dumps(p['outputs'])
    assert frozen_state(store,source_exp)==before


@pytest.mark.parametrize('mutation',['confidence','request','image','source','config'])
def test_run_fails_closed_on_changed_frozen_inputs(source,mutation):
    store,source_exp,path=source;exp=build(source)['experiment']
    run=store.get_run(store.list_runs(exp)[0]['id']);p=run['prepared']
    if mutation=='image':
        Path(store.get_case(run['case_id'])['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
    elif mutation=='source':
        with store.connect() as c:c.execute('UPDATE runs SET result_json=? WHERE id=?',(dumps({'raw_response':'changed'}),p['source_run_id']))
    else:
        if mutation=='confidence':p['user_payload']['candidate_extraction']['claims'][0]['model_confidence']={'score':1}
        if mutation=='request':p['request']['body']['max_completion_tokens']=123
        if mutation=='config':p['config']['reasoning_effort']='low'
        with store.connect() as c:c.execute('UPDATE runs SET prepared_json=? WHERE id=?',(dumps(p),run['id']))
    with pytest.raises(ValueError):execute_verification(store,exp,caller=lambda *_:pytest.fail('Must not call provider'))
    assert all(r['status']=='planned' for r in store.list_runs(exp))


def test_interrupted_unknown_outcome_requires_explicit_new_attempt(source):
    store,_,_=source;exp=build(source)['experiment']
    rid=store.list_runs(exp)[0]['id'];store.start(rid)
    assert execute_verification(store,exp,caller=fake)['submitted']==23
    assert store.get_run(rid)['status']=='interrupted'
    assert execute_verification(store,exp,caller=fake)['submitted']==0
    assert execute_verification(store,exp,caller=fake,retry_interrupted=True)['submitted']==1
    assert store.get_run(rid)['status']=='interrupted'
    assert execute_verification(store,exp,caller=fake,retry_interrupted=True)['submitted']==0


def test_invalid_raw_output_preserved_without_retry(source):
    store,_,_=source;exp=build(source)['experiment']
    def bad(c,r):return dict(fake(c,r),raw_response='{"claim_decisions":',finish_reason='length')
    execute_verification(store,exp,caller=bad,max_calls=1)
    invalid=next(store.get_run(r['id']) for r in store.list_runs(exp) if r['status']=='invalid')
    assert invalid['result']['raw_response']=='{"claim_decisions":'
    assert invalid['validation']['completion']['output_limit_reached']
    assert invalid['result']['derived_extraction'] is None
    write_verification_report(store.path,exp,store.path.parent/'invalid.md')


def test_terminal_provider_error_is_reported_on_resume_without_resubmission(source, monkeypatch):
    from tools.knowledge_graph.playground.providers import ProviderError
    store, _, _ = source
    exp = build(source)['experiment']
    monkeypatch.setenv('META_API_KEY', 'PRIVATE-TEST-KEY')
    body = dumps({'error': {'message': 'JSON schema keyword `uniqueItems` is not supported PRIVATE-TEST-KEY'}})
    def rejected(*_):
        raise ProviderError('Provider HTTP 400', response=body, attempts=1)
    progress = []
    result = execute_verification(store, exp, caller=rejected, concurrency=20, progress=progress.append)
    assert result['states'] == {'error': 24}
    assert all('uniqueItems' in row['error'] for row in progress)
    before = frozen_state(store, exp)
    resumed = execute_verification(store, exp, caller=lambda *_: pytest.fail('No new provider calls'))
    assert resumed['submitted'] == 0
    assert resumed['errors'][0]['count'] == 24
    assert 'uniqueItems' in resumed['errors'][0]['message']
    assert 'PRIVATE-TEST-KEY' not in dumps(progress) + dumps(resumed)
    assert frozen_state(store, exp) == before


def test_v4_report_is_offline_and_cli_revalidation_only_appends(source, capsys):
    from scripts.playgrounds.kg_extractor_playground import main
    from tools.knowledge_graph.playground.verification_study import verification_policy
    store, source_exp, path = source
    exp = build(source)['experiment']
    execute_verification(store, exp, caller=fake, max_calls=1)
    run = next(store.get_run(r['id']) for r in store.list_runs(exp) if r['status'] == 'completed')
    previous = frozen_state(store, exp)
    frozen_policy = verification_policy()
    report = write_verification_report(store.path, exp, path/'v4/report.md', matching_policy='local-quote-v4')
    assert report['matching_policy'] == 'local-quote-v4'
    assert report['current_validation_states'] == {'valid': 1}
    assert frozen_state(store, exp) == previous
    assert store.experiment_plan(exp)['policy'] == frozen_policy
    source_before = frozen_state(store, source_exp)
    assert main(['validate', '--db', str(store.path), '--run', str(run['id']),
                 '--matching-policy', 'local-quote-v4']) == 0
    newer = store.get_run(run['id'])
    for key in ('result', 'prepared', 'status', 'input_hash'):
        assert newer[key] == run[key]
    assert len(newer['validation_history']) == len(run['validation_history'])+1
    assert newer['validation']['matching_policy'] == 'local-quote-v4'
    assert newer['validation']['semantic_status'] == 'pending_independent_review'
    assert frozen_state(store, source_exp) == source_before


def test_split_claim_retarget_and_preserve_frozen_groups():
    base=extraction();review=decisions(base)
    replacement=deepcopy(base['claims'][0]);replacement['id']='pV1'
    other=deepcopy(replacement);other['id']='pV2'
    review['claim_decisions'][0].update(action='split',replacements=[replacement,other])
    with pytest.raises(ValueError,match='dangling'):apply_decisions(base,review)
    for i,(old,d) in enumerate(zip(base['relations'],review['relation_decisions'])):
        if old['target_id']==base['claims'][0]['id']:
            new=deepcopy(old);new.update(id='rV'+str(i),target_id='pV1')
            d.update(action='replace',replacements=[new])
    result=apply_decisions(base,review)
    for key in ('concepts','entities','events','evidence','warnings'):assert result[key]==base[key]
    assert base==extraction()


@pytest.mark.parametrize('mutation',['missing','duplicate','duplicate_evidence','id_collision','invented_evidence','new_frozen_node'])
def test_contract_rejects_untraceable_changes(source,mutation):
    store,_,_=source;exp=build(source)['experiment'];run=store.get_run(store.list_runs(exp)[0]['id'])
    p=run['prepared'];review=decisions(p['baseline_extraction'])
    if mutation=='missing':review['relation_decisions'].pop()
    if mutation=='duplicate':review['claim_decisions'].append(review['claim_decisions'][0])
    if mutation=='duplicate_evidence':review['claim_decisions'][0]['evidence_ids'] *= 2
    if mutation=='id_collision':review['claim_decisions'][0].update(action='replace',replacements=[extraction()['claims'][0]])
    if mutation=='invented_evidence':review['claim_decisions'][0]['evidence_ids']=['evFake']
    if mutation=='new_frozen_node':review['entities']=[]
    val=evaluate_review(store.get_case(run['case_id'])['snapshot'],{'raw_response':dumps(review)},p)
    assert not val['structural_valid'] and val['derived_extraction'] is None


def test_review_source_quotes_checked_including_transliteration(source):
    store,_,_=source;exp=build(source)['experiment'];run=store.get_run(store.list_runs(exp)[0]['id'])
    p=run['prepared'];review=decisions(p['baseline_extraction'])
    snap=store.get_case(run['case_id'])['snapshot']
    snap['ocr']['blocks'].append({'id':'greek','text':'Καὶ γὰρ ἐξ αὐτοῦ.','type':'text'})
    review['new_evidence']=[{'id':'evV1','source':'ocr_block','locator':'greek','quote':'Kai gar ex autou.'}]
    review['claim_decisions'][0]['evidence_ids']=['evV1']
    val=evaluate_review(snap,{'raw_response':dumps(review)},p)
    assert val['structural_valid'] and not val['source_valid']
    review['new_evidence'][0]['quote']='Καὶ γὰρ ἐξ αὐτοῦ.'
    val=evaluate_review(snap,{'raw_response':dumps(review)},p)
    assert val['structural_valid']
    match=next(m for m in val['derived_validation']['evidence_matches'] if m['evidence_id']=='evV1')
    assert match['accepted']


def test_blind_adjudication_counts_correct_edges_lost(source):
    store,_,path=source;exp=build(source)['experiment']
    def remove(c,r):
        result=fake(c,r);review=json.loads(result['raw_response'])
        review['relation_decisions'][0]['action']='remove'
        result['raw_response']=dumps(review);return result
    execute_verification(store,exp,caller=remove,max_calls=1)
    report=path/'report.md';write_verification_report(store.path,exp,report)
    template=json.loads((path/'blind/judgments.template.json').read_text())
    for pair in template['pairs']:
        pair.update(reviewer='Independent test reviewer',source_checked=True,independent_of_generator=True)
        for output in pair['outputs'].values():
            for group in ('claims','relations'):
                for obj in output[group]:obj.update(status='adjudicated',decision='correct',comment='Synthetic review',source_refs=[{'source':'ocr_block','locator':'b1'}])
    decisions_file=path/'decisions.json';decisions_file.write_text(dumps(template))
    write_verification_report(store.path,exp,report,reviews=decisions_file)
    data=json.loads(report.with_suffix('.json').read_text())
    assert data['semantic_review']['counts']['relations:correct_content_removed']==1
    template['pairs'][0]['outputs']['X']['extraction_hash']='changed';decisions_file.write_text(dumps(template))
    with pytest.raises(ValueError,match='changed extraction'):write_verification_report(store.path,exp,report,reviews=decisions_file)


def test_search_fallback_explicit_and_metrics_withheld(source):
    store,exp,path=source
    row=store.list_runs(exp)[0]
    with store.connect() as c:
        c.execute('UPDATE run_validation SET validation_json=? WHERE run_id=?',(dumps({'structural_valid':False}),row['id']))
    fixture=path/'queries.json'
    result=prepare_judgments(store.path,exp,fixture,arms=['D'],missing_output_policy='summary_only',
                             query_file=ROOT/'tools/knowledge_graph/playground/fixtures/navigation_queries_v25.json')
    assert result['queries']==12 and result['pending_judgments']==144
    data=evaluate_search(store.path,exp,fixture)
    assert all(r['metrics'] is None for r in data['results'])
    assert any(r['summary_only_fallback_pages']==[row['page']] for r in data['results'])
    assert all(len(r['document_hashes'])==12 for r in data['results'])
    assert {r['arm'] for r in data['results']}=={'summary','D'}
    changed=json.loads(fixture.read_text());changed['queries'][0]['text']='changed after seeing results'
    fixture.write_text(dumps(changed))
    with pytest.raises(ValueError,match='wording'):evaluate_search(store.path,exp,fixture)


@pytest.mark.parametrize('mutation',['holdout','missing_page','truncated','image'])
def test_preflight_failure_is_atomic_and_source_unchanged(source,mutation):
    store,exp,path=source
    row=store.list_runs(exp)[0]
    if mutation=='image':
        Path(store.get_case(row['case_id'])['snapshot']['facsimile']['archived_path']).unlink()
    else:
        with store.connect() as c:
            if mutation=='holdout':c.execute("UPDATE cases SET split='blind_holdout' WHERE id=?",(row['case_id'],))
            if mutation=='missing_page':c.execute('UPDATE cases SET page=? WHERE id=?',('PG999:1',row['case_id']))
            if mutation=='truncated':c.execute('UPDATE runs SET result_json=? WHERE id=?',(dumps({'raw_response':'{','finish_reason':'length'}),row['id']))
    before=frozen_state(store,exp);n=len(store.list_experiments())
    with pytest.raises(ValueError):build(source)
    assert len(store.list_experiments())==n and frozen_state(store,exp)==before
    assert not (path/'preflight.json').exists()


def test_duplicate_json_keys_and_nonjson_numbers_rejected(source):
    store,_,_=source;exp=build(source)['experiment'];run=store.get_run(store.list_runs(exp)[0]['id'])
    snap=store.get_case(run['case_id'])['snapshot']
    for raw in ('{"schema_version":"x","schema_version":"y"}', '{"number":NaN}'):
        result=evaluate_review(snap,{'raw_response':raw},run['prepared'])
        assert not result['structural_valid']
