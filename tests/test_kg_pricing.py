"""User-provided tariffs, local/cloud isolation and immutable cost history."""
from copy import deepcopy
import json

import pytest

from tools.knowledge_graph.playground import pricing
from tools.knowledge_graph.playground.common import KIT, dumps, read_json
from tools.knowledge_graph.playground.experiment_runner import execute_experiment, plan_experiment, prepare
from tools.knowledge_graph.playground.providers import normalize_config
from tools.knowledge_graph.playground.reporting import csv_report, export_jsonl, markdown, report
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store


@pytest.mark.parametrize('provider,model,base_url,rates', [
    ('meta', 'muse-spark-1.3-contributor', None, (.10, .20)),
    ('meta', 'muse-spark-1.3', None, (1.25, 4.25)),
    ('meta', 'muse-spark-1.2-contributor', None, (.10, .20)),
    ('ollama', 'gemma4:cloud', None, (.14, .40)),
    ('ollama', 'gemma4:31b-cloud', None, (.14, .40)),
    ('ollama', 'gemma4:31b', 'https://ollama.com', (.14, .40)),
    ('ollama', 'gpt-oss:20b-cloud', None, (.07, .30)),
    ('ollama', 'gpt-oss:20b', 'https://ollama.com', (.07, .30)),
])
def test_registered_rates(provider, model, base_url, rates):
    config = {'provider': provider, 'model': model}
    if base_url:
        config['base_url'] = base_url
    normalized = normalize_config(config)
    assert normalized['prices'] == dict(input_per_million=rates[0], output_per_million=rates[1])
    assert normalize_config(normalized) == normalized
    ref = pricing.reference(normalized)
    assert ref['source'] == 'user_provided' and ref['recorded_on'] == '2026-11-01'
    assert len(ref['sha256']) == 64


@pytest.mark.parametrize('provider,model,endpoint', [
    ('ollama', 'gemma4:31b', 'http://localhost:11434'),
    ('ollama', 'gpt-oss:20b', 'http://localhost:11434'),
    ('ollama', 'gpt-oss:120b-cloud', 'https://ollama.com'),
    ('ollama', 'gemma4:31b', 'https://ollama.com.example.invalid'),
    ('meta', 'muse-spark-1.2', 'https://api.meta.ai/v1'),
    ('compatible', 'muse-spark-1.3', 'http://localhost:8000/v1'),
    ('ollama', 'custom-gemma4:cloud', 'http://localhost:11434'),
])
def test_unknown_models_and_local_inference_remain_unpriced(provider, model, endpoint):
    c = normalize_config({'provider': provider, 'model': model, 'base_url': endpoint})
    assert c['prices'] == {} and pricing.lookup(c) is None


@pytest.mark.parametrize('prices', [{}, {'input_per_million': 0, 'output_per_million': 0},
                                     {'input_per_million': 2, 'output_per_million': 3, 'cached_input_per_million': 1}])
def test_explicit_prices_override_defaults(prices):
    c = normalize_config({'provider': 'meta', 'model': 'muse-spark-1.3', 'prices': prices})
    assert c['prices'] == prices
    assert normalize_config(c) == c
    assert pricing.reference(c)['source'] == ('explicit_rates' if prices else 'unpriced')


def test_total_output_not_double_billed_and_no_unprovided_cache_discount():
    usage = {'input_tokens': 9412, 'cached_input_tokens': 8000, 'output_tokens': 8192, 'reasoning_tokens': 6824}
    prices = {'input_per_million': .1, 'output_per_million': .2}
    assert pricing.estimate_cost(usage, prices) == pytest.approx(.0025796)
    assert pricing.estimate_cost({'input_tokens': 9412}, prices) is None
    with pytest.raises(ValueError):
        normalize_config({'provider': 'meta', 'model': 'test', 'prices': {'input_per_million': True}})


def legacy_run(result=None):
    return {'config': {'provider': 'meta', 'model': 'muse-spark-1.3-contributor', 'prices': {}},
            'prepared': {}, 'cached_from': None,
            'result': result or {'usage': {'input_tokens': 9412, 'output_tokens': 8192, 'reasoning_tokens': 6824},
                                 'inference_performed': True, 'cost_usd': None}}


