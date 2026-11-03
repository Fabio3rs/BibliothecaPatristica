"""Synthetic source-grounded checks. All provider calls use in-memory transports."""
from copy import deepcopy
import io
import json

import pytest
from jsonschema import Draft202012Validator

from tools.knowledge_graph.playground.common import ROOT, digest, dumps, read_json
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.validation_current import evaluate_response
from tools.knowledge_graph.playground.discourse_contract import locate_anchor
from tools.knowledge_graph.playground.evidence_locator_v5 import locate_on_page
from tools.knowledge_graph.playground.ordered_requests import request_spec, send, wire_text
from tools.knowledge_graph.playground import providers
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment
from tools.knowledge_graph.playground.comparison import compare_pair
from tools.knowledge_graph.playground.reporting import report, markdown

A = 'concepts-v3-statements-confidence'
B = 'concepts-v3.1-order-confidence'
C = 'concepts-v3.1-discourse-actions-confidence'
FIXTURE = ROOT / 'tools/knowledge_graph/playground/fixtures/page-output.discourse.demo.json'


def example():
    return demo_snapshot(), read_json(FIXTURE)


def check(source, data, mode='concepts_relations'):
    return evaluate_response(source, {'raw_response': dumps(data)}, render(source, C, mode))


def test_order_control_changes_only_system_prompt():
    source = demo_snapshot()
    a, b, c = [render(source, v, 'concepts_relations') for v in (A, B, C)]
    assert a['user_prompt'] == b['user_prompt']
    assert a['schema_hash'] == b['schema_hash'] != c['schema_hash']
    assert b['system_prompt'].endswith(a['system_prompt'])
    assert b['system_prompt'].count('EXPERIMENT MODE\n') == c['system_prompt'].count('EXPERIMENT MODE\n') == 1
    assert list(json.loads(c['user_prompt'])['response_schema']['properties']).index('discourse_units') < list(json.loads(c['user_prompt'])['response_schema']['properties']).index('claims')


def test_valid_discordant_voices_do_not_generate_stance_from_agent():
    source, data = example()
    data['claims'][0]['attributions'] = []
    before = deepcopy(data)
    result = check(source, data)
    assert result['valid'] and result['semantic_status'] == 'pending_review'
    edges = result['graph_projection']['relations']
    assert any(e['source_id'] == 'v1' and e['target_id'] == 'e1' and e['predicate'] == 'HAS_AGENT' for e in edges)
    assert not any(e['target_id'] == 'p1' and e['predicate'] in {'AFFIRMS', 'REFUTES'} for e in edges)
    assert data == before == result['parsed']
    assert result['anchor_matches'][0]['matched_quote'] == 'refutat'


@pytest.mark.parametrize('defect', ['duplicate_id', 'cycle', 'self_cycle', 'speaker', 'claim_unit', 'argument_type',
                                   'missing_evidence', 'claim_ref', 'reserved', 'condition', 'anchor_parent', 'participant_role'])
def test_invalid_references_and_action_contract(defect):
    source, data = example()
    event = data['events'][0]
    if defect == 'duplicate_id': data['discourse_units'][0]['id'] = 'ev1'
    if defect == 'cycle': data['discourse_units'][0]['parent_unit_id'] = 'u2'
    if defect == 'self_cycle': data['discourse_units'][0]['parent_unit_id'] = 'u1'
    if defect == 'speaker': data['discourse_units'][0]['speaker_id'] = 'p1'
    if defect == 'claim_unit': data['claims'][0]['discourse_unit_id'] = 'e2'
    if defect == 'argument_type': event['arguments'][2]['node_id'] = 'e2'
    if defect == 'missing_evidence': data['discourse_units'][0]['evidence_ids'] = ['absent']
    if defect == 'claim_ref': event['claim_ids'] = ['e1']
    if defect == 'reserved':
        data['relations'] = [{'id': 'r1', 'source_id': 'e1', 'target_id': 'p1', 'predicate': 'AFFIRMS',
                              'grounding': 'explicit', 'evidence_ids': ['ev8'], 'model_confidence': event['model_confidence']}]
    if defect == 'condition': event['modality'] = 'conditional'
    if defect == 'anchor_parent': event['evidence_ids'] = ['ev6']
    if defect == 'participant_role':
        data['claims'][0]['participants'] = [{'entity_id': 'e2', 'role': 'target', 'evidence_ids': ['ev8']}]
    out = check(source, data)
    assert not out['valid'] and not out['structural_valid']
    assert not out.get('graph_projection') and out['validated_extraction'] is None


