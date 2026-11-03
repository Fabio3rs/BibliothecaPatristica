"""Field semantics must reach the provider without changing historical contracts."""
from copy import deepcopy
import re

import pytest

from tools.knowledge_graph.playground.common import ROOT, digest
from tools.knowledge_graph.playground.experiment_runner import (
    execute_experiment, plan_experiment, prepare,
)
from tools.knowledge_graph.playground.prompt_registry import MODES, freeze_template
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store


BASE = 'concepts-v2.5-semantics-confidence'
CANDIDATE = 'concepts-v2.6-field-contract-confidence'
COMPACT = 'concepts-v2.7-compact-confidence'
DISCOURSE = 'concepts-v2.8-discourse-confidence'
DELTA = ROOT / 'tools/knowledge_graph/prompts/fields_v2.6_contract.txt'
BASE_HASH = '8926f528d8a0a540b836f5d288acdacf869fe56998f538d3089148cfed4111ec'


@pytest.mark.parametrize('mode', MODES)
def test_field_glossary_is_the_only_system_change(mode):
    baseline = freeze_template(BASE, mode)
    candidate = freeze_template(CANDIDATE, mode)
    glossary = DELTA.read_text(encoding='utf-8').rstrip('\n')
    assert candidate['system_prompt'].count(glossary) == 1
    assert candidate['system_prompt'].replace(glossary + '\n\n', '', 1) == baseline['system_prompt']
    assert candidate['schema'] == baseline['schema']
    assert candidate['schema_hash'] == baseline['schema_hash']
    for marker in ('MODEL-REPORTED CONFIDENCE —', 'EXPERIMENT MODE\n'):
        assert candidate['system_prompt'].count(marker) == 1
    assert candidate['variant']['base_system_hash'] == BASE_HASH
    assert freeze_template(BASE, 'concepts_relations')['system_hash'] == BASE_HASH


@pytest.mark.parametrize('group', ['concepts', 'entities', 'claims', 'events', 'relations', 'evidence', 'warnings'])
def test_every_schema_field_and_enum_has_an_operational_definition(group):
    """Catch schema/glossary drift, not just a list of enum names anywhere in a prompt."""
    glossary = DELTA.read_text(encoding='utf-8')
    item = freeze_template(CANDIDATE, 'concepts_relations')['schema']['properties'][group]['items']
    for name, spec in item['properties'].items():
        assert re.search(r'\b' + re.escape(name) + r'\b', glossary), (group, name)
        for value in spec.get('enum', []):
            assert re.search(r'\b' + re.escape(value) + r'\s*=\s*\S', glossary), (group, name, value)


