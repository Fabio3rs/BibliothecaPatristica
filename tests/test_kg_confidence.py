"""Versioned self-reports are test data, never calibrated scores or semantic gold."""
from copy import deepcopy
import io
import json

import pytest
from jsonschema import Draft202012Validator

from tools.knowledge_graph.playground.common import KIT, ROOT, dumps, read_json
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.providers import normalize_config, request_spec, send
from tools.knowledge_graph.playground.validation import evaluate, PARENTS
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment, prepare
from tools.knowledge_graph.playground.comparison import compare_runs
from tools.knowledge_graph.playground.reporting import export_jsonl

FIXTURE = ROOT / 'tools/knowledge_graph/playground/fixtures/page-output.confidence.demo.json'


def confidence_prompt(mode='concepts_relations'):
    return render(demo_snapshot(), 'concepts-v2-confidence', mode)


@pytest.mark.parametrize('mode', ['concepts_only', 'concepts_relations', 'full_multimodal'])
def test_confidence_is_an_explicit_versioned_contract(mode):
    original = render(demo_snapshot(), 'concepts-v1', mode)
    improved = render(demo_snapshot(), 'concepts-v1.2', mode)
    confidence = confidence_prompt(mode)
    assert original['schema'] == improved['schema']
    assert original['system_hash'] != improved['system_hash']
    assert improved['schema_version'] == 'kg-extraction-v1'
    assert confidence['schema_version'] == 'kg-extraction-v2'
    assert confidence['schema_hash'] != improved['schema_hash']
    assert confidence['user_payload']['response_schema'] == confidence['schema']
    assert 'MODEL-REPORTED CONFIDENCE' in confidence['system_prompt']
    assert 'MODEL-REPORTED CONFIDENCE' not in improved['system_prompt']
    assert confidence['user_payload']['ocr'] == improved['user_payload']['ocr']
    assert confidence['user_payload']['v2'] == improved['user_payload']['v2']
    Draft202012Validator.check_schema(confidence['schema'])


def test_v2_extends_only_version_and_per_object_confidence():
    original = read_json(KIT / 'kg-extraction-v1.schema.json')
    extension = deepcopy(confidence_prompt()['schema'])
    extension.pop('$defs')
    extension['title'] = original['title']
    extension['description'] = original['description']
    extension['properties']['schema_version']['const'] = 'kg-extraction-v1'
    for group in PARENTS:
        item = extension['properties'][group]['items']
        del item['properties']['model_confidence']
        item['required'].remove('model_confidence')
    assert original == extension


@pytest.mark.parametrize('value', [-.1, 1.01, float('nan'), float('inf'), None, True, '0.8', {'score':.8}])
def test_invalid_confidence_scores_rejected(value):
    data = read_json(FIXTURE)
    data['concepts'][0]['model_confidence']['score'] = value
    assert not evaluate(demo_snapshot(), data, confidence_prompt()['schema'])['structural_valid']


@pytest.mark.parametrize('value', ['', 'x' * 401, None])
def test_confidence_needs_bounded_rationale(value):
    data = read_json(FIXTURE)
    data['relations'][0]['model_confidence']['rationale'] = value
    assert not evaluate(demo_snapshot(), data, confidence_prompt()['schema'])['structural_valid']


@pytest.mark.parametrize('group', PARENTS)
def test_every_extracted_object_requires_confidence(group):
    data = read_json(FIXTURE)
    data['events'] = [{'id':'v1', 'description':'Evento sintético', 'narrative_status':'uncertain',
                       'time_expression':None, 'evidence_ids':[],
                       'model_confidence':{'score':.2, 'rationale':'Somente teste de contrato.'}}]
    del data[group][0]['model_confidence']
    assert not evaluate(demo_snapshot(), data, confidence_prompt()['schema'])['structural_valid']


@pytest.mark.parametrize('score', [0, .5, 1])
def test_confidence_never_changes_evidence_or_semantic_status(score):
    data = read_json(FIXTURE)
    data['relations'][0]['predicate'] = 'DEFENDS'  # Source says REFUTES.
    data['relations'][0]['model_confidence']['score'] = score
    checked = evaluate(demo_snapshot(), data, confidence_prompt()['schema'])
    assert checked['valid']  # Mechanical validation cannot adjudicate attribution.
    assert checked['semantic_status'] == 'pending_review'
    assert checked['gold_status'] == 'not_gold'
    data['evidence'][0]['quote'] = 'Invented quote with no supporting source.'
    assert not evaluate(demo_snapshot(), data, confidence_prompt()['schema'])['source_valid']


