"""Loopback-only HTTP integration. Execute inside Native Lab when sockets are isolated."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from tools.knowledge_graph.playground.service import Playground
from tools.knowledge_graph.playground.web_app import make_server


@pytest.fixture
def server(tmp_path):
    app=Playground(db=tmp_path/'lab.db',summaries_db=tmp_path/'missing.db',indices_db=tmp_path/'missing-indices.db',
                   corpus_root=tmp_path/'corpus',benchmark_dir=tmp_path/'benchmark',ocr_versions_db=tmp_path/'missing-ocr.db')
    http=make_server(app,port=0)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{http.server_port}'
    def request(path,body=None,headers=None):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                                   headers=headers or {},method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers
    yield app,request
    http.shutdown();http.server_close();thread.join(timeout=3)


def auth(request):
    status,body,_=request('/api/bootstrap');assert status==200
    return {'X-KG-CSRF':json.loads(body)['csrf'],'Content-Type':'application/json'}


def test_ui_assets_and_boot_do_not_infer(server):
    app,request=server
    for path in ['/','/playground.js','/playground.css','/favicon.svg']:
        status,_,headers=request(path);assert status==200
        assert "script-src 'self'" in headers['Content-Security-Policy']
    assert app.store.list_runs()==[]
    assert json.loads(request('/api/bootstrap')[1])['summary_db_available'] is False
    assert json.loads(request('/api/bootstrap')[1])['default_max_output_tokens']==32768
    assert json.loads(request('/api/catalog')[1])==[]


def test_csrf_origin_host_and_json_checks(server):
    _,request=server
    assert request('/api/load',{'demo':True})[0]==403
    headers=auth(request)
    assert request('/api/load',{'demo':True},dict(headers,Origin='https://external.invalid'))[0]==403
    assert request('/api/bootstrap',headers={'Host':'attacker.invalid'})[0]==403
    assert request('/api/load',{'demo':True},dict(headers,**{'Content-Type':'text/plain'}))[0]==415
    assert request('/api/load',[],headers)[0]==400
    assert request('/api/image?case=9999')[0]==400
    assert request('/../../etc/passwd')[0]==404


def test_price_lookup_and_preview_freeze_rates_without_inference(server):
    app,request=server
    catalog=json.loads(request('/api/bootstrap')[1])['pricing']
    assert len(catalog['models'])==5 and catalog['source']=='user_provided'
    code,body,_=request('/api/pricing?provider=meta&model=muse-spark-1.3')
    assert code==200 and json.loads(body)['prices']['output_per_million']==4.25
    assert json.loads(request('/api/pricing?provider=ollama&model=gemma4:31b')[1]) is None
    assert json.loads(request('/api/pricing?provider=ollama&model=gemma4:cloud')[1])['prices']['input_per_million']==.14
    headers=auth(request)
    c=json.loads(request('/api/load',{'demo':True},headers)[1])
    code,body,_=request('/api/preview',{'case_ids':[c['id']],
        'configs':[{'provider':'meta','model':'muse-spark-1.3-contributor'}]},headers)
    assert code==200
    row=json.loads(body)['runs'][0]
    run=app.store.get_run(row['id'])
    assert run['status']=='planned' and run['result'] is None
    assert run['config']['prices']=={'input_per_million':.1,'output_per_million':.2}
    assert run['prepared']['pricing_reference']['source']=='user_provided'


def test_missing_databases_return_actionable_error(server):
    _,request=server
    code,body,_=request('/api/load',{'page':'PG026:71'},auth(request))
    assert code==400 and 'unavailable' in json.loads(body)['error'].lower()


def test_dry_run_snapshot_and_explicit_execution_gate(server):
    app,request=server;headers=auth(request)
    c=json.loads(request('/api/load',{'demo':True},headers)[1])
    code,body,_=request('/api/preview',{'case_ids':[c['id']],'configs':[{'provider':'fixture','model':'demo'}]},headers)
    assert code==200
    preview=json.loads(body);exp=preview['experiment'];run=preview['runs'][0]['id']
    assert app.store.get_run(run)['status']=='planned'
    assert request('/api/execute',{'experiment':exp},headers)[0]==400
    # Execute fixture synchronously via application boundary to avoid timing-based polling in tests.
    from tools.knowledge_graph.playground.experiment_runner import execute_experiment
    execute_experiment(app.store,exp)
    code,body,_=request('/api/run?id='+str(run));assert code==200
    assert json.loads(body)['validation']['valid']
    malicious='<img src=x onerror=alert(1)>'
    assert request('/api/review',{'run_id':run,'object_id':'r1','decision':'uncertain','comment':malicious},headers)[0]==200
    exported=json.loads(request('/api/export?experiment='+exp)[1])
    assert exported['run']['reviews'][0]['comment']==malicious
    assert exported['case']['synthetic']
    assert request('/api/report?experiment='+exp+'&format=csv')[0]==200
    assert request('/api/report?experiment='+exp+'&format=markdown')[0]==200


def test_holdout_not_available_via_direct_run_case_report_or_export(server):
    app,request=server
    case=app.load(demo=True);snap=case['snapshot'];snap['source']['split']='blind_holdout'
    held=app.store.case(snap)
    from tools.knowledge_graph.playground.experiment_runner import plan_experiment
    exp=plan_experiment(app.store,[held],[{'provider':'fixture','model':'demo'}],evaluation=True)
    run=app.store.list_runs(exp)[0]['id']
    # A frozen holdout remains protected even if no external manifest is present.
    for path in [f'/api/case?id={held["id"]}',f'/api/run?id={run}',f'/api/report?experiment={exp}',f'/api/export?experiment={exp}']:
        assert request(path)[0]==400
        assert request(path+'&evaluation=true')[0]==200
    assert not any(r['id']==run for r in json.loads(request('/api/runs')[1]))
    assert request('/api/compare',{'runs':[run,run]},auth(request))[0]==400


@pytest.mark.parametrize('approximate', [False, True])
def test_revalidate_saved_fenced_response_without_inference_or_rewriting_run(server, approximate):
    from tools.knowledge_graph.playground.common import KIT, read_json
    from tools.knowledge_graph.playground.experiment_runner import plan_experiment
    app,request=server
    case=app.load(demo=True)
    exp=plan_experiment(app.store,[case],[{'provider':'fixture','model':'demo'}])
    identifier=app.store.list_runs(exp)[0]['id']
    extraction=read_json(KIT/'page-output.demo.json')
    if approximate:
        extraction['evidence'][0]['quote']='Atanasio'
    raw='```json\n'+json.dumps(extraction)+'\n```'
    result={'raw_response':raw,'usage':{},'latency_seconds':0,'parsed_json':None}
    app.store.start(identifier)
    app.store.finish(identifier,'invalid',result,{'version':'kg-validation-1','valid':False,'parsed':None})
    code,body,_=request('/api/validate',{'run_id':identifier},auth(request))
    assert code==200
    checked=json.loads(body)
    assert checked['valid'] and checked['graph_inspectable']
    assert checked['response_format']=='markdown_fence'
    assert checked['source_exact'] is not approximate
    assert checked['evidence_counts']['normalized']==int(approximate)
    metrics=json.loads(request('/api/report?experiment='+exp)[1])
    assert metrics['runs'][0]['approximate_quotes']==int(approximate)
    assert metrics['runs'][0]['latest_validation_valid']
    assert metrics['summary']['located_evidence_valid_fraction_of_structural_valid']==1
    assert metrics['summary']['exact_evidence_valid_fraction_of_structural_valid']==int(not approximate)
    after=json.loads(request('/api/run?id='+str(identifier))[1])
    assert after['status']=='invalid' and after['result']==result
    assert len(after['validation_history'])==2


def test_revalidation_keeps_provider_truncation_diagnostic(server):
    from tools.knowledge_graph.playground.experiment_runner import plan_experiment
    app,request=server;case=app.load(demo=True)
    exp=plan_experiment(app.store,[case],[{'provider':'fixture','model':'demo','max_output_tokens':8192}])
    identifier=app.store.list_runs(exp)[0]['id']
    result={'raw_response':'{"concepts":[{"label":"unfinished','finish_reason':'length',
            'usage':{'output_tokens':8192,'reasoning_tokens':6824}}
    app.store.start(identifier);app.store.finish(identifier,'invalid',result)
    code,body,_=request('/api/validate',{'run_id':identifier},auth(request))
    assert code==200
    checked=json.loads(body)
    assert 'provider_output_limit_reached' in checked['warnings']
    assert checked['completion']['visible_output_tokens']==1368
    assert not checked['valid'] and app.store.get_run(identifier)['result']==result
