"""No paid calls: frozen v3 review, semantic regression fixtures and mock resume."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest
from PIL import Image

from tools.knowledge_graph.playground.common import ROOT, digest, dumps, readonly
from tools.knowledge_graph.playground.experiment_runner import prepare, execute_run, execute_experiment
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.statement_review_contract import VERSION, evaluate_review
from tools.knowledge_graph.playground.statement_review_study import (
    prepare_statement_review, make_prepared, check_schedule, policy,
)
from tools.knowledge_graph.playground.statement_review_reporting import write_report, neutral_output, units
from tools.knowledge_graph.playground.validation_dispatch import evaluate_response
from tools.knowledge_graph.playground.verification_runner import execute_verification


def extraction():
    return json.loads((ROOT/'tools/knowledge_graph/playground/fixtures/page-output.statements.demo.json').read_text())


def keep(data):
    return {'schema_version': VERSION, 'new_evidence': [], 'warnings': [],
            **{key: [{field: obj['id'], 'action': 'keep', 'replacements': [],
                      'reason': 'Conteúdo sustentado pela fonte sintética.', 'evidence_ids': obj['evidence_ids']}
                     for obj in data[group]]
               for group, key, field in (('claims', 'claim_decisions', 'claim_id'),
                                          ('relations', 'relation_decisions', 'relation_id'))}}


def fake(config, request):
    assert config['reasoning_effort'] == 'medium'
    assert request['body']['reasoning_effort'] == 'medium'
    assert 'uniqueItems' not in dumps(request['body']['response_format']['json_schema']['schema'])
    content = request['body']['messages'][1]['content']
    payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
    assert 'model_confidence' not in dumps(payload['candidate_extraction'])
    return {'raw_response': dumps(keep(payload['candidate_extraction'])),
            'usage': {'input_tokens': 120, 'output_tokens': 80, 'reasoning_tokens': 40},
            'latency_seconds': .01, 'finish_reason': 'stop', 'attempts': 1, 'provider_response': '{}'}


def frozen_state(store, exp):
    with readonly(store.path) as c:
        return digest({
            'experiment': dict(c.execute('SELECT * FROM experiments WHERE id=?', (exp,)).fetchone()),
            'runs': [dict(r) for r in c.execute('SELECT * FROM runs WHERE experiment_id=? ORDER BY id', (exp,))],
            'validations': [dict(r) for r in c.execute('SELECT v.* FROM run_validation v JOIN runs r ON r.id=v.run_id WHERE r.experiment_id=? ORDER BY v.id', (exp,))],
            'cases': [dict(r) for r in c.execute('SELECT DISTINCT c.* FROM cases c JOIN runs r ON r.case_id=c.id WHERE r.experiment_id=? ORDER BY c.id', (exp,))]})


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path/'playground.db')
    exp = store.experiment('Synthetic frozen v3', {})
    image = tmp_path/'fixture.png'
    Image.new('RGB', (24, 32), 'white').save(image)
    snap = demo_snapshot()
    snap['ocr_raw'] = '\n'.join(b['text'] for b in snap['ocr']['blocks'])
    snap['source']['ocr_sha256'] = digest(snap['ocr_raw'])
    snap['facsimile'] = {'available': True, 'path': str(image), 'sha256': digest(image.read_bytes())}
    case = store.case(snap)
    p = prepare(case, {'provider': 'meta', 'model': 'muse-spark-1.3-contributor', 'reasoning_effort': 'medium'},
                variant='concepts-v3-statements-confidence', facsimile='on')
    ids = []
    for rep in range(1, 5):
        identifier = store.add_run(exp, case, p, rep)
        result = {'raw_response': dumps(extraction()), 'parsed_json': extraction(),
                  'usage': {}, 'cost_usd': .005, 'finish_reason': 'stop'}
        checked = evaluate_response(case['snapshot'], result, p)
        assert checked['valid']
        store.start(identifier)
        store.finish(identifier, 'completed', result, checked)
        ids.append(identifier)
    return store, exp, ids, tmp_path


def build(source, **kwargs):
    store, _, ids, path = source
    return prepare_statement_review(store, source_runs=ids, output=path/'preflight.json',
                                    benchmark_dir=path/'absent', **kwargs)


def test_prepare_zero_inference_frozen_input_schema_image_and_repetitions(source, monkeypatch):
    store, exp, ids, path = source
    before = frozen_state(store, exp)
    monkeypatch.setattr('tools.knowledge_graph.playground.providers.send', lambda *_: pytest.fail('No inference'))
    pf = build(source)
    assert pf['size'] == 8 and pf['inference_calls'] == 0
    assert pf['estimated_calls_usd'] == pytest.approx(.04)
    assert pf['checks']['reasoning_efforts'] == ['medium']
    assert frozen_state(store, exp) == before
    assert policy() == store.experiment_plan(pf['experiment'])['policy']
    with readonly(store.path) as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    for row in store.list_runs(pf['experiment']):
        p = store.get_run(row['id'])['prepared']
        original = store.get_run(p['source_run_id'])['prepared']
        assert p['config'] == original['config']
        for key in ('source', 'v2', 'ocr', 'facsimile'):
            assert p['user_payload'][key] == original['user_payload'][key]
        assert p['request']['body']['messages'][1]['content'][1] == original['request']['body']['messages'][1]['content'][1]
        assert p['extraction_schema'] == original['schema']
        assert p['schema']['properties']['claim_decisions']['items']['properties']['replacements']['items'] == original['schema']['properties']['claims']['items']
        assert 'Helena' not in p['system_prompt'] and 'Paulo' not in p['system_prompt']
        assert p['system_prompt'].count('CLAIM CONTRACT') == 1
        assert 'SYNTHETIC ROLE EXAMPLE' not in p['system_prompt']
        assert 'model_confidence' not in dumps(p['user_payload']['candidate_extraction'])
        assert 'warnings' not in p['user_payload']['candidate_extraction']
    by_source = {}
    for t in pf['schedule']:
        by_source.setdefault(t['source_run_id'], []).append(t)
    assert set(by_source) == set(ids)
    for items in by_source.values():
        assert {t['repetition'] for t in items} == {1, 2}
        assert len({t['request_hash'] for t in items}) == 1
    with pytest.raises(ValueError, match='already exists'):
        build(source)
    with pytest.raises(ValueError, match='verify-run'):
        execute_experiment(store, pf['experiment'])
    with pytest.raises(ValueError, match='verify-run'):
        execute_run(store, store.list_runs(pf['experiment'])[0]['id'])


@pytest.mark.parametrize('mutation', ['source', 'request', 'config', 'image', 'schema', 'case'])
def test_frozen_drift_fails_before_provider(source, mutation):
    store, _, _, _ = source
    exp = build(source)['experiment']
    run = store.get_run(store.list_runs(exp)[0]['id'])
    p = run['prepared']
    if mutation == 'image':
        Path(store.get_case(run['case_id'])['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
    elif mutation == 'source':
        with store.connect() as c:
            c.execute('UPDATE runs SET result_json=? WHERE id=?', (dumps({'raw_response': 'changed'}), p['source_run_id']))
    elif mutation == 'case':
        with store.connect() as c:
            c.execute("UPDATE cases SET snapshot_hash='changed' WHERE id=?", (run['case_id'],))
    else:
        if mutation == 'config':
            p['config']['reasoning_effort'] = 'low'
        if mutation == 'request':
            p['request']['body']['max_completion_tokens'] = 4
        if mutation == 'schema':
            p['extraction_schema']['properties']['claims']['items']['required'] = []
        with store.connect() as c:
            c.execute('UPDATE runs SET prepared_json=? WHERE id=?', (dumps(p), run['id']))
    with pytest.raises(ValueError):
        execute_verification(store, exp, caller=lambda *_: pytest.fail('No provider call'))
    assert all(r['status'] == 'planned' for r in store.list_runs(exp))


def test_schedule_seed_reproducibility_and_damage(source):
    pf = build(source)
    check_schedule(pf)
    for key, value in [('size', 7), ('repetitions', 1), ('seed', 1), ('bypass_cache', False)]:
        with pytest.raises(ValueError):
            check_schedule(dict(pf, **{key: value}))
    p = deepcopy(pf)
    p['schedule'][0]['repetition'] = 5
    with pytest.raises(ValueError):
        check_schedule(p)


def test_mock_resume_bypass_reporting_and_blinding(source, monkeypatch):
    store, exp, _, path = source
    before = frozen_state(store, exp)
    review = build(source)['experiment']
    calls = []
    monkeypatch.setattr(store, 'cached', lambda *_: pytest.fail('Must bypass cache'))
    def call(config, request):
        calls.append(digest(request))
        return fake(config, request)
    stop = threading.Event()
    stop.set()
    assert execute_verification(store, review, caller=call, stop_event=stop)['submitted'] == 0
    assert execute_verification(store, review, caller=call, max_calls=1)['submitted'] == 1
    assert execute_verification(store, review, caller=call, concurrency=20)['submitted'] == 7
    assert execute_verification(store, review, caller=call)['submitted'] == 0
    assert len(calls) == 8
    for row in store.list_runs(review):
        run = store.get_run(row['id'])
        assert run['status'] == 'completed'
        assert run['result']['derived_extraction'] == run['prepared']['baseline_extraction']
        assert run['validation']['derived_validation']['graph_projection']['relations']
        assert run['validation']['gold_status'] == 'not_gold'
    report_before = frozen_state(store, review)
    result = write_report(store.path, review, path/'report.md')
    assert result['blind_pairs_available'] == 8 and result['adjudicated_units'] == 0
    packets = [json.loads(line) for line in (path/'blind/packets.jsonl').read_text().splitlines()]
    assert len({p['pair_id'] for p in packets}) == 8
    for packet in packets:
        assert not set(packet) & {'source_run_id', 'review_run_id', 'arm'}
        assert 'model_confidence' not in dumps(packet['outputs'])
        for side in packet['outputs'].values():
            assert all(o['id'].startswith(g + '_') for g in ('claims', 'entities', 'evidence') for o in side[g])
            assert any(kind == 'attributions' for _, kind in units(side))
    assert frozen_state(store, review) == report_before
    assert frozen_state(store, exp) == before
    data = json.loads((path/'report.json').read_text())
    assert len(data['stability']) == 4
    assert all(x['comparisons'][0]['identical_claim_content'] for x in data['stability'])


def test_interrupted_repetition_retry_keeps_original_attempt(source):
    store, _, _, path = source
    exp = build(source)['experiment']
    identifier = store.list_runs(exp)[0]['id']
    store.start(identifier)
    assert execute_verification(store, exp, caller=fake)['submitted'] == 7
    assert store.get_run(identifier)['status'] == 'interrupted'
    assert execute_verification(store, exp, caller=fake)['submitted'] == 0
    assert execute_verification(store, exp, caller=fake, retry_interrupted=True)['submitted'] == 1
    assert execute_verification(store, exp, caller=fake, retry_interrupted=True)['submitted'] == 0
    assert len(store.list_runs(exp)) == 9
    result = write_report(store.path, exp, path/'report.md')
    assert result['states'] == {'completed': 8}
    data = json.loads((path/'report.json').read_text())
    assert data['unknown_cost_attempts'] == [identifier]


def test_bad_response_preserves_raw_and_no_implicit_retry(source):
    store, _, _, path = source
    exp = build(source)['experiment']
    def broken(config, request):
        return dict(fake(config, request), raw_response='{"claim_decisions":', finish_reason='length')
    execute_verification(store, exp, caller=broken, max_calls=1)
    run = next(store.get_run(r['id']) for r in store.list_runs(exp) if r['status'] == 'invalid')
    assert run['result']['raw_response'] == '{"claim_decisions":'
    assert run['validation']['completion']['output_limit_reached']
    assert run['validation']['derived_extraction'] is None
    assert execute_verification(store, exp, caller=fake)['submitted'] == 7
    assert write_report(store.path, exp, path/'report.md')['blind_pairs_available'] == 7


def test_schema_ref_integrity_and_evidence_rejection(source):
    store, _, ids, _ = source
    original = store.get_run(ids[0])
    p = make_prepared(original['prepared'], extraction(), ids[0])
    snap = store.get_case(original['case_id'])['snapshot']
    data = keep(extraction())
    replacement = deepcopy(extraction()['claims'][0])
    replacement['id'] = 'pV1'
    data['claim_decisions'][0].update(action='replace', replacements=[replacement])
    for field, change in [('entity_id', 'ghost'), ('evidence_ids', ['ghost'])]:
        bad = deepcopy(data)
        bad['claim_decisions'][0]['replacements'][0]['attributions'][0][field] = change
        checked = evaluate_review(snap, {'raw_response': dumps(bad)}, p)
        assert checked['schema_valid'] and not checked['structural_valid']
    bad = deepcopy(data)
    bad['new_evidence'] = [{'id': 'evV1', 'source': 'ocr_block', 'locator': 'b1', 'quote': 'Invented and entirely unrelated passage'}]
    checked = evaluate_review(snap, {'raw_response': dumps(bad)}, p)
    assert checked['structural_valid'] and not checked['source_valid']
    assert checked['derived_extraction'] is not None
    assert not evaluate_review(snap, {'raw_response': dumps(dict(data, entities=[]))}, p)['schema_valid']
    missing = deepcopy(data)
    missing['claim_decisions'].pop()
    assert not evaluate_review(snap, {'raw_response': dumps(missing)}, p)['structural_valid']


def test_source_grounded_synthetic_split_quote_narrator_condition(source):
    store, _, ids, _ = source
    original = store.get_run(ids[0])
    snap = store.get_case(original['case_id'])['snapshot']
    text = 'Helena escreve: Se voltarem a objetar, as palavras de Lúcio os responderão: «A luz ilumina». Lúcio não formulou essa hipótese.'
    snap['ocr']['blocks'].append({'id': 'b9', 'type': 'texto_principal', 'script': 'latino', 'bbox': None, 'text': text})
    base = extraction()
    base['concepts'], base['relations'] = [], []
    base['entities'][0].update(label='Helena', evidence_ids=['ev9'])
    base['entities'][1].update(label='Lúcio', type='PERSON', evidence_ids=['ev9'])
    claim = deepcopy(base['claims'][1])
    claim.update(id='p1', proposition='Lúcio responderá aos adversários: a luz ilumina.', mode='reported', evidence_ids=['ev9'])
    claim['attributions'] = [{'entity_id': 'e2', 'stance': 'AFFIRMS', 'scope': 'reported', 'evidence_ids': ['ev9']}]
    claim['participants'] = [{'entity_id': 'e2', 'role': 'agent', 'evidence_ids': ['ev9']}]
    base['claims'] = [claim]
    base['evidence'] = [{'id': 'ev9', 'source': 'ocr_block', 'locator': 'b9', 'quote': text}]
    base['warnings'] = []
    p = make_prepared(original['prepared'], base, ids[0])
    review = keep(base)
    quoted, frame = deepcopy(claim), deepcopy(claim)
    quoted.update(id='pV1', proposition='A luz ilumina.', participants=[])
    frame.update(id='pV2', proposition='Se voltarem a objetar, as palavras de Lúcio os responderão.', mode='conditional')
    frame['attributions'] = [{'entity_id': 'e1', 'stance': 'AFFIRMS', 'scope': 'direct', 'evidence_ids': ['ev9']}]
    review['claim_decisions'][0].update(action='split', replacements=[quoted, frame])
    checked = evaluate_review(snap, {'raw_response': dumps(review)}, p)
    assert checked['valid']
    derived = checked['derived_extraction']
    assert derived['claims'][0]['attributions'][0]['entity_id'] == 'e2'
    assert derived['claims'][1]['attributions'][0]['entity_id'] == 'e1'
    for group in ('concepts', 'entities', 'events', 'evidence'):
        assert derived[group] == base[group]
    edges = checked['derived_validation']['graph_projection']['relations']
    assert not any(e['source_id'] == 'e2' and e['target_id'] == 'pV2' and e['predicate'] == 'AFFIRMS' for e in edges)
    # An existing but wrong holder is a semantic defect, not something a schema can establish.
    frame['attributions'][0]['entity_id'] = 'e2'
    wrong = evaluate_review(snap, {'raw_response': dumps(review)}, p)
    assert wrong['valid'] and wrong['semantic_status'] == 'pending_independent_review'


def test_review_adjudication_requires_sources_and_no_mutation(source):
    store, _, _, path = source
    exp = build(source)['experiment']
    execute_verification(store, exp, caller=fake, max_calls=1)
    write_report(store.path, exp, path/'report.md')
    forms = json.loads((path/'blind/judgments.template.json').read_text())
    form = forms['pairs'][0]
    form.update(reviewer='Synthetic independent test reviewer', independent_of_generator=True, source_checked=True)
    for output in form['outputs'].values():
        for unit in output['units']:
            unit.update(status='adjudicated', decision='correct', comment='Verificado apenas na fonte sintética.',
                        source_refs=[{'source': 'summary_segment', 'locator': 's1'}])
    decisions = path/'decisions.json'
    decisions.write_text(dumps(forms))
    before = frozen_state(store, exp)
    report = write_report(store.path, exp, path/'report.md', reviews=decisions)
    assert report['adjudicated_units'] > 0
    data = json.loads((path/'report.json').read_text())
    assert data['semantic_review']['transition_counts']['correct_content_preserved'] == 2
    assert frozen_state(store, exp) == before
    form['independent_of_generator'] = False
    decisions.write_text(dumps(forms))
    with pytest.raises(ValueError, match='independent'):
        write_report(store.path, exp, path/'report.md', reviews=decisions)
    with pytest.raises(ValueError, match='separate'):
        write_report(store.path, exp, path/'report.md', reviews=path/'blind/judgments.template.json')


def test_empty_valid_control(source):
    store, _, ids, _ = source
    original = store.get_run(ids[0])
    empty = {k: v if k == 'schema_version' else [] for k, v in extraction().items()}
    p = make_prepared(original['prepared'], empty, ids[0])
    checked = evaluate_review(store.get_case(original['case_id'])['snapshot'], {'raw_response': dumps(keep(empty))}, p)
    assert checked['valid'] and checked['derived_extraction'] == empty


def test_holdout_rejected_before_preparation(source, monkeypatch):
    store, _, ids, _ = source
    cid = store.get_run(ids[0])['case_id']
    with store.connect() as c:
        c.execute("UPDATE cases SET split='blind_holdout' WHERE id=?", (cid,))
    monkeypatch.setattr(store, 'get_run', lambda *_: pytest.fail('Do not read held-out output'))
    with pytest.raises(ValueError, match='holdout'):
        build(source)


def test_cli_preparation_and_empty_report(source, capsys):
    from scripts.playgrounds.kg_extractor_playground import main
    store, exp, ids, path = source
    before = frozen_state(store, exp)
    assert main(['verify-prepare', '--study', 'statement-boundaries-v1', '--db', str(store.path),
                 '--source-runs', *map(str, ids), '--output', str(path/'preflight.json')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['size'] == 8
    assert main(['verify-report', '--db', str(store.path), '--experiment', result['experiment'],
                 '--output', str(path/'report.md')]) == 0
    assert json.loads(capsys.readouterr().out)['blind_pairs_available'] == 0
    assert frozen_state(store, exp) == before