def test_v1_and_v2_cannot_be_silently_interchanged():
    data = read_json(FIXTURE)
    assert not evaluate(demo_snapshot(), data)['structural_valid']
    old = read_json(KIT / 'page-output.demo.json')
    assert evaluate(demo_snapshot(), old)['valid']
    assert not evaluate(demo_snapshot(), old, confidence_prompt()['schema'])['structural_valid']
    for group in PARENTS:
        data[group] = []
    data['evidence'] = []
    assert evaluate(demo_snapshot(), data, confidence_prompt()['schema'])['valid']


@pytest.mark.parametrize('provider', ['ollama', 'meta', 'openai', 'compatible'])
def test_v2_mock_provider_round_trip_uses_actual_schema_version(provider, monkeypatch):
    monkeypatch.setenv('KG_CONFIDENCE_TEST_KEY', 'synthetic-test-key')
    config = normalize_config({'provider':provider, 'model':'offline-test', 'structured_output':'schema',
                               'api_key_env':'KG_CONFIDENCE_TEST_KEY'})
    prompt = confidence_prompt()
    request = request_spec(config, prompt)
    schema = request['body']['format'] if provider == 'ollama' else request['body']['response_format']['json_schema']['schema']
    data = read_json(FIXTURE)
    Draft202012Validator(schema).validate(data)
    assert prompt['schema']['properties']['schema_version'] == {'const':'kg-extraction-v2'}
    response = ({'message':{'content':dumps(data)}, 'done_reason':'stop'} if provider == 'ollama' else
                {'choices':[{'message':{'content':dumps(data)}, 'finish_reason':'stop'}]})
    calls = []
    def transport(req, timeout):
        calls.append(req)
        return io.BytesIO(dumps(response).encode())
    result = send(config, request, transport=transport)
    assert len(calls) == 1
    assert evaluate(demo_snapshot(), result['raw_response'], prompt['schema'])['valid']


def test_fixture_storage_cache_resume_export_and_comparison(tmp_path):
    store = Store(tmp_path / 'test.db')
    case = store.case(demo_snapshot())
    config = {'provider':'fixture', 'model':'synthetic-confidence'}
    variants = ['concepts-v1.2', 'concepts-v2-confidence']
    assert prepare(case, config, variant=variants[0])['input_hash'] != prepare(case, config, variant=variants[1])['input_hash']
    experiment = plan_experiment(store, [case], [config], variants=variants)
    execute_experiment(store, experiment)
    identifiers = [r['id'] for r in store.list_runs(experiment)]
    assert len(identifiers) == 2
    runs = [store.get_run(i) for i in identifiers]
    assert all(r['status'] == 'completed' and r['result']['synthetic_replay'] for r in runs)
    assert all(r['result']['inference_performed'] is False and r['result']['usage'] == {} for r in runs)
    old = next(r for r in runs if r['prepared']['schema_version'] == 'kg-extraction-v1')
    new = next(r for r in runs if r['prepared']['schema_version'] == 'kg-extraction-v2')
    assert 'model_confidence' not in old['validation']['parsed']['concepts'][0]
    assert new['validation']['parsed']['concepts'][0]['model_confidence']['score'] == .5
    compared = compare_runs(store, identifiers)['pairs'][0]
    assert len(compared['matched']) == 5
    assert len(compared['model_confidence_differences']) == 8
    assert not compared['evidence_differences'] and not compared['relations_only_left']
    before = deepcopy(new['result'])
    execute_experiment(store, experiment, resume=True)
    assert store.get_run(new['id'])['result'] == before
    assert len(store.list_runs(experiment)) == 2
    exported = [json.loads(row) for row in export_jsonl(store, experiment)]
    assert any(row['run']['prepared']['schema_version'] == 'kg-extraction-v2' for row in exported)
    repeat = plan_experiment(store, [case], [config], variants=[variants[1]])
    execute_experiment(store, repeat)
    assert store.list_runs(repeat)[0]['status'] == 'cached'
    independent = plan_experiment(store, [case], [config], variants=[variants[1]], repetitions=2)
    execute_experiment(store, independent)
    assert all(r['status'] == 'completed' for r in store.list_runs(independent))