def test_missing_anchor_is_source_error_not_schema_error():
    source, data = example()
    data['events'][0]['trigger']['text'] = 'invented action phrase'
    out = check(source, data)
    assert out['structural_valid'] and not out['source_valid'] and not out['valid']
    assert out['graph_inspectable']


def test_unresolved_voice_and_arguments_are_allowed_without_fabricated_edges():
    source, data = example()
    data['discourse_units'][0]['speaker_id'] = None
    data['events'][0]['arguments'][1].update(node_id=None, mention=None, antecedent=None)
    out = check(source, data)
    assert out['valid']
    assert any(d['code'] == 'unresolved_action_argument' for d in out['diagnostics'])
    assert not any(e['predicate'] == 'HAS_OPPONENT' for e in out['graph_projection']['relations'])


def test_conditional_action_and_direct_assertion_are_independent():
    source, data = example()
    text = 'Helena ait: Si adversarii iterum obiciant, verba Lucii eis respondebunt.'
    source['ocr']['blocks'][0]['text'] = text
    source['v2']['segments'] = []
    data['concepts'] = []
    data['entities'] = [dict(data['entities'][0], id=i, label=label, evidence_ids=['ev1'])
                        for i, label in [('e1','Helena'), ('e2','Lúcio'), ('e3','Adversários')]]
    data['evidence'] = [{'id':'ev1','source':'ocr_block','locator':'b1','quote':text}]
    data['discourse_units'] = [{'id':'u1','kind':'narration','speaker_id':'e1','parent_unit_id':None,'evidence_ids':['ev1']}]
    data['claims'] = [dict(data['claims'][0], discourse_unit_id='u1', mode='conditional',
        proposition='Se os adversários repetirem a objeção, as palavras de Lúcio lhes responderão.',
        evidence_ids=['ev1'], attributions=[{'entity_id':'e1','stance':'AFFIRMS','scope':'direct','evidence_ids':['ev1']}])]
    anchor = lambda t: {'evidence_id':'ev1','text':t}
    event = data['events'][0]
    event.update(action='ANSWER', modality='conditional', claim_ids=['p1'], evidence_ids=['ev1'],
                 condition_anchor=anchor('Si adversarii iterum obiciant'), trigger=anchor('respondebunt'),
                 arguments=[{'role':'agent','node_id':'e2','mention':anchor('Lucii'),'antecedent':None,'evidence_ids':['ev1']},
                            {'role':'recipient','node_id':'e3','mention':anchor('eis'),'antecedent':anchor('adversarii'),'evidence_ids':['ev1']}])
    out = check(source, data)
    assert out['valid']
    edges = out['graph_projection']['relations']
    assert [(e['source_id'],e['scope'],e['claim_mode']) for e in edges if e['predicate']=='AFFIRMS'] == [('e1','direct','conditional')]
    assert all(e['event_modality'] == 'conditional' for e in edges if e['predicate'].startswith('HAS_'))
    assert not any(e['source_id']=='e2' and e['predicate']=='AFFIRMS' for e in edges)
    other = deepcopy(data); other['events'][0]['modality'] = 'hypothetical'
    comparison = compare_pair({'id':1,'validation':out}, {'id':2,'validation':check(source,other)})
    assert comparison['relations_only_left'] and comparison['relations_only_right']
    # Existing but semantically wrong referent remains reviewable, not automatically caught as historical truth.
    data['events'][0]['arguments'][1]['node_id'] = 'e1'
    assert check(source,data)['semantic_status'] == 'pending_review'


@pytest.mark.parametrize('text,quote,anchor', [
    ('πρόλογος. α\u0313ληθὴς λόγος. τέλος.', 'ἀληθὴς λόγος', 'ἀληθὴς'),
    ('præstantior est natura.', 'praestantior est natura.', 'praestantior'),
    ('longa nar-\nratio continetur.', 'longa narratio continetur.', 'narratio'),
    ('Auctor refutat errorem.', 'Auctor refutat errorem.', 'refutat'),
])
def test_anchor_unicode_original_offsets(text, quote, anchor):
    source = demo_snapshot(); source['ocr']['blocks'] = [{'id':'b1','text':text}]
    evidence = {'id':'ev1','source':'ocr_block','locator':'b1','quote':quote}
    match = locate_on_page(quote, 'b1', source['ocr']['blocks'])
    found = locate_anchor({'evidence_id':'ev1','text':anchor}, evidence, match, source)
    assert found['accepted'] and found['position_resolved']
    assert text[found['start']:found['end']] == found['matched_quote']
    assert source['ocr']['blocks'][0]['text'] == text


