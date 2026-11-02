import io
import json
import urllib.error
from copy import deepcopy

import pytest

from tools.knowledge_graph.playground.common import KIT, dumps, read_json
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.providers import normalize_config, request_spec, send, estimate_cost, ProviderError
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot


def prompt():
    return render(demo_snapshot(), 'concepts-v1', 'concepts_relations')


@pytest.mark.parametrize('provider', ['ollama','openai','compatible','meta'])
def test_mocked_provider_round_trip(provider, monkeypatch):
    monkeypatch.setenv('TEST_KG_KEY','private-test-key')
    config=normalize_config({'provider':provider,'model':'test','api_key_env':'TEST_KG_KEY'})
    request=request_spec(config,prompt())
    expected=read_json(KIT/'page-output.demo.json')
    if provider=='ollama':
        response={'message':{'content':dumps(expected)},'prompt_eval_count':10,'eval_count':20,'done_reason':'stop'}
    else:
        response={'choices':[{'message':{'content':dumps(expected)},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':10,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':3}}}
    called=[]
    def transport(req,timeout):
        called.append(req)
        assert req.headers['Authorization']=='Bearer private-test-key'
        assert timeout==180
        return io.BytesIO(dumps(response).encode())
    result=send(config,request,transport=transport)
    assert json.loads(result['raw_response'])==expected
    assert result['usage']['input_tokens']==10
    assert result['attempts']==1 and len(called)==1
    assert 'private-test-key' not in dumps(request)


@pytest.mark.parametrize('provider', ['ollama','openai','compatible','meta'])
def test_images_and_schema_construction(provider):
    c=normalize_config({'provider':provider,'model':'test','vision':True,'structured_output':'schema'})
    r=request_spec(c,prompt(),{'base64':'abc','mime':'image/jpeg'})
    body=r['body']
    if provider=='ollama':
        assert isinstance(body['format'],dict)
        assert body['messages'][1]['images']==['abc']
        assert body['options']['num_predict']==32768
    else:
        assert body['response_format']['json_schema']['strict']
        assert body['messages'][1]['content'][1]['image_url']['url']=='data:image/jpeg;base64,abc'
    assert body['messages'][0]['role']==('developer' if provider=='meta' else 'system')


def test_ollama_cloud_has_explicit_prompt_only_format():
    c=normalize_config({'provider':'ollama','model':'gemma4:cloud'})
    assert c['structured_output']=='none'
    assert 'format' not in request_spec(c,prompt())['body']


@pytest.mark.parametrize('settings', [
    {'provider':'openai','temperature':.5}, {'provider':'compatible','reasoning_effort':'high'},
    {'provider':'meta','reasoning_effort':'none'}, {'provider':'meta','model':'muse-spark-1.2','reasoning_effort':'max'},
    {'provider':'openai','num_ctx':8192}, {'provider':'ollama','retries':100},
    {'base_url':'https://secret@example.org/v1'}, {'base_url':'https://example.org/v1?key=secret'},
    {'api_key':'secret'}, {'headers':{'Authorization':'secret'}}, {'max_output_tokens':0},
    {'prices':{'input_per_million':float('nan')}}, {'vision':'false'}])
def test_unsupported_and_sensitive_settings_fail_before_request(settings):
    with pytest.raises(ValueError): normalize_config(dict({'provider':'ollama','model':'test'},**settings))


def test_json_fallback_is_explicit_not_hidden_retry():
    c=normalize_config({'provider':'compatible','model':'test','structured_output':'json'})
    assert request_spec(c,prompt())['body']['response_format']=={'type':'json_object'}
    assert 'reasoning_effort' not in request_spec(c,prompt())['body']


def test_retry_transport_bounded_and_does_not_retry_invalid_json(monkeypatch):
    c=normalize_config({'provider':'ollama','model':'test','retries':2})
    calls=[]
    def transport(req,timeout):
        calls.append(1)
        if len(calls)<3:raise urllib.error.HTTPError(req.full_url,503,'unavailable',{},io.BytesIO(b'wait'))
        return io.BytesIO(b'{"message":{"content":"NOT JSON"}}')
    result=send(c,request_spec(c,prompt()),transport=transport,sleeper=lambda _:None)
    assert result['attempts']==3 and result['raw_response']=='NOT JSON'
    calls.clear()
    def bad(req,timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url,400,'bad',{},io.BytesIO(b'unsupported'))
    with pytest.raises(ProviderError):send(c,request_spec(c,prompt()),transport=bad,sleeper=lambda _:None)
    assert len(calls)==1


def test_provider_errors_redact_key(monkeypatch):
    monkeypatch.setenv('KG_KEY','SECRET-VALUE')
    c=normalize_config({'provider':'meta','model':'test','api_key_env':'KG_KEY'})
    def transport(req,timeout):
        raise urllib.error.HTTPError(req.full_url,401,'bad',{},io.BytesIO(b'SECRET-VALUE'))
    with pytest.raises(ProviderError) as error: send(c,request_spec(c,prompt()),transport=transport)
    assert 'SECRET-VALUE' not in error.value.response


