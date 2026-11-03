"""Navigation contract integration. Fixtures are synthetic; no paid transport."""
from copy import deepcopy
import io
import json

import pytest
from jsonschema import Draft202012Validator

from tools.knowledge_graph.playground.common import ROOT, digest, dumps, read_json
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.prompt_registry import render, freeze_template
from tools.knowledge_graph.playground.validation_current import evaluate_response
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment, prepare
from tools.knowledge_graph.playground.comparison import compare_pair
from tools.knowledge_graph.playground.reporting import report, markdown, csv_report, export_jsonl
from tools.knowledge_graph.playground import providers
from tools.knowledge_graph.playground.ordered_requests import request_spec, send, wire_text

VARIANT = 'navigation-v1-topics-arguments'
FIXTURE = ROOT / 'tools/knowledge_graph/playground/fixtures/page-output.navigation.demo.json'
FIELDS = {'schema_version','concepts','arguments','evidence','warnings'}


def example():
    return demo_snapshot(), read_json(FIXTURE)


def check(source, data, **response):
    return evaluate_response(source, dict(raw_response=dumps(data), **response), render(source, VARIANT, 'concepts_relations'))


def test_schema_and_empty_arrays_without_historical_fields():
    source, data = example()
    schema = freeze_template(VARIANT, 'concepts_relations')['schema']
    Draft202012Validator.check_schema(schema)
    assert set(schema['properties']) == FIELDS
    assert check(source, data)['valid']
    for k in FIELDS - {'schema_version'}:
        data[k] = []
    assert check(source, data)['valid']
    for forbidden in ('entities','claims','relations','model_confidence','participants','action_frames','events','attributions','discourse_units'):
        assert not check(source, dict(data, **{forbidden: []}))['schema_valid']


@pytest.mark.parametrize('defect', ['duplicate', 'evidence_ref', 'concept_ref', 'wrong_concept_type', 'no_evidence', 'salience', 'blank_id', 'missing_text', 'bbox', 'bad_source'])
def test_structural_constraints(defect):
    source, data = example()
    if defect == 'duplicate': data['arguments'][0]['id']='ev1'
    elif defect == 'evidence_ref': data['arguments'][0]['evidence_ids']=['missing']
    elif defect == 'concept_ref': data['arguments'][0]['concept_ids']=['missing']
    elif defect == 'wrong_concept_type': data['arguments'][0]['concept_ids']=['ev1']
    elif defect == 'no_evidence': data['arguments'][0]['evidence_ids']=[]
    elif defect == 'salience': data['concepts'][0]['salience']='incidental'
    elif defect == 'blank_id': data['arguments'][0]['id']=' '
    elif defect == 'missing_text': del data['arguments'][0]['text']
    elif defect == 'bbox': data['evidence'][0]['bbox']=[0,0,1,1]
    elif defect == 'bad_source': data['evidence'][0]['source']='cumulative_summary'
    out=check(source,data)
    assert not out['valid'] and not out['structural_valid']
    assert out['validated_extraction'] is None


@pytest.mark.parametrize('source_type',['ocr_block','summary_segment'])
def test_localized_evidence_original_offsets_and_unknown_locator(source_type):
    source, data=example()
    text='Si interrogant, non sequitur: ἄγγελος οὐκ ἀΐδιος.'
    if source_type=='ocr_block': source['ocr']['blocks'][0]['text']=text
    else: source['v2']['segments'][0]['summary']=text
    data['evidence'][0].update(source=source_type,locator='b1' if source_type=='ocr_block' else 's1',quote=text)
    out=check(source,data)
    assert out['valid'] and out['evidence_counts']['exact']==1
    assert out['parsed']==data
    match=out['evidence_matches'][0]
    assert text[match['start']:match['end']]==text
    data['evidence'][0]['locator']='invented'
    assert not check(source,data)['source_valid']
    data['evidence'][0]['quote']='Invented citation absent from this source and its current summary.'
    assert not check(source,data)['source_valid']


def test_minor_differences_can_be_located_and_not_rewritten():
    source,data=example();data['evidence'][0]['quote']=data['evidence'][0]['quote'].replace('sententiam eorum','sententiam\n eorum')
    out=check(source,data)
    assert out['valid'] and not out['source_exact']
    assert out['parsed']==data and out['semantic_status']=='pending_review'
    assert 'graph_projection' not in out


