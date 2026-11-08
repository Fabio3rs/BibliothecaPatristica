"""Offline staging integration tests. No real Gateway or agent calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tools.indexing import alphabetical_semantic_check as sem
from tools.indexing.alphabetical_analysis_db import (
    connect_analysis_db, init_analysis_schema, replace_semantic_payload,
    replace_locator_items, load_locator_results,
)
from tools.indexing.alphabetical_compact_pipeline import (
    build_locator_items, assemble_compact_payload, validate_locator_results,
)
from test_alphabetical_analysis_pipeline import scripture_semantic


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / 'PL001' / 'text'
    root.mkdir(parents=True)
    source = root / 'index-600.txt'
    target = root / 'body-050.txt'
    declaration = 'Numeri indicant paginas editionis antiquae operum Gaudentii.'
    source.write_text(declaration + '\nCaritas, 101.\n', encoding='utf-8')
    target.write_text('<pagina><bloco tipo="cabecalho">101 GAUDENTIUS</bloco>'
        '<bloco tipo="texto_principal">Caritas est amor Dei et proximi. '
        'Omnes fratres diligamus. SENTINEL_END_OF_OCR.</bloco></pagina>', encoding='utf-8')
    payload = scripture_semantic(root, ref_count=1)
    payload['scripture_refs'] = []
    payload['refs'] = payload['refs'][:1]
    payload['refs'][0].pop('scripture_ref_order')
    payload['sections'][0].update(section_kind='analytical_index', heading_raw='INDEX RERUM',
                                  file_start=str(source), file_end=str(source))
    payload['entries'][0].update(entry_kind='lemma', lemma_raw='Caritas', lemma_norm='Caritas',
                                 entry_raw='Caritas, 101.', context_raw=None, source_file=str(source))
    db = tmp_path / 'analysis.sqlite'
    with connect_analysis_db(db) as con:
        init_analysis_schema(con)
        replace_semantic_payload(con, payload=payload)
        items = build_locator_items(payload)
        items[0]['candidates'] = [{'file': str(target), 'probability': .9999,
            'helper_is_best': True, 'helper_status': 'resolved', 'candidate_role': 'target_candidate',
            'evidence': [{'kind': 'neighbor_sequence', 'detail': '100, 101, 102'}]}]
        replace_locator_items(con, volume_id='PL001', locator_items=items, deterministic_results=[])
        # Support historical fixtures whose source_file lives in raw_json instead.
        con.execute('UPDATE analysis_entries SET source_file=?', (str(source),))
        con.commit()
        rows = sem.load_rows(con, 'PL001')
    con.close()
    location_map = sem.mapping_draft(rows)
    scope = location_map['sections'][0]
    scope.update(reviewed=True, work='Gaudentius', edition='original', reviewer='fixture-review',
        evidence=[{'file':str(source), 'sha256':sem.digest(source.read_bytes()), 'quote':declaration}])
    scope['mappings'][0].update(verified=True, evidence=[{'file': str(target),
        'sha256': sem.digest(target.read_bytes()), 'quote':'101 GAUDENTIUS'}])
    map_file = tmp_path / 'map.json'
    sem.write_json(map_file, location_map)
    return SimpleNamespace(root=root, source=source, target=target, db=db, rows=rows,
        map=location_map, map_file=map_file, payload=payload, items=items, tmp=tmp_path)


def run(sample, options=None, **kwargs):
    return sem.semantic_check_stage(analysis_db=sample.db, volume_id='PL001',
        source_root=sample.root, intermediate_dir=sample.tmp / 'out',
        options=options or sem.JevOptions(), location_map_path=sample.map_file, **kwargs)


def fake_pool(monkeypatch, p=.98, error=None, cost=.001, before_finish=None):
    def execute(items, args, gateway, endpoint, finish):
        assert endpoint == sem.ENDPOINT
        for state in items:
            if not gateway.available():
                break
            gateway.calls += 1
            attempt = {'call_number':gateway.calls, 'latency_ms':10, 'cost_usd':cost,
                       'input_tokens':100, 'output_tokens':1}
            if error:
                attempt.update(error=error, http_status=429)
            else:
                attempt['probability'] = p
            gateway.observe(attempt)
            state['attempts'] = [attempt]
            if before_finish:
                before_finish(state)
            finish(state)
        return {'stop_reason': gateway.stop_reason or 'candidates_exhausted', 'max_in_flight':args.workers}
    monkeypatch.setattr(sem, 'run_pool', execute)
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'TEST-SECRET-NEVER-LOG')


def test_full_payload_single_question_and_prompt(sample):
    record, payload = sem.prepare(sample.rows[0], sample.map, sample.root, sem.JevOptions())
    assert record['status'] == 'prepared'
    assert payload['state'] == 'Complete OCR of the candidate physical page:\n' + sample.target.read_text()
    assert 'SENTINEL_END_OF_OCR' in payload['state']
    assert list(payload['questions']) == ['topic_present']
    question = payload['questions']['topic_present']
    assert question['type'] == 'boolean'
    assert 'Caritas' in question['instructions']
    assert 'Do not presume the index or its page mapping is correct' in question['instructions']
    assert 'Do not require literal matching' in question['instructions']


def test_no_api_no_db_changes_or_key(sample, monkeypatch):
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    monkeypatch.setattr(sem, 'run_pool', lambda *a, **kw: pytest.fail('network'))
    before = sample.db.read_bytes()
    summary = run(sample, sem.JevOptions(no_api=True, include_payload=True))
    assert summary['calls'] == 0 and summary['prepared'] == 1
    assert before == sample.db.read_bytes()
    payload = json.loads(next(Path(summary['log']).parent.glob('payload-*.json')).read_text())
    assert 'SENTINEL_END_OF_OCR' in payload['state']


@pytest.mark.parametrize('change,reason', [
    ('scope', 'editorial_scope_requires_review'),
    ('mapping', 'editorial_mapping_requires_review'),
    ('duplicate', 'multiple_physical_pages'),
    ('edition_hash', 'editorial_scope_requires_review'),
    ('target_hash', 'source_hash_changed'),
    ('bad_quote', 'evidence_quote_not_unique'),
    ('source', 'source_anchor_not_unique'),
    ('range', 'unsupported_or_non_single_editorial_coordinate'),
])
def test_fail_closed_despite_9999_score(sample, change, reason):
    scope = sample.map['sections'][0]
    row = sample.rows[0]
    if change == 'scope': scope['reviewed'] = False
    if change == 'mapping': scope['mappings'][0]['verified'] = False
    if change == 'duplicate': scope['mappings'].append(deepcopy(scope['mappings'][0]))
    if change == 'edition_hash': scope['section_sha256'] = 'bad'
    if change == 'target_hash': sample.target.write_text('CHANGED OCR')
    if change == 'bad_quote': scope['evidence'][0]['quote'] = 'Not present in the source'
    if change == 'source': row['entry_raw'] = 'Another entry, 101.'
    if change == 'range':
        item=json.loads(row['locator_item_json']); item['range_end_raw']='105'
        row['locator_item_json']=json.dumps(item)
    record, payload = sem.prepare(row, sample.map, sample.root, sem.JevOptions())
    assert payload is None and record['skip_reason'] == reason


def test_positive_promotes_staging_and_skips_agent_keeps_probability_null(sample, monkeypatch):
    fake_pool(monkeypatch)
    summary = run(sample, sem.JevOptions(apply=True))
    assert summary['applied'] == 1 and summary['canonical_db_updates'] == 0
    assert Path(summary['backup']).is_file()
    with connect_analysis_db(sample.db, read_only=True) as con:
        row = sem.load_rows(con,'PL001')[0]
        results = load_locator_results(con,'PL001')
        assert row['locator_status'] == 'resolved' and row['target_probability'] is None
        sem.check_stored_decisions(con,'PL001')
    assert results[0]['confidence'] is None
    assert results[0]['semantic_validation']['probability'] == .98
    canonical = assemble_compact_payload(sample.payload, results)
    assert canonical['refs'][0]['target_file_probability'] is None
    assert canonical['refs'][0]['raw_json']['compact_locator']['semantic_validation']['probability'] == .98
    # verify should consume no agent shards for an already resolved occurrence.
    from tools.indexing.alphabetical_analysis_pipeline import verify_stage
    verify_stage(analysis_db=sample.db, volume_id='PL001', source_root=sample.root,
        intermediate_dir=sample.tmp/'verify', agent_runner=lambda *a, **kw:pytest.fail('agent called'))
    assert 'TEST-SECRET' not in Path(summary['log']).read_text()


@pytest.mark.parametrize('p', [.05, .51, .6])
def test_negative_and_near_threshold_remain_pending(sample, monkeypatch, p):
    fake_pool(monkeypatch,p=p)
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['applied']==0
    with connect_analysis_db(sample.db,read_only=True) as con:
        assert sem.load_rows(con,'PL001')[0]['locator_status']=='pending'


def test_cache_replay_inspection_then_apply_without_key(sample, monkeypatch):
    fake_pool(monkeypatch)
    first=run(sample)
    monkeypatch.delenv('AI_GATEWAY_API_KEY')
    monkeypatch.setattr(sem,'run_pool',lambda *a,**kw:pytest.fail('cache should avoid HTTP'))
    second=run(sample,sem.JevOptions(apply=True))
    assert first['applied']==0
    assert second['cache_hits']==1 and second['calls']==0 and second['applied']==1


def test_stale_sources_block_cache_and_checkpoint_reuse(sample, monkeypatch):
    fake_pool(monkeypatch)
    run(sample,sem.JevOptions(apply=True))
    sample.target.write_text('Changed source')
    with connect_analysis_db(sample.db,read_only=True) as con:
        with pytest.raises(ValueError,match='hash changed'):
            sem.check_stored_decisions(con,'PL001')
    from tools.indexing.alphabetical_analysis_pipeline import assemble_stage
    with pytest.raises(ValueError,match='hash changed'):
        assemble_stage(analysis_db=sample.db, volume_id='PL001', source_root=sample.root,
            output_file=sample.tmp/'canonical.json', skip_fingerprint=True)


def test_staging_change_during_request_prevents_apply(sample,monkeypatch):
    def mutate(_):
        with connect_analysis_db(sample.db) as con:
            con.execute("UPDATE analysis_entries SET entry_raw='Changed entry'")
            con.commit()
    fake_pool(monkeypatch,before_finish=mutate)
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['applied']==0
    record=json.loads(Path(summary['log']).read_text().splitlines()[0])
    assert record['application_error']=='staging_or_sources_changed_before_apply'


def test_running_stage_guard(sample,monkeypatch):
    fake_pool(monkeypatch)
    with connect_analysis_db(sample.db) as con:
        con.execute("UPDATE analysis_volume_stages SET status='running' WHERE stage='verify'")
        con.commit()
    with pytest.raises(ValueError,match='running'):
        run(sample)
    assert run(sample,sem.JevOptions(no_api=True))['calls']==0


def test_api_error_and_missing_cost_stop_without_promotion(sample,monkeypatch):
    fake_pool(monkeypatch,error='http_error',cost=None)
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['statuses']['api_error']==1 and summary['applied']==0
    assert summary['stop_reason']=='cost_unavailable'


def test_size_guard_does_not_truncate(sample):
    record,payload=sem.prepare(sample.rows[0],sample.map,sample.root,sem.JevOptions(max_input_bytes=20))
    assert payload is None and record['skip_reason']=='ocr_too_large_no_truncation'


def test_cli_has_independent_worker_and_budget_controls(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from scripts.run_alphabetical_index_extraction import build_analysis_arg_parser
    args=build_analysis_arg_parser().parse_args(['run','--workers','2','--with-jev','--jev-workers','16',
        '--jev-limit','500','--jev-max-cost-usd','.25'])
    assert args.workers==2 and args.jev_workers==16 and args.jev_limit==500


def test_verify_receives_fresh_negative_hint(sample, monkeypatch):
    fake_pool(monkeypatch,p=.05)
    run(sample)
    with connect_analysis_db(sample.db,read_only=True) as con:
        hints=sem.semantic_hints(con,'PL001')
        hint=hints[sample.rows[0]['occurrence_key']][0]
        assert hint['decision'] is False and hint['topic_probability']==.05
        assert 'not pagination proof' in hint['instruction']
    sample.target.write_text('Changed source')
    with connect_analysis_db(sample.db,read_only=True) as con:
        assert sem.semantic_hints(con,'PL001')=={}


def test_changed_policy_invalidates_cache(sample,monkeypatch):
    fake_pool(monkeypatch)
    assert run(sample)['calls']==1
    second=run(sample,sem.JevOptions(threshold=.8))
    assert second['calls']==1 and second['cache_hits']==0


def test_revoked_map_during_request_prevents_apply(sample,monkeypatch):
    def revoke(_):
        sample.map['sections'][0]['reviewed']=False
        sem.write_json(sample.map_file,sample.map)
    fake_pool(monkeypatch,before_finish=revoke)
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['applied']==0


def test_command_budget_shared_between_stages(sample,monkeypatch):
    fake_pool(monkeypatch,cost=.01)
    options=sem.JevOptions(max_cost_usd=.01)
    budget=sem.JevBudget(options)
    try:
        assert run(sample,options,budget=budget)['calls']==1
        # Change the subject context to invalidate the cache without changing the map.
        with connect_analysis_db(sample.db) as con:
            con.execute("UPDATE analysis_entries SET lemma_raw='Caritas Dei'")
            con.commit()
        second=run(sample,options,budget=budget)
        assert second['calls']==0 and second['command_calls']==1
        assert second['command_reported_cost_usd']==.01
    finally:
        budget.close()


def test_two_semantic_writers_are_excluded(sample):
    with sem.semantic_lock(sample.db,'PL001',False):
        with pytest.raises(ValueError,match='already running'):
            run(sample)


@pytest.mark.parametrize('stop_reason', [None, 'interrupted', 'authentication_error'])
def test_cli_run_orders_jev_before_codex_verify(sample,monkeypatch,capsys,stop_reason):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from scripts import run_alphabetical_index_extraction as cli
    seen=[]
    for name in ['discover_stage','extract_stage','locate_stage','verify_stage','assemble_stage']:
        def stub(*, _name=name, **kw):
            seen.append(_name)
            if _name=='verify_stage': assert kw['workers']==2
            return {'status':'complete'}
        monkeypatch.setattr(cli,name,stub)
    def semantic(**kw):
        seen.append('semantic_check_stage')
        assert kw['options'].workers==16
        return {'status':'complete', 'stop_reason':stop_reason}
    monkeypatch.setattr(sem,'semantic_check_stage',semantic)
    monkeypatch.setattr(cli,'build_filtered_pages_artifact',lambda **kw:{})
    out=sample.tmp/'payloads';out.mkdir()
    (out/'PL001_filtered_pages.json').write_text('{}')
    argv=['run','--volume-id','PL001','--root',str(sample.root.parent.parent),
        '--analysis-db',str(sample.db),'--output-dir',str(out),
        '--intermediate-root',str(sample.tmp/'staging'),'--log-dir',str(sample.tmp/'logs'),
        '--with-jev','--workers','2','--jev-workers','16']
    if stop_reason:
        with pytest.raises(KeyboardInterrupt if stop_reason=='interrupted' else ValueError):
            cli.run_analysis_cli(argv)
        assert seen==['discover_stage','extract_stage','locate_stage','semantic_check_stage']
    else:
        cli.run_analysis_cli(argv)
        assert seen==['discover_stage','extract_stage','locate_stage','semantic_check_stage','verify_stage','assemble_stage']


@pytest.mark.parametrize('field', ['sha256', 'quote'])
def test_review_rejects_null_evidence(sample, field):
    sample.map['sections'][0]['evidence'][0][field] = None
    record, payload = sem.prepare(sample.rows[0], sample.map, sample.root, sem.JevOptions())
    assert payload is None
    assert record['skip_reason'] == 'invalid_reviewed_evidence'


def test_review_cache_replay_preserves_original_usage(sample, monkeypatch):
    fake_pool(monkeypatch)
    run(sample)
    with connect_analysis_db(sample.db, read_only=True) as con:
        original = con.execute('SELECT record_json FROM analysis_semantic_evaluations').fetchone()[0]
    run(sample)
    with connect_analysis_db(sample.db, read_only=True) as con:
        replayed = con.execute('SELECT record_json FROM analysis_semantic_evaluations').fetchone()[0]
    assert original == replayed
    assert json.loads(replayed)['attempts'][0]['cost_usd'] == .001


def test_review_verify_excluded_by_semantic_lock(sample):
    from tools.indexing.alphabetical_analysis_pipeline import verify_stage
    with sem.semantic_lock(sample.db, 'PL001', False):
        with pytest.raises(ValueError, match='already running'):
            verify_stage(analysis_db=sample.db, volume_id='PL001', source_root=sample.root,
                intermediate_dir=sample.tmp/'verify', agent_runner=lambda *a, **kw:pytest.fail('agent called'))


def test_review_json_formatting_does_not_invalidate_applied_decision(sample, monkeypatch):
    fake_pool(monkeypatch)
    run(sample, sem.JevOptions(apply=True))
    with connect_analysis_db(sample.db) as con:
        row = sem.load_rows(con, 'PL001')[0]
        con.execute('UPDATE analysis_occurrences SET locator_item_json=?',
                    (json.dumps(json.loads(row['locator_item_json']), indent=4),))
        con.commit()
        sem.check_stored_decisions(con, 'PL001')


@pytest.mark.parametrize('literal', ['I, 101', 'Leo 101', '101 seqq.', '101-105'])
def test_review_qualified_reference_does_not_use_plain_number_map(sample, literal):
    row = sample.rows[0]
    row['ref_raw'] = literal
    row['entry_raw'] = 'Caritas, ' + literal + '.'
    sample.source.write_text(sample.source.read_text().replace('Caritas, 101.', row['entry_raw']))
    scope = sample.map['sections'][0]
    scope['evidence'][0]['sha256'] = sem.digest(sample.source.read_bytes())
    item=json.loads(row['locator_item_json']); item['ref_raw']=literal
    row['locator_item_json']=json.dumps(item)
    record,payload=sem.prepare(row,sample.map,sample.root,sem.JevOptions())
    assert payload is None and record['skip_reason']=='qualified_coordinate_requires_mapping'


def test_review_revoked_map_blocks_assemble_after_apply(sample, monkeypatch):
    fake_pool(monkeypatch)
    run(sample, sem.JevOptions(apply=True))
    sample.map['sections'][0]['reviewed'] = False
    sem.write_json(sample.map_file, sample.map)
    from tools.indexing.alphabetical_analysis_pipeline import assemble_stage
    with pytest.raises(ValueError, match='map'):
        assemble_stage(analysis_db=sample.db, volume_id='PL001', source_root=sample.root,
            output_file=sample.tmp/'canonical.json', skip_fingerprint=True)


def test_review_cached_observation_is_reclassified(sample, monkeypatch):
    fake_pool(monkeypatch, p=.05)
    run(sample)
    with connect_analysis_db(sample.db) as con:
        r=con.execute('SELECT record_json FROM analysis_semantic_evaluations').fetchone()
        record=json.loads(r[0]);record.update(decision=True,needs_review=False)
        con.execute('UPDATE analysis_semantic_evaluations SET record_json=?',(json.dumps(record),))
        con.commit()
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['applied']==0 and summary['no']==1


def test_review_semantic_lock_uses_canonical_db_path(sample, monkeypatch):
    monkeypatch.chdir(sample.tmp)
    with sem.semantic_lock(sample.db,'PL001',False):
        with pytest.raises(ValueError,match='already running'):
            with sem.semantic_lock(Path('analysis.sqlite'),'PL001',False):
                pytest.fail('alias bypassed volume lock')


def test_review_deleted_map_during_request_keeps_audited_evaluation(sample, monkeypatch):
    fake_pool(monkeypatch,before_finish=lambda _:sample.map_file.unlink())
    summary=run(sample,sem.JevOptions(apply=True))
    assert summary['evaluated']==1 and summary['applied']==0
    record=json.loads(Path(summary['log']).read_text().splitlines()[0])
    assert record['application_error']=='editorial_map_unavailable_before_apply'
    assert record['attempts'][0]['cost_usd']==.001