def test_meta_transport_schema_preserves_local_constraints_and_schema_data():
    from jsonschema import Draft202012Validator
    from tools.knowledge_graph.playground.providers import meta_transport_schema
    schema = {'type': 'object', 'properties': {
        'uniqueItems': {'type': 'string', 'enum': ['uniqueItems']},
        'refs': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'minItems': 1}},
        '$defs': {'a/b': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True}},
        'required': ['uniqueItems', 'refs'], 'additionalProperties': False}
    before = deepcopy(schema)
    projected, removed = meta_transport_schema(schema)
    assert schema == before
    assert projected['properties']['uniqueItems'] == before['properties']['uniqueItems']
    assert projected['properties']['refs']['minItems'] == 1
    assert projected['$defs']['a/b'] == {'type': 'array', 'items': {'type': 'string'}}
    assert {r['path'] for r in removed} == {'/properties/refs/uniqueItems', '/$defs/a~1b/uniqueItems'}
    repeated = {'uniqueItems': 'uniqueItems', 'refs': ['ev1', 'ev1']}
    assert Draft202012Validator(projected).is_valid(repeated)
    assert not Draft202012Validator(schema).is_valid(repeated)


def test_meta_review_request_projects_unique_items_only():
    from tools.knowledge_graph.playground.verification_contract import review_schema
    p = render(demo_snapshot(), 'concepts-v2.2-confidence', 'concepts_relations')
    original = request_spec(normalize_config({'provider': 'meta', 'model': 'test'}), p)
    assert 'schema_projection' not in original
    assert original['body']['response_format']['json_schema']['schema'] == p['schema']
    p['schema'] = review_schema(p['schema'])
    before = deepcopy(p)
    r = request_spec(normalize_config({'provider': 'meta', 'model': 'test'}), p)
    wire = deepcopy(r['body']['response_format']['json_schema']['schema'])
    assert 'uniqueItems' not in dumps(wire)
    for group in ('claim_decisions', 'relation_decisions'):
        wire['properties'][group]['items']['properties']['evidence_ids']['uniqueItems'] = True
    assert wire == p['schema'] == before['schema']
    assert p == before
    assert len(r['schema_projection']['removed_constraints']) == 2
    assert r['schema_projection']['local_validation_preserved']
    ollama = request_spec(normalize_config({'provider': 'ollama', 'model': 'test'}), p)
    assert ollama['body']['format'] == p['schema']


def test_schema_http_400_exposes_redacted_detail_without_retry(monkeypatch):
    monkeypatch.setenv('KG_KEY', 'SECRET-VALUE')
    c = normalize_config({'provider': 'meta', 'model': 'test', 'api_key_env': 'KG_KEY', 'retries': 4})
    calls = []
    body = dumps({'error': {'message': 'JSON schema keyword `uniqueItems` is not supported SECRET-VALUE'}})
    def transport(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 400, 'bad schema', {}, io.BytesIO(body.encode()))
    with pytest.raises(ProviderError, match='uniqueItems') as error:
        send(c, request_spec(c, prompt()), transport=transport, sleeper=lambda _: pytest.fail('No retry'))
    assert len(calls) == error.value.attempts == 1
    assert 'SECRET-VALUE' not in str(error.value) + error.value.response
    assert '[REDACTED]' in str(error.value)


def test_cost_requires_rates_and_usage():
    assert estimate_cost({}, {}) is None
    assert estimate_cost({'input_tokens':100,'output_tokens':20}, {}) is None
    value=estimate_cost({'input_tokens':100,'output_tokens':20,'cached_input_tokens':50},
                         {'input_per_million':2,'output_per_million':4,'cached_input_per_million':1})
    assert value==pytest.approx(.00023)


def test_malformed_envelope_is_preserved_without_retry():
    config=normalize_config({'provider':'compatible','model':'test'})
    response='{"choices":[{"message":{"content":"{}"}}],"usage":3}'
    with pytest.raises(ProviderError) as error:
        send(config,request_spec(config,prompt()),transport=lambda *args,**kwargs:io.BytesIO(response.encode()))
    assert error.value.response==response


def test_invalid_token_counts_become_unknown():
    config=normalize_config({'provider':'ollama','model':'test'})
    response=b'{"message":{"content":"{}"},"prompt_eval_count":"bad","eval_count":-1}'
    result=send(config,request_spec(config,prompt()),transport=lambda *args,**kwargs:io.BytesIO(response))
    assert result['usage']['input_tokens'] is None and result['usage']['output_tokens'] is None


@pytest.mark.parametrize('provider', ['ollama','openai','compatible','meta'])
def test_larger_default_budget_and_explicit_saved_budgets(provider):
    config=normalize_config({'provider':provider,'model':'test'})
    assert config['max_output_tokens']==32768
    body=request_spec(config,prompt())['body']
    assert (body['options']['num_predict'] if provider=='ollama' else body[config['token_parameter']])==32768
    saved=normalize_config(dict(config,max_output_tokens=8192))
    assert saved['max_output_tokens']==8192
    assert normalize_config(saved)==saved


def test_meta_reasoning_tokens_are_subset_of_completion_budget(monkeypatch):
    monkeypatch.setenv('KG_META_TEST','test-key')
    config=normalize_config({'provider':'meta','model':'muse-spark-1.3-contributor','api_key_env':'KG_META_TEST'})
    envelope={'choices':[{'message':{'content':'{"evidence":[{"quote":"cut off'},'finish_reason':'length'}],
              'usage':{'prompt_tokens':9412,'completion_tokens':8192,'completion_tokens_details':{'reasoning_tokens':6824}}}
    result=send(config,request_spec(config,prompt()),transport=lambda *a,**kw:io.BytesIO(dumps(envelope).encode()))
    assert result['usage']['output_tokens']==8192 and result['usage']['reasoning_tokens']==6824
    assert result['finish_reason']=='length'
    assert estimate_cost(result['usage'],{'input_per_million':0,'output_per_million':1})==pytest.approx(8192/1_000_000)
