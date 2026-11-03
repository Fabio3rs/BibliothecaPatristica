"""Synthetic Unicode transport regressions; all network calls are mocked."""
import base64
import io
import json

import pytest

from tools.knowledge_graph.playground.common import digest, dumps
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment
from tools.knowledge_graph.playground.http_capture import capture_body
from tools.knowledge_graph.playground.ordered_requests import request_spec, send, wire_text
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.providers import normalize_config, ProviderError
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store

VARIANT = 'navigation-v1.1-unicode-topics-arguments'
UNICODE = 'æ œ ã Ἀθανάσιος κύριος α\u0313'


def setup(monkeypatch):
    monkeypatch.setenv('KG_UNICODE_TEST_KEY', 'test-secret-unicode')
    config = normalize_config({'provider': 'meta', 'model': 'test',
                               'api_key_env': 'KG_UNICODE_TEST_KEY'})
    prompt = render(demo_snapshot(), VARIANT, 'concepts_relations')
    return config, request_spec(config, prompt)


def envelope(content):
    return dumps({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}).encode('utf-8')


def test_variant_changes_only_system_prompt():
    snapshot = demo_snapshot()
    old = render(snapshot, 'navigation-v1-topics-arguments', 'concepts_relations')
    new = render(snapshot, VARIANT, 'concepts_relations')
    for field in ('schema', 'schema_hash', 'user_prompt', 'user_hash', 'input_preparation'):
        assert new[field] == old[field]
    assert new['system_hash'] != old['system_hash']
    assert 'UNICODE OUTPUT' not in old['system_prompt']
    assert new['system_prompt'].count('UNICODE OUTPUT') == 1
    assert new['system_prompt'].count('EXPERIMENT MODE') == 1
    assert new['variant']['input_policy'] == 'navigation-no-hints-v1'


@pytest.mark.parametrize('content', [dumps({'quote': UNICODE}), json.dumps({'quote': UNICODE}),
                                    r'{"quote":"\u0000e6"}'])
def test_literal_and_escaped_content_preserved_without_repairs(monkeypatch, content):
    config, request = setup(monkeypatch)
    body = envelope(content)
    calls = []

    def transport(req, timeout):
        calls.append(req)
        assert req.data == wire_text(request).encode('utf-8')
        assert 'æ'.encode('utf-8') in req.data
        assert req.get_header('Content-type') == 'application/json'
        assert req.get_header('Accept') == 'application/json'
        assert req.get_header('Accept-charset') == 'utf-8'
        return io.BytesIO(body)

    result = send(config, request, transport=transport)
    assert len(calls) == 1
    assert result['raw_response'] == content
    capture = result['http_response']
    assert capture['utf8_valid'] and capture['complete']
    assert base64.b64decode(capture['body_base64']) == body
    assert capture['sha256'] == digest(body)
    if '0000e6' in content:
        assert json.loads(result['raw_response'])['quote'] == '\x00e6'
    else:
        assert json.loads(result['raw_response'])['quote'] == UNICODE


@pytest.mark.parametrize('body', [b'\xffbad', b'{invalid json'])
def test_invalid_transport_retains_bytes_without_retry(monkeypatch, body):
    config, request = setup(monkeypatch)
    calls = []

    def transport(req, timeout):
        calls.append(1)
        return io.BytesIO(body)

    with pytest.raises(ProviderError) as error:
        send(config, request, transport=transport)
    assert len(calls) == 1
    assert base64.b64decode(error.value.http_response['body_base64']) == body
    assert error.value.http_response['utf8_valid'] == (body[:1] != b'\xff')


def test_response_byte_limit_applies_before_decoding(monkeypatch):
    from tools.knowledge_graph.playground import providers
    config, request = setup(monkeypatch)
    monkeypatch.setattr(providers, 'MAX_BODY_BYTES', 8)
    with pytest.raises(ProviderError, match='byte limit') as error:
        send(config, request, transport=lambda *a, **kw: io.BytesIO('α'.encode() * 8))
    assert error.value.http_response['byte_count'] == 9
    assert not error.value.http_response['complete']
    assert 'body_base64' not in error.value.http_response


@pytest.mark.parametrize('escape', [False, True])
def test_capture_does_not_archive_reflected_secret(monkeypatch, escape):
    config, _ = setup(monkeypatch)
    secret = 'test-secret-unicode'
    text = ''.join('\\u%04x' % ord(c) for c in secret) if escape else secret
    capture = capture_body(text.encode(), config)
    assert capture['status'] == 'omitted_sensitive_reflection'
    assert 'body_base64' not in capture


@pytest.mark.parametrize('body', [envelope(dumps({'schema_version': 'kg-navigation-v1',
    'concepts': [], 'arguments': [], 'evidence': [], 'warnings': []})), b'\xffbad'])
def test_body_capture_persists_through_runner(tmp_path, monkeypatch, body):
    config, _ = setup(monkeypatch)
    store = Store(tmp_path / 'unicode.db')
    experiment = plan_experiment(store, [store.case(demo_snapshot())], [config],
                                 variants=[VARIANT], bypass_cache=True)

    def caller(config, request):
        return send(config, request, transport=lambda *a, **kw: io.BytesIO(body))

    execute_experiment(store, experiment, caller=caller)
    run = store.get_run(store.list_runs(experiment)[0]['id'])
    assert run['status'] == ('error' if body[:1] == b'\xff' else 'completed')
    assert base64.b64decode(run['result']['http_response']['body_base64']) == body