@pytest.mark.parametrize('text',[
    'A tese de que o Filho é criatura é contestada; sua negação não é uma afirmação dessa tese.',
    'Se os adversários repetirem a objeção, a passagem citada poderá responder-lhes.',
    'A objeção supõe uma natureza comum; a resposta distingue diferença de natureza e diferença de grau.'
])
def test_qualifications_survive_storage_validation_without_semantic_rewrite(tmp_path,text):
    source,data=example()
    source['v2']['segments'][0]['summary']=text
    data['evidence'][0].update(source='summary_segment',locator='s1',quote=text)
    data['arguments'][0]['text']=text
    out=check(source,data)
    assert out['valid'] and out['parsed']['arguments'][0]['text']==text
    assert out['semantic_status']=='pending_review'  # No automated historical accuracy claim.


@pytest.mark.parametrize('key',['editorial_subject_hints','editorialSubjectHints','external_index','thematic_index'])
def test_no_hint_input_and_historical_render_unchanged(key):
    source,_=example();before=deepcopy(source)
    old=render(source,'concepts-v2.7-compact-confidence','concepts_relations')
    new=render(source,VARIANT,'concepts_relations')
    assert source==before
    assert new['system_prompt'].count('EXPERIMENT MODE\n')==1
    assert 'No stance or graph relations' in new['system_prompt']
    assert render(source,'concepts-v2.7-compact-confidence','concepts_relations')==old
    source['v2']['metadata_context'][key]=['unrequested topic']
    with pytest.raises(ValueError,match='excludes editorial'):
        render(source,VARIANT,'concepts_relations')
    assert key in render(source,'concepts-v2.7-compact-confidence','concepts_relations')['user_payload']['v2']['metadata_context']


def test_meta_request_with_image_and_mock_transport(monkeypatch):
    monkeypatch.setenv('KG_TEST_KEY','synthetic-secret')
    source,data=example();prompt=render(source,VARIANT,'concepts_relations',attached=True)
    config=providers.normalize_config({'provider':'meta','model':'muse-spark-1.3-contributor','reasoning_effort':'medium','api_key_env':'KG_TEST_KEY'})
    request=request_spec(config,prompt,{'base64':'ZmFrZQ==','mime':'image/png','transport_sha256':'synthetic'})
    wire=json.loads(wire_text(request));schema=wire['response_format']['json_schema']['schema']
    assert wire['response_format']['json_schema']['name']=='kg_navigation_v1'
    assert wire['reasoning_effort']=='medium' and set(schema['properties'])==FIELDS
    assert 'data:image/png;base64,ZmFrZQ==' in dumps(wire)
    Draft202012Validator(schema).validate(data)
    calls=[]
    def transport(req,timeout):
        calls.append(req.data)
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':dumps(data)},'finish_reason':'stop'}]}).encode())
    result=send(config,request,transport=transport)
    assert len(calls)==1 and calls[0]==wire_text(request).encode()
    assert 'synthetic-secret' not in dumps(request)
    assert evaluate_response(source,result,prompt)['valid']


@pytest.mark.parametrize('mode',['concepts_only','concepts_relations','full_multimodal'])
def test_storage_resume_exports_reviews_and_cross_schema_comparison(tmp_path,mode):
    store=Store(tmp_path/'test.db');case=store.case(demo_snapshot())
    old_exp=plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}],variants=['concepts-v3-statements-confidence'])
    execute_experiment(store,old_exp);old=store.get_run(store.list_runs(old_exp)[0]['id'])
    exp=plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}],variants=[VARIANT],modes=[mode],bypass_cache=True)
    assert len(store.list_runs(exp))==1 and store.list_runs(exp)[0]['status']=='planned'
    execute_experiment(store,exp,caller=lambda *a: pytest.fail('No network'))
    run=store.get_run(store.list_runs(exp)[0]['id'])
    assert run['status']=='completed',run
    assert set(run['validation']['parsed'])==FIELDS
    assert not run['result']['inference_performed']
    assert run['prepared']['validation_policy']=='local-quote-v6'
    execute_experiment(store,exp,resume=True,caller=lambda *a: pytest.fail('No repeated call'))
    assert store.get_run(run['id'])==run
    store.review(run['id'],'a1' if mode!='concepts_only' else 'c1','uncertain')
    assert store.get_run(run['id'])['result']==run['result']
    assert store.get_run(old['id'])==old
    pair=compare_pair(old,run)
    assert 'error' not in pair
    assert not pair['relations_only_right']
    assert all(m['kind']!='arguments' for m in pair['matched'])
    assert compare_pair(run,dict(run,id=999))['matched']
    metrics=report(store,exp)
    assert metrics['summary']['average_arguments']==(0 if mode=='concepts_only' else 1)
    assert 'Arguments' in markdown(metrics) and 'arguments' in csv_report(metrics)
    exported=list(export_jsonl(store,exp))
    assert exported and 'Criaturalidade' in str(exported)
    with store.connect() as con: assert con.execute('pragma journal_mode').fetchone()[0]=='wal'


