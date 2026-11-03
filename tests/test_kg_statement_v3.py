"""Synthetic discourse contracts and offline end-to-end experiments; no billing."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from tools.knowledge_graph.playground.common import ROOT, digest, read_json
from tools.knowledge_graph.playground.prompt_registry import freeze_template, render
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.statement_contract import RESERVED, graph_view
from tools.knowledge_graph.playground.validation_dispatch import evaluate_response
from tools.knowledge_graph.playground.providers import normalize_config, request_spec
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment
from tools.knowledge_graph.playground.comparison import compare_pair
from tools.knowledge_graph.playground.reporting import report, export_jsonl

VARIANT = 'concepts-v3-statements-confidence'
CONFIDENCE = {'score': .5, 'rationale': 'Fixture sintética: não é avaliação do modelo.'}


def discourse():
    source = demo_snapshot()
    quote = 'Helena ait: Si adversarii iterum obiciant, verba Lucii eis respondebunt.'
    source['ocr']['blocks'] = [{'id': 'b1', 'type': 'texto_principal', 'script': 'latino', 'bbox': None, 'text': quote}]
    source['v2']['segments'] = [{'id': 's1', 'summary': 'Helena apresenta as palavras de Lúcio como resposta a uma objeção futura.'}]
    data = {'schema_version': 'kg-extraction-v3', 'concepts': [], 'entities': [], 'claims': [],
            'events': [], 'relations': [], 'warnings': [],
            'evidence': [{'id': 'ev1', 'source': 'ocr_block', 'locator': 'b1', 'quote': quote}]}
    for identifier, label, kind in [('e1', 'Helena', 'PERSON'), ('e2', 'Lúcio', 'PERSON'), ('e3', 'Adversários', 'GROUP')]:
        data['entities'].append({'id': identifier, 'label': label, 'type': kind, 'origin': 'current_page',
                                 'evidence_ids': ['ev1'], 'model_confidence': dict(CONFIDENCE)})
    data['claims'] = [{'id': 'p1', 'proposition': 'Se os adversários objetarem novamente, as palavras de Lúcio lhes responderão.',
                      'mode': 'conditional', 'evidence_ids': ['ev1'], 'model_confidence': dict(CONFIDENCE),
                      'attributions': [{'entity_id': 'e1', 'stance': 'AFFIRMS', 'scope': 'direct', 'evidence_ids': ['ev1']}],
                      'participants': [{'entity_id': 'e2', 'role': 'agent', 'evidence_ids': ['ev1']},
                                       {'entity_id': 'e2', 'role': 'cited_authority', 'evidence_ids': ['ev1']},
                                       {'entity_id': 'e3', 'role': 'target', 'evidence_ids': ['ev1']}]}]
    return source, data


def check(source, data, **prepared):
    p = dict(freeze_template(VARIANT, 'concepts_relations'), validation_policy='local-quote-v5', **prepared)
    return evaluate_response(source, {'raw_response': json.dumps(data)}, p)


@pytest.mark.parametrize('mode', ['concepts_only', 'concepts_relations', 'full_multimodal'])
def test_versioned_prompt_schema_and_no_duplicate_components(mode):
    p = freeze_template(VARIANT, mode)
    Draft202012Validator.check_schema(p['schema'])
    assert p['schema_version'] == 'kg-extraction-v3'
    assert p['system_prompt'].count('EXPERIMENT MODE\n') == 1
    assert p['system_prompt'].count('MODEL CONFIDENCE\n') == 1
    assert 'participants:' in p['system_prompt'] and 'attributions:' in p['system_prompt']
    for field, prop in p['schema']['properties']['claims']['items']['properties'].items():
        assert field in p['system_prompt']
        for value in prop.get('enum', []):
            assert value in p['system_prompt']
    for field in ('attributions', 'participants'):
        for key, prop in p['schema']['properties']['claims']['items']['properties'][field]['items']['properties'].items():
            assert key in p['system_prompt']
            for value in prop.get('enum', []):
                assert value in p['system_prompt']
    old = freeze_template('concepts-v2.7-compact-confidence', 'concepts_relations')
    assert old['schema_version'] == 'kg-extraction-v2'
    assert old['system_hash'] == '8915bea0741cdf51a3f7812f99d0b96d81814b97adf7c70a7d0d0b1a61438732'


def test_only_schema_and_prompt_change_in_model_input():
    source, _ = discourse()
    old = render(source, 'concepts-v2.7-compact-confidence', 'concepts_relations')
    new = render(source, VARIANT, 'concepts_relations')
    assert {k: v for k, v in old['user_payload'].items() if k != 'response_schema'} == {
        k: v for k, v in new['user_payload'].items() if k != 'response_schema'}


def test_actor_roles_never_manufacture_a_stance_and_scopes_survive():
    source, data = discourse()
    before = digest((source, data))
    checked = check(source, data)
    assert checked['valid'] and checked['semantic_status'] == 'pending_review'
    assert checked['parsed'] == data and checked['validated_extraction'] == data
    assert data['relations'] == []
    edges = checked['graph_projection']['relations']
    assert [(r['source_id'], r['predicate'], r['target_id']) for r in edges] == [
        ('e1', 'AFFIRMS', 'p1'), ('p1', 'HAS_AGENT', 'e2'), ('p1', 'CITES_AUTHORITY', 'e2'), ('p1', 'HAS_TARGET', 'e3')]
    assert all(r['claim_mode'] == 'conditional' and r['evidence_ids'] == ['ev1'] for r in edges)
    assert edges[0]['scope'] == 'direct'
    assert digest((source, data)) == before
    view = graph_view(checked)
    assert len(view['relations']) == 4 and checked['parsed']['relations'] == []


def test_wrong_speaker_requires_semantic_review_even_with_new_fields():
    source, data = discourse()
    data['claims'][0]['attributions'][0]['entity_id'] = 'e2'
    checked = check(source, data)
    assert checked['valid']  # Existence of an entity ID cannot prove attribution.
    assert checked['semantic_status'] == 'pending_review' and checked['gold_status'] == 'not_gold'


def test_unresolved_voice_preserves_statement_and_participants_without_stance():
    source, data = discourse()
    data['claims'][0]['attributions'] = []
    checked = check(source, data)
    assert checked['valid']
    assert all(r['predicate'] != 'AFFIRMS' for r in checked['graph_projection']['relations'])
    assert any(d['code'] == 'statement_attribution_unresolved' for d in checked['diagnostics'])


@pytest.mark.parametrize('field', ['attributions', 'participants'])
@pytest.mark.parametrize('error', ['entity', 'evidence', 'duplicate', 'missing_array', 'empty_evidence'])
def test_nested_integrity_checks(field, error):
    source, data = discourse()
    item = data['claims'][0][field][0]
    if error == 'entity': item['entity_id'] = 'p1'
    elif error == 'evidence': item['evidence_ids'] = ['missing']
    elif error == 'duplicate': data['claims'][0][field].append(deepcopy(item))
    elif error == 'missing_array': del data['claims'][0][field]
    else: item['evidence_ids'] = []
    checked = check(source, data)
    assert not checked['valid'] and not checked['structural_valid']
    assert not checked.get('graph_projection') and not checked['graph_inspectable']


@pytest.mark.parametrize('predicate', sorted(RESERVED))
def test_model_cannot_duplicate_projected_edges(predicate):
    source, data = discourse()
    data['relations'] = [{'id': 'r1', 'source_id': 'e1', 'target_id': 'p1', 'predicate': predicate,
                          'grounding': 'explicit', 'evidence_ids': ['ev1'], 'model_confidence': dict(CONFIDENCE)}]
    assert not check(source, data)['valid']


@pytest.mark.parametrize('provider', ['meta', 'ollama', 'openai', 'compatible'])
def test_provider_schema_construction_offline(provider):
    source, data = discourse()
    prompt = render(source, VARIANT, 'concepts_relations')
    config = normalize_config({'provider': provider, 'model': 'offline-test', 'structured_output': 'schema'})
    body = request_spec(config, prompt)['body']
    schema = body['format'] if provider == 'ollama' else body['response_format']['json_schema']['schema']
    Draft202012Validator(schema).validate(data)
    assert 'attributions' in json.dumps(schema)
    assert 'uniqueItems' not in json.dumps(schema)


def test_frozen_v2_rejects_v3_and_empty_v3_is_valid():
    source, data = discourse()
    p = freeze_template('concepts-v2.7-compact-confidence', 'concepts_relations')
    assert not evaluate_response(source, {'raw_response': json.dumps(data)}, p)['valid']
    for field in ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence'): data[field] = []
    assert check(source, data)['valid']


def test_projected_scope_changes_are_not_equal_relations():
    source, data = discourse()
    a = {'id': 1, 'validation': check(source, data)}
    data['claims'][0]['attributions'][0]['scope'] = 'hypothetical'
    b = {'id': 2, 'validation': check(source, data)}
    pair = compare_pair(a, b)
    assert len(pair['relations_only_left']) == len(pair['relations_only_right']) == 1
    assert pair['relations_only_left'][0]['scope'] == 'direct'
    assert pair['relations_only_right'][0]['scope'] == 'hypothetical'


def test_editor_and_reported_author_keep_distinct_positions():
    source, data = discourse()
    text = 'Editor ait: Cassius annum XL proponit; ego annum L puto.'
    source['ocr']['blocks'][0]['text'] = text
    data['evidence'][0]['quote'] = text
    data['entities'][0]['label'] = 'Editor'
    data['entities'][1]['label'] = 'Cássio'
    first = data['claims'][0]
    first.update(proposition='A data é o ano XL.', mode='reported', participants=[],
                 attributions=[{'entity_id':'e2', 'stance':'AFFIRMS', 'scope':'reported', 'evidence_ids':['ev1']},
                               {'entity_id':'e1', 'stance':'REPORTS', 'scope':'direct', 'evidence_ids':['ev1']}])
    second = deepcopy(first)
    second.update(id='p2', proposition='A data é o ano L.', mode='asserted',
                  attributions=[{'entity_id':'e1', 'stance':'AFFIRMS', 'scope':'direct', 'evidence_ids':['ev1']}])
    data['claims'].append(second)
    checked = check(source, data)
    assert checked['valid']
    edges = checked['graph_projection']['relations']
    assert not any(r['source_id']=='e2' and r['target_id']=='p2' for r in edges)
    assert any(r['source_id']=='e1' and r['target_id']=='p1' and r['predicate']=='REPORTS' for r in edges)


def test_storage_projection_review_export_and_synthetic_runner(tmp_path):
    store = Store(tmp_path / 'test.db')
    case = store.case(demo_snapshot())
    exp = plan_experiment(store, [case], [{'provider': 'fixture', 'model': 'demo'}], variants=[VARIANT], repetitions=2)
    runs = execute_experiment(store, exp, caller=lambda *a: pytest.fail('No API calls'))
    assert len(runs) == 2 and all(r['status'] == 'completed' for r in runs)
    stored = [store.get_run(r['id']) for r in runs]
    assert all(r['result']['synthetic_replay'] and not r['result']['inference_performed'] for r in stored)
    run = store.get_run(runs[0]['id'])
    edge = run['validation']['graph_projection']['relations'][0]
    store.review(run['id'], edge['id'], 'uncertain', 'attribution', 'Synthetic review')
    after = store.get_run(run['id'])
    assert after['result'] == run['result']
    assert after['reviews'][0]['object_id'] == edge['id']
    assert report(store, exp)['runs'][0]['projected_relations'] == 3
    assert '@v3:' in ''.join(export_jsonl(store, exp))


def test_case_id_cli_reuses_frozen_sources_without_reloading(tmp_path, monkeypatch):
    from scripts.playgrounds.kg_extractor_playground import main
    store = Store(tmp_path / 'test.db')
    case = store.case(demo_snapshot())
    monkeypatch.setattr('tools.knowledge_graph.playground.service.Playground.load', lambda *a, **kw: pytest.fail('Must use snapshot'))
    args = ['run', '--db', str(store.path), '--case-ids', str(case['id']), '--provider', 'fixture', '--model', 'demo',
            '--prompts', VARIANT, '--dry-run', '--benchmark-dir', str(tmp_path / 'missing')]
    assert main(args) in (0, None)
    run = store.get_run(store.list_runs()[0]['id'])
    assert run['case_id'] == case['id'] and run['status'] == 'planned'
    assert run['result'] is None and run['prepared']['schema_version'] == 'kg-extraction-v3'
    held = deepcopy(case['snapshot']); held['source']['split'] = 'blind_holdout'
    held_case = store.case(held)
    args[args.index('--case-ids') + 1] = str(held_case['id'])
    with pytest.raises(ValueError, match='holdout'): main(args)