def test_confidence_definitions_and_open_predicates_remain_compatible():
    template = freeze_template(CANDIDATE, 'concepts_relations')
    glossary = DELTA.read_text(encoding='utf-8')
    for name in template['schema']['$defs']['model_confidence']['properties']:
        assert re.search(r'\b' + name + r'\b', glossary)
    assert template['schema']['properties']['relations']['items']['properties']['predicate'] == {'type': 'string'}
    assert 'an open string, not an enum' in glossary
    assert 'claim -> ATTRIBUTES_TO -> actor' in glossary
    for predicate in ('AFFIRMS', 'DEFENDS', 'REFUTES'):
        assert f'actor -> {predicate} -> claim' in glossary
    # These are instructions under test, not assertions that a model obeys them.
    assert 'modern Portuguese label alone does not make content inferred' in glossary
    assert 'opponent\'s rejected position can remain reported' in glossary
    assert 'A hypothetical target does not qualify the incoming act' in glossary


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('variant', [CANDIDATE, COMPACT, DISCOURSE])
def test_meta_request_changes_only_system_prompt_and_cache_key(mode, variant, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Prompt preparation must not send requests or read credentials')

    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    monkeypatch.setattr('tools.knowledge_graph.playground.providers.os.getenv', forbidden)
    snapshot = demo_snapshot()
    original = deepcopy(snapshot)
    case = {'snapshot': snapshot, 'snapshot_hash': digest(snapshot)}
    config = {'provider': 'meta', 'model': 'muse-spark-1.3-contributor'}
    old = prepare(case, config, variant=BASE, mode=mode)
    new = prepare(case, config, variant=variant, mode=mode)
    request = deepcopy(new['request'])
    request['body']['messages'][0]['content'] = old['system_prompt']
    assert request == old['request']
    assert new['user_prompt'] == old['user_prompt']
    assert new['config'] == old['config']
    assert new['ocr_preparation'] == old['ocr_preparation']
    assert new['schema_hash'] == old['schema_hash']
    assert new['input_hash'] != old['input_hash']
    assert 'reasoning_effort' not in new['request']['body']
    assert snapshot == original


@pytest.mark.parametrize('variant', [CANDIDATE, COMPACT, DISCOURSE])
def test_synthetic_round_trip_preserves_history_and_independent_repetitions(tmp_path, monkeypatch, variant):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline fixture must not call a provider')

    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    store = Store(tmp_path / 'playground.db')
    case = store.case(demo_snapshot())
    profile = {'provider': 'fixture', 'model': 'synthetic-confidence'}
    baseline = plan_experiment(store, [case], [profile], variants=[BASE])
    execute_experiment(store, baseline, caller=forbidden)
    old_run = store.get_run(store.list_runs(baseline)[0]['id'])
    candidate = plan_experiment(store, [case], [profile], variants=[variant], repetitions=2)
    assert store.experiment_plan(candidate)['bypass_cache'] is True
    execute_experiment(store, candidate, caller=forbidden)
    rows = store.list_runs(candidate)
    assert len(rows) == 2 and {r['repetition'] for r in rows} == {1, 2}
    for row in rows:
        run = store.get_run(row['id'])
        assert run['status'] == 'completed'
        assert run['result']['inference_performed'] is False
        assert run['validation']['valid']
        assert run['validation']['semantic_status'] == 'pending_review'
    assert store.get_run(old_run['id']) == old_run


@pytest.mark.parametrize('mode', MODES)
def test_compact_modes_keep_schema_and_frozen_predecessor(mode):
    old = freeze_template(CANDIDATE, mode)
    compact = freeze_template(COMPACT, mode)
    text = compact['system_prompt']
    assert compact['schema'] == old['schema']
    assert compact['schema_hash'] == old['schema_hash']
    assert len(text) < .4 * len(old['system_prompt'])
    assert text.count('EXPERIMENT MODE\n') == 1
    assert text.count('MODEL CONFIDENCE\n') == 1
    assert 'FIELD AND ENUM MEANINGS' not in text
    assert 'SUBJECT DISCOVERY AND ARGUMENT CHECK' not in text
    assert not compact['variant'].get('contract_rules_file')
    suffix = text.split('EXPERIMENT MODE\n')[1]
    assert ('keep entities, claims, events and relations empty' in suffix) == (mode == 'concepts_only')
    if mode == 'full_multimodal':
        assert 'attached image' in suffix
    assert freeze_template(CANDIDATE, 'concepts_relations')['system_hash'] == compact['variant']['base_system_hash']


def test_compression_keeps_all_field_and_enum_definitions():
    compact = freeze_template(COMPACT, 'concepts_relations')
    text = compact['system_prompt']
    for name, spec in compact['schema']['properties'].items():
        if name == 'schema_version':
            assert name in text
            continue
        for field, definition in spec['items']['properties'].items():
            assert re.search(r'\b' + re.escape(field) + r'\b', text), (name, field)
            for value in definition.get('enum', []):
                assert re.search(r'\b' + re.escape(value) + r'\s*=\s*\S', text), (name, field, value)
    for field in compact['schema']['$defs']['model_confidence']['properties']:
        assert re.search(r'\b' + field + r'\b', text)
    assert 'A person described or criticized is not thereby its speaker.' in text
    assert 'Modern labels and translation alone do not imply inferred.' in text
    assert 'A hypothetical target does not make its incoming stance hypothetical' in text
    assert 'Never stitch, translate, transliterate or repair a quotation from the image.' in text


@pytest.mark.parametrize('mode', MODES)
def test_discourse_delta_is_isolated_without_new_fields_or_duplicate_modes(mode):
    baseline = freeze_template(COMPACT, mode)
    candidate = freeze_template(DISCOURSE, mode)
    delta = (ROOT / 'tools/knowledge_graph/prompts/discourse_v2.8_contract.txt').read_text(encoding='utf-8')
    assert candidate['system_prompt'].count(delta) == 1
    assert candidate['system_prompt'].replace('\n\n' + delta, '', 1) == baseline['system_prompt']
    assert candidate['schema'] == baseline['schema']
    assert candidate['schema_hash'] == baseline['schema_hash']
    assert candidate['system_prompt'].count('EXPERIMENT MODE\n') == 1
    assert candidate['system_prompt'].count('MODEL CONFIDENCE\n') == 1
    assert candidate['system_prompt'].split('EXPERIMENT MODE\n')[1] == baseline['system_prompt'].split('EXPERIMENT MODE\n')[1]
    assert freeze_template(COMPACT, 'concepts_relations')['system_hash'] == candidate['variant']['base_system_hash']