def test_legacy_reestimate_is_a_read_only_projection():
    run = legacy_run()
    before = deepcopy(run)
    cost = pricing.run_estimate(run)
    assert cost['cost_usd'] == pytest.approx(.0025796)
    assert cost['basis'] == 'catalog_reestimate'
    assert cost['pricing']['prices'] == {'input_per_million': .1, 'output_per_million': .2}
    assert run == before
    run['prepared']['pricing_reference'] = {'source': 'unpriced', 'prices': {}}
    assert pricing.run_estimate(run)['cost_usd'] is None


@pytest.mark.parametrize('result,expected', [
    ({'cost_usd': .123, 'inference_performed': True}, .123),
    ({'cost_usd': 0, 'inference_performed': False, 'usage': {}}, 0),
    ({'cost_usd': None, 'inference_performed': None, 'usage': {}}, None),
    ({'inference_performed': True, 'usage': {'input_tokens': 9412}}, None),
])
def test_saved_costs_replays_and_missing_usage(result, expected):
    assert pricing.run_estimate(legacy_run(result))['cost_usd'] == expected


def test_plan_freezes_rates_resume_and_reports(tmp_path, monkeypatch):
    store = Store(tmp_path / 'lab.db')
    case = store.case(demo_snapshot())
    config = {'provider': 'meta', 'model': 'muse-spark-1.3-contributor'}
    exp = plan_experiment(store, [case], [config])
    identifier = store.list_runs(exp)[0]['id']
    prepared = store.get_run(identifier)['prepared']
    assert prepared['config']['prices']['output_per_million'] == .2
    assert prepared['pricing_reference']['source'] == 'user_provided'
    changed = pricing.catalog()
    changed['models'][0]['prices']['output_per_million'] = .4
    path = tmp_path / 'prices.json'
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(pricing, 'CATALOG_PATH', path)
    assert prepare(case, config)['input_hash'] != prepared['input_hash']
    calls = []
    def mock(config, request):
        calls.append(config)
        return {'raw_response': dumps(read_json(KIT / 'page-output.demo.json')), 'finish_reason': 'stop',
                'usage': {'input_tokens': 9412, 'output_tokens': 8192, 'reasoning_tokens': 6824},
                'latency_seconds': .1, 'attempts': 1}
    execute_experiment(store, exp, resume=True, caller=mock)
    assert calls[0]['prices']['output_per_million'] == .2
    run = store.get_run(identifier)
    assert run['cost_estimate']['cost_usd'] == pytest.approx(.0025796)
    assert run['cost_estimate']['pricing'] == prepared['pricing_reference']
    data = report(store, exp)
    assert data['runs'][0]['cost_usd'] == pytest.approx(.0025796)
    assert data['runs'][0]['price_source'] == 'user_provided'
    assert 'stored_estimate / 2026-11-01' in markdown(data)
    assert 'price_catalog_sha256' in csv_report(data)
    exported = json.loads(next(export_jsonl(store, exp)))
    assert exported['run']['cost_estimate'] == run['cost_estimate']
    execute_experiment(store, exp, resume=True, caller=mock)
    assert len(calls) == 1


def test_legacy_database_read_and_export_never_rewrite_run(tmp_path):
    store = Store(tmp_path / 'lab.db')
    case = store.case(demo_snapshot())
    prepared = prepare(case, {'provider': 'meta', 'model': 'muse-spark-1.3-contributor', 'prices': {}})
    prepared.pop('pricing_reference')
    exp = store.save_plan('Legacy fixture', {'evaluation': False, 'size': 1}, [(case, prepared, 1)])
    identifier = store.list_runs(exp)[0]['id']
    store.start(identifier)
    store.finish(identifier, 'invalid', legacy_run()['result'])
    with store.connect() as con:
        before = tuple(con.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone())
    run = store.get_run(identifier)
    assert run['cost_estimate']['basis'] == 'catalog_reestimate'
    assert report(store, exp)['runs'][0]['cost_usd'] == pytest.approx(.0025796)
    assert json.loads(next(export_jsonl(store, exp)))['run']['result']['cost_usd'] is None
    with store.connect() as con:
        after = tuple(con.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone())
    assert before == after