def test_truncated_and_duplicate_json_responses():
    source,data=example();out=check(source,data,finish_reason='length')
    assert not out['valid'] and out['completion']['output_limit_reached']
    assert any(d['code']=='provider_output_limit_reached' for d in out['diagnostics'])
    prepared=render(source,VARIANT,'concepts_relations')
    for raw in ('{"concepts":[],"concepts":[]}', '{', 'NaN', 'null'):
        assert not evaluate_response(source,{'raw_response':raw},prepared)['valid']


def test_frozen_source_replay_does_not_read_current_ocr(tmp_path):
    source,_=example();source['source']['ocr_path']=str(tmp_path/'absent.txt')
    store=Store(tmp_path/'test.db');case=store.case(source)
    config={'provider':'meta','model':'muse-spark-1.3-contributor','reasoning_effort':'medium'}
    prepared=prepare(case,config,variant=VARIANT)
    assert prepared['case_hash']==case['snapshot_hash']
    assert prepared['user_payload']['v2']==source['v2']
    assert prepared['user_payload']['ocr']['blocks'][0]['text']==source['ocr']['blocks'][0]['text']
    assert prepared['request'] and store.list_runs()==[]


def test_archived_image_is_frozen_and_tampering_fails(tmp_path):
    from PIL import Image
    source,_=example()
    path=tmp_path/'page.png';Image.new('RGB',(32,48),'white').save(path)
    source['facsimile']={'available':True,'path':str(path),'sha256':digest(path.read_bytes())}
    store=Store(tmp_path/'test.db');case=store.case(source)
    config={'provider':'meta','model':'muse-spark-1.3-contributor'}
    first=prepare(case,config,variant=VARIANT,facsimile='on')
    path.unlink()
    second=prepare(store.get_case(case['id']),config,variant=VARIANT,facsimile='on')
    assert first==second and first['facsimile_selected']
    assert first['image_transport_sha256']
    from pathlib import Path
    Path(case['snapshot']['facsimile']['archived_path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='Archived facsimile changed'):
        prepare(case,config,variant=VARIANT,facsimile='on')


def test_page_kind_signals_are_not_external_editorial_subject_hints():
    source,_=example()
    source['v2']['metadata_context']['static_analysis']={'page_kind_hints':['body','apparatus']}
    prompt=render(source,VARIANT,'concepts_relations')
    assert prompt['user_payload']['v2']==source['v2']
    source['v2']['metadata_context']['static_analysis']['editorial_subject_hints']=['secret index topic']
    with pytest.raises(ValueError,match='excludes editorial'):
        render(source,VARIANT,'concepts_relations')


def test_bibliographic_projection_does_not_inject_external_index_excerpts():
    source,_=example()
    source['v2']['metadata_context']['index_works']=[{'work_key':'demo-work',
        'title_raw':'Example work','author_raw':'Example author','confidence':'medium',
        'raw_json':'{"evidence":["EXTERNAL INDEX TOPIC"]}',
        'source_section_key':'other-page-index', 'editorial_subject_hints':['secret topic']}]
    before=deepcopy(source)
    prompt=render(source,VARIANT,'concepts_relations')
    work=prompt['user_payload']['v2']['metadata_context']['index_works'][0]
    assert set(work)=={'work_key','title_raw','author_raw','confidence'}
    assert 'EXTERNAL INDEX TOPIC' not in prompt['user_prompt'] and 'secret topic' not in prompt['user_prompt']
    assert len(prompt['input_preparation']['omitted_metadata_paths'])==3
    assert source==before
