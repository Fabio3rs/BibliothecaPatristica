"""Explicit Flex profiles; mocked transports only, never a live API."""
import io
import json
import urllib.error
from copy import deepcopy

import pytest

from scripts.playgrounds.kg_extractor_playground import parser, provider_config
from tools.knowledge_graph.playground.common import dumps
from tools.knowledge_graph.playground.providers import normalize_config, send, request_spec
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.experiment_runner import prepare
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.validation_current import evaluate_response


def test_flex_cli_and_omission_preserve_existing_profiles():
    args=parser().parse_args(['run','--model','gpt-6-luna','--provider','openai','--service-tier','flex','--dry-run'])
    assert provider_config(args)['service_tier']=='flex'
    for provider in ('openai','meta','ollama'):
        config=normalize_config({'provider':provider,'model':'test'})
        assert 'service_tier' not in config
        assert 'service_tier' not in request_spec(config,render(demo_snapshot(),'concepts-v1','concepts_relations'))['body']
    args=parser().parse_args(['corpus-create','--model','test','--provider','openai','--service-tier','flex'])
    assert provider_config(args)['service_tier']=='flex'


@pytest.mark.parametrize('settings',[{'provider':'meta','service_tier':'flex'}, {'provider':'compatible','service_tier':'flex'},
                                    {'provider':'openai','service_tier':'bogus'}, {'provider':'openai','service_tier':{} }])
def test_invalid_tiers_rejected(settings):
    with pytest.raises(ValueError,match='service_tier'):
        normalize_config(dict(model='test',**settings))


def test_flex_survives_retry_without_default_fallback(monkeypatch):
    monkeypatch.setenv('KG_TEST_KEY','synthetic-secret')
    config=normalize_config({'provider':'openai','model':'gpt-6-luna','service_tier':'flex','timeout':900,
                             'api_key_env':'KG_TEST_KEY','retries':1})
    request=request_spec(config,render(demo_snapshot(),'navigation-v1-topics-arguments','concepts_relations'))
    calls=[]
    def transport(req,timeout):
        assert timeout==900
        calls.append(json.loads(req.data))
        if len(calls)==1:
            raise urllib.error.HTTPError(req.full_url,429,'Resource unavailable',{},io.BytesIO(b'{"error":{"message":"Resource unavailable"}}'))
        return io.BytesIO(b'{"service_tier":"flex","choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}')
    result=send(config,request,transport=transport,sleeper=lambda _:None,uniform=lambda a,b:0)
    assert len(calls)==2 and all(c['service_tier']=='flex' for c in calls)
    assert json.loads(result['provider_response'])['service_tier']=='flex'
    assert 'synthetic-secret' not in dumps(request)


def test_service_tier_invalidates_cache_and_preserves_local_schema(tmp_path):
    store=Store(tmp_path/'demo.db');case=store.case(demo_snapshot())
    config={'provider':'openai','model':'gpt-5.4-mini'}
    baseline=prepare(case,config,variant='navigation-v1-topics-arguments')
    flex=prepare(case,dict(config,service_tier='flex'),variant='navigation-v1-topics-arguments')
    assert baseline['input_hash']!=flex['input_hash']
    assert baseline['schema_hash']==flex['schema_hash'] and baseline['system_hash']==flex['system_hash']
    body=deepcopy(flex['request']['body']);assert body.pop('service_tier')=='flex'
    assert body==baseline['request']['body']
    wire_schema=body['response_format']['json_schema']['schema']
    assert 'uniqueItems' not in dumps(wire_schema) and 'minLength' not in dumps(wire_schema)
    assert flex['schema']['properties']['arguments']['items']['properties']['concept_ids']['uniqueItems']
    data={'schema_version':'kg-navigation-v1','concepts':[], 'arguments':[], 'evidence':[], 'warnings':[]}
    assert evaluate_response(case['snapshot'],{'raw_response':dumps(data)},flex)['valid']
    data['concepts']=[{'id':'','label':'x','meaning':'x','salience':'primary','evidence_ids':['ev1','ev1']}]
    assert not evaluate_response(case['snapshot'],{'raw_response':dumps(data)},flex)['structural_valid']