def test_anchor_span_and_repeated_positions():
    source=demo_snapshot(); text='Paulus ait. Paulus ait. Deinde negat.'
    source['ocr']['blocks']=[{'id':'b1','text':text}]
    ev={'id':'ev1','source':'ocr_block','locator':'b1','quote':'Paulus ait.'}
    match=locate_on_page(ev['quote'],'b1',source['ocr']['blocks'])
    found=locate_anchor({'evidence_id':'ev1','text':'Paulus'},ev,match,source)
    assert found['accepted'] and not found['position_resolved']
    assert len(found['candidates'])==2 and 'start' not in found
    assert not locate_anchor({'evidence_id':'ev1','text':'negat'},ev,match,source)['accepted']


@pytest.mark.parametrize('provider', ['ollama', 'openai', 'compatible', 'meta'])
@pytest.mark.parametrize('structured', ['schema', 'json', 'none'])
def test_ordered_wire_and_frozen_roundtrip(provider, structured, monkeypatch):
    monkeypatch.setenv('KG_TEST_KEY', 'synthetic-key')
    config = providers.normalize_config({'provider':provider,'model':'test','api_key_env':'KG_TEST_KEY','structured_output':structured})
    prompt = render(demo_snapshot(), C, 'concepts_relations')
    request = request_spec(config, prompt)
    saved = json.loads(dumps(request))
    assert saved == request and wire_text(saved) == wire_text(request)
    wire = json.loads(wire_text(saved))
    if structured == 'schema':
        schema = wire['format'] if provider=='ollama' else wire['response_format']['json_schema']['schema']
        assert list(schema['properties']) == prompt['variant']['response_property_order']
        Draft202012Validator(schema).validate(read_json(FIXTURE))
        if provider != 'ollama': assert wire['response_format']['json_schema']['name'] == 'kg_extraction_v3_1'
    called=[]
    def transport(req, timeout):
        called.append(req.data)
        return io.BytesIO(b'{"message":{"content":"{}"}}' if provider=='ollama' else b'{"choices":[{"message":{"content":"{}"}}]}')
    send(config, saved, transport=transport)
    assert called == [wire_text(request).encode()]
    assert 'synthetic-key' not in dumps(saved)
    saved['body']['model']='tampered'
    with pytest.raises(ValueError, match='hash mismatch'): send(config,saved,transport=transport)
    assert len(called)==1
    for variant in (A,B):
        old=render(demo_snapshot(),variant,'concepts_relations')
        req=request_spec(config,old)
        assert req==providers.request_spec(config,old) and 'presentation' not in req
        assert wire_text(req)==dumps(req['body'])


@pytest.mark.parametrize('mode',['concepts_only','concepts_relations','full_multimodal'])
def test_storage_fixture_resume_cache_comparison_and_reviews(tmp_path, mode):
    store=Store(tmp_path/'demo.db'); case=store.case(demo_snapshot())
    exp=plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}],variants=[A,B,C],modes=[mode],repetitions=2)
    runs=execute_experiment(store,exp,caller=lambda *a: pytest.fail('No model calls'))
    assert len(runs)==6 and all(r['status']=='completed' for r in runs)
    before = [store.get_run(r['id']) for r in runs]
    execute_experiment(store,exp,resume=True,caller=lambda *a: pytest.fail('No replay'))
    assert [store.get_run(r['id']) for r in runs] == before
    runs=[store.get_run(r['id']) for r in runs]
    new=next(r for r in runs if r['prepared']['variant']['id']==C)
    old=next(r for r in runs if r['prepared']['variant']['id']==A)
    assert not new['result']['inference_performed']
    assert 'pairs' not in compare_pair(old,new)
    store.review(new['id'], 'u1' if mode!='concepts_only' else 'c1', 'uncertain')
    assert store.get_run(new['id'])['result']==new['result']
    metrics=report(store,exp)
    assert metrics['summary']['actual_inference_runs']==0
    assert 'v3.1 discourse/action' in markdown(metrics)
    exp2=plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}],variants=[C],modes=[mode])
    cached=execute_experiment(store,exp2)[0]
    assert cached['status']=='cached'
    assert cached['cached_from'] in {r['id'] for r in runs if r['prepared']['variant']['id']==C}


def test_empty_output_and_old_contract_rejection():
    source,data=example()
    for key,value in data.items():
        if isinstance(value,list):data[key]=[]
    assert check(source,data)['valid']
    old=render(source,A,'concepts_relations')
    assert not evaluate_response(source,{'raw_response':dumps(data)},old)['schema_valid']
