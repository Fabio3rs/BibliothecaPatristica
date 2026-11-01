"""Model text preparation preserves XML source and block provenance."""
from copy import deepcopy
import json

import pytest

from tools.knowledge_graph.playground.common import KIT, digest, dumps, read_json
from tools.knowledge_graph.playground.ocr_adapter import parse_blocks, prepare_model_ocr
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import prepare, plan_experiment, execute_experiment


def test_preparation_changes_only_text_inside_existing_blocks():
    raw = ('<pagina numero="71"><cabecalho>ORA-\nTIO</cabecalho>'
           '<bloco tipo="texto_principal" script="grego" bbox="10,20,300,400" extra="keep">'
           'λό-\nγος <i>inter-\npretatio</i> et gratia\nκαὶ οὐσία</bloco>'
           '<notas><nota>edi-\ntoris &amp; aliud</nota></notas>'
           '<bloco tipo="unknown">anti-ariano 12-14\n&lt;termo&gt;</bloco></pagina>')
    original = parse_blocks(raw)
    before = deepcopy(original)
    prepared, audit = prepare_model_ocr(original)
    assert original == before
    assert [b['text'] for b in prepared['blocks']] == [
        'ORATIO', 'λόγος interpretatio et gratia\nκαὶ οὐσία',
        'editoris & aliud', 'anti-ariano 12-14\n<termo>']
    assert audit['changed_blocks'] == 3
    for source, model, entry in zip(original['blocks'], prepared['blocks'], audit['blocks']):
        assert {k: v for k, v in source.items() if k != 'text'} == {k: v for k, v in model.items() if k != 'text'}
        assert entry['source_text_sha256'] == digest(source['text'])
        assert entry['model_text_sha256'] == digest(model['text'])
    assert {k: v for k, v in original.items() if k != 'blocks'} == {k: v for k, v in prepared.items() if k != 'blocks'}


@pytest.mark.parametrize('raw,expected', [
    ('<pagina><bloco>inter‐\npretatio</bloco></pagina>', ['interpretatio']),
    ('<pagina><bloco>inter-\r\n  pretatio\r\naliud</bloco></pagina>', ['interpretatio\naliud']),
    ('<pagina><bloco>inter-\n</bloco><bloco>pretatio</bloco></pagina>', ['inter-\n', 'pretatio']),
    ('<pagina><bloco>inter-\npretatio & aliud</bloco><bloco>incom-\npletum', ['interpretatio & aliud', 'incompletum']),
    ('inter-\npretatio\nλόγος', ['interpretatio\nλόγος']),
    ('<pagina><bloco>anti-ariano\n12-14\nligne - suite</bloco></pagina>', ['anti-ariano\n12-14\nligne - suite']),
    ('<pagina><bloco>natura</bloco></pagina>', ['natura']),
])
def test_linebreak_cleaning_handles_legacy_and_malformed_ocr_without_crossing_blocks(raw, expected):
    source = parse_blocks(raw)
    before = deepcopy(source)
    prepared, _ = prepare_model_ocr(source)
    assert [b['text'] for b in prepared['blocks']] == expected
    assert source == before
    assert prepared['warnings'] == source['warnings']


@pytest.mark.parametrize('provider', ['ollama', 'openai', 'compatible', 'meta'])
def test_all_provider_requests_use_prepared_block_text_and_keep_original_snapshot(tmp_path, provider):
    store = Store(tmp_path/'playground.db')
    snap = demo_snapshot()
    raw = '<pagina><bloco script="latino">inter-\npretatio</bloco><notas>Nota.</notas></pagina>'
    snap.update(ocr=parse_blocks(raw), ocr_raw=raw)
    source = deepcopy(snap)
    case = store.case(snap)
    prepared = prepare(case, {'provider': provider, 'model': 'test'})
    payload = json.loads(prepared['request']['body']['messages'][1]['content'])
    assert payload['ocr']['blocks'][0]['text'] == 'interpretatio'
    assert payload['v2'] == snap['v2'] and payload['source'] == snap['source']
    assert len(payload['ocr']['blocks']) == 2
    assert prepared['ocr_preparation']['changed_blocks'] == 1
    assert snap == source and store.get_case(case['id'])['snapshot'] == source


def test_preparation_version_participates_in_cache_key(tmp_path, monkeypatch):
    import tools.knowledge_graph.playground.ocr_adapter as adapter
    case = Store(tmp_path/'playground.db').case(demo_snapshot())
    first = prepare(case, {'provider': 'fixture', 'model': 'demo'})
    monkeypatch.setattr(adapter, 'MODEL_TEXT_VERSION', 'test-new-version')
    second = prepare(case, {'provider': 'fixture', 'model': 'demo'})
    assert first['request'] == second['request']
    assert first['input_hash'] != second['input_hash']


def test_frozen_plan_sends_clean_text_and_locates_response_in_original_source(tmp_path, monkeypatch):
    import tools.knowledge_graph.playground.prompt_registry as registry
    store = Store(tmp_path/'playground.db')
    snap = demo_snapshot()
    original = snap['ocr']['blocks'][0]['text'].replace('Athanasius', 'Atha-\nnasius')
    snap['ocr']['blocks'][0]['text'] = original
    case = store.case(snap)
    exp = plan_experiment(store, [case], [{'provider': 'ollama', 'model': 'test'}])
    identifier = store.list_runs(exp)[0]['id']
    frozen = store.get_run(identifier)['prepared']
    assert frozen['ocr_preparation']['changed_blocks'] == 1
    def should_not_prepare_again(*args, **kwargs):
        raise AssertionError('Resuming a saved plan must use its frozen request')
    monkeypatch.setattr(registry, 'prepare_model_ocr', should_not_prepare_again)
    calls = []
    def fake_provider(config, request):
        calls.append(request)
        assert 'Athanasius' in json.loads(request['body']['messages'][1]['content'])['ocr']['blocks'][0]['text']
        return {'raw_response': dumps(read_json(KIT/'page-output.demo.json')),
                'usage': {}, 'latency_seconds': 0, 'finish_reason': 'stop'}
    execute_experiment(store, exp, caller=fake_provider, resume=True)
    run = store.get_run(identifier)
    assert len(calls) == 1 and run['prepared'] == frozen
    assert run['validation']['valid'] and run['validation']['evidence_counts']['normalized'] == 1
    evidence = run['validation']['evidence_matches'][-1]
    assert evidence['matched_quote'] == original[evidence['start']:evidence['end']]
    assert 'Atha-\nnasius' in evidence['matched_quote']
    assert store.get_case(case['id'])['snapshot']['ocr']['blocks'][0]['text'] == original
