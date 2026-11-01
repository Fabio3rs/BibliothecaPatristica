"""Process workers and local fake HTTP providers: run through Native Lab."""
from __future__ import annotations

import fcntl
import io
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_kg_playground import corpus
from tools.knowledge_graph.playground.common import digest, dumps
from tools.knowledge_graph.playground.corpus_queue import create_campaign, discover, recover, status, set_source_policy
from tools.knowledge_graph.playground.corpus_runner import lock_paths, run_campaign
from tools.knowledge_graph.playground.providers import ProviderError, normalize_config, retry_after_seconds, send
from tools.knowledge_graph.playground.service import Playground


@pytest.fixture
def app(corpus, tmp_path):
    db, root, versions = corpus
    return Playground(db=tmp_path/'lab.db', summaries_db=db, corpus_root=root,
                      indices_db=tmp_path/'indices.db', benchmark_dir=tmp_path/'benchmark',
                      ocr_versions_db=versions)


def add_generation(app, identifier, page=71, volume='PG026', current=0, state='valid', taint=None):
    with sqlite3.connect(app.summaries_db) as con:
        columns = [r[1] for r in con.execute('PRAGMA table_info(resumo_generations)')]
        row = dict(zip(columns, con.execute('SELECT * FROM resumo_generations WHERE id=1').fetchone()))
        row.update(id=identifier, pagina_num=page, documento=volume, is_current=current,
                   status=state, tainted_by_page=taint, pagina_file=f'{volume}-{page:03}.txt', ocr_result_id=None)
        con.execute('INSERT INTO resumo_generations VALUES(' + ','.join('?' for _ in columns) + ')', [row[k] for k in columns])
    path = app.corpus_root / volume / 'text' / row['pagina_file']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<pagina><bloco>λόγος et substantia.</bloco></pagina>')


@pytest.fixture
def fake_api():
    calls = []
    setting = {'fail': False, 'invalid': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            if setting.get('barrier'):
                setting['barrier'].wait(timeout=10)
            time.sleep(setting.get('delay', 0.08))
            if setting['fail']:
                self.send_response(503); self.end_headers(); return
            data = {'schema_version': 'kg-extraction-v2', **{k: [] for k in
                    ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence', 'warnings')}}
            response = {'choices': [{'message': {'content': 'bad json' if setting['invalid'] else dumps(data)},
                                     'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 100, 'completion_tokens': 40}}
            raw = dumps(response).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    yield {'provider': 'compatible', 'model': 'synthetic-http', 'base_url': f'http://127.0.0.1:{http.server_port}/v1',
           'retries': 0}, calls, setting
    http.shutdown(); http.server_close(); thread.join()


def test_discovery_priority_shadow_eligibility_filters_pinning(app):
    add_generation(app, 2, current=1)
    add_generation(app, 3)
    add_generation(app, 4, page=72, state='metadata_pending')
    add_generation(app, 5, page=72, taint=71)
    add_generation(app, 6, page=73, state='invalid')
    add_generation(app, 7, volume='PL001')
    before = digest(app.summaries_db.read_bytes())
    campaign = create_campaign(app, {'provider': 'meta', 'model': 'test'}, collections=['PG'])
    assert discover(app.store, campaign, chunk_size=1)['discovered'] == 2
    assert discover(app.store, campaign, chunk_size=1)['discovered'] == 0
    with app.store.connect() as con:
        rows = con.execute('SELECT generation_id FROM corpus_jobs ORDER BY id').fetchall()
    assert [r[0] for r in rows] == [2, 4]
    add_generation(app, 8, page=72)
    discover(app.store, campaign)
    assert {r['generation_id'] for r in status(app.store, campaign)['recent_jobs']} == {2, 4}
    assert before != digest(app.summaries_db.read_bytes())  # Only our inserted fixture generation changed it.
    after = digest(app.summaries_db.read_bytes())
    discover(app.store, campaign)
    assert after == digest(app.summaries_db.read_bytes())
    assert status(app.store, campaign)['journal_mode'] == 'wal'


def test_holdout_exclusion_and_ambiguous_current(app):
    app.benchmark_dir.mkdir()
    (app.benchmark_dir/'selected_pages.jsonl').write_text(dumps({'split': 'blind_holdout', 'source': {'documento': 'PG026'}})+'\n')
    campaign = create_campaign(app, {'provider': 'meta', 'model': 'test'})
    assert discover(app.store, campaign)['excluded_holdout'] == 1
    assert status(app.store, campaign)['total_pages'] == 0
    campaign = create_campaign(app, {'provider': 'meta', 'model': 'test'}, evaluation=True)
    assert discover(app.store, campaign)['discovered'] == 1
    add_generation(app, 2, current=1)
    add_generation(app, 3, current=1)
    campaign = create_campaign(app, {'provider': 'meta', 'model': 'test'}, evaluation=True)
    assert discover(app.store, campaign)['ambiguous_current'] == 1


def test_discovery_crosses_volume_boundaries_without_offset_or_duplicates(app):
    add_generation(app, 2, page=72)
    add_generation(app, 3, page=1, volume='PL001')
    add_generation(app, 4, page=2, volume='PL001', state='metadata_pending')
    campaign = create_campaign(app, {'provider': 'meta', 'model': 'test'})
    assert discover(app.store, campaign, chunk_size=1)['discovered'] == 4
    assert discover(app.store, campaign, chunk_size=1)['discovered'] == 0
    info = status(app.store, campaign)
    assert {(r['documento'], r['pagina_num']) for r in info['recent_jobs']} == {
        ('PG026', 71), ('PG026', 72), ('PL001', 1), ('PL001', 2)}


def test_allow_drift_policy_persists_and_requeues_only_drift_errors(app, fake_api):
    add_generation(app, 2, page=72)
    add_generation(app, 3, page=73)
    path = app.corpus_root/'PG026/text/PG026-071.txt'
    historical = path.read_text()
    path.write_text('<pagina><bloco>Current OCR revised.</bloco></pagina>')
    (app.corpus_root/'PG026/text/PG026-072.txt').unlink()
    campaign = create_campaign(app, fake_api[0])
    original_plan = app.store.experiment_plan(campaign)
    assert run_campaign(app.store, campaign)['states'] == {'completed': 1, 'source_error': 2}
    old_id = app.store.list_runs(campaign)[0]['id']
    old_run = app.store.get_run(old_id)
    events = []
    result = run_campaign(app.store, campaign, allow_source_drift=True, progress=events.append)
    assert result['states'] == {'completed': 2, 'source_error': 1}
    assert any(e.get('requeued_source_drift') == 1 for e in events)
    assert app.store.experiment_plan(campaign) == original_plan
    assert app.store.get_run(old_id) == old_run
    new = app.store.get_run(app.store.list_runs(campaign)[0]['id'])
    snap = app.store.get_case(new['case_id'])['snapshot']
    assert snap['source']['historical_ocr_matches'] is False
    assert snap['source']['source_drift_allowed'] is True
    assert snap['source']['historical_ocr'] == {'id': 9, 'sha256': digest(historical)}
    assert snap['source']['ocr_sha256'] == digest(path.read_bytes())
    assert any('SOURCE_CONFLICT' in warning for warning in snap['ocr']['warnings'])
    assert new['prepared']['corpus_source_policy']['revision_id'] is not None
    assert run_campaign(app.store, campaign)['submitted'] == 0
    assert len(fake_api[1]) == 2
    revision = status(app.store, campaign)['source_policy']['revision_id']
    assert set_source_policy(app.store, campaign, True) == revision
    assert set_source_policy(app.store, campaign, False) != revision
    assert not status(app.store, campaign)['source_policy']['allow_source_drift']
    with app.store.connect() as con:
        attempts = [dict(r) for r in con.execute('SELECT * FROM corpus_attempts WHERE job_id=(SELECT id FROM corpus_jobs WHERE pagina_num=71) ORDER BY number')]
    assert [r['status'] for r in attempts] == ['source_error', 'completed']


def test_allow_drift_create_and_dry_run_never_calls_provider(app, fake_api):
    path = app.corpus_root/'PG026/text/PG026-071.txt'
    path.write_text('<pagina><bloco>Revised OCR.</bloco></pagina>')
    campaign = create_campaign(app, fake_api[0], allow_source_drift=True)
    assert run_campaign(app.store, campaign, dry_run=True)['states'] == {'prepared': 1}
    assert not fake_api[1]


def test_allow_drift_does_not_allow_wrong_page_or_unverified_fallback(app):
    from tools.knowledge_graph.playground.snapshot_v2 import load_page
    with sqlite3.connect(app.ocr_versions_db) as con:
        con.execute('UPDATE ocr_results SET page_num=72 WHERE id=9')
    with pytest.raises(ValueError, match='different physical page'):
        load_page(app.summaries_db, app.corpus_root, 'PG026:71', ocr_versions_db=app.ocr_versions_db,
                  allow_source_drift=True)
    with sqlite3.connect(app.ocr_versions_db) as con:
        con.execute('UPDATE ocr_results SET page_num=71 WHERE id=9')
    path = app.corpus_root/'PG026/text/PG026-071.txt'
    path.rename(path.with_name('another-071.txt'))
    path.with_name('another-071.txt').write_text('Revised OCR at unverified fallback path')
    with pytest.raises(ValueError, match='Numerical fallback requires'):
        load_page(app.summaries_db, app.corpus_root, 'PG026:71', ocr_versions_db=app.ocr_versions_db,
                  allow_source_drift=True)


def test_spawn_dry_run_resume_freezes_prompt_and_preserves_results(app, fake_api, monkeypatch):
    config, calls, _ = fake_api
    add_generation(app, 2, page=72)
    campaign = create_campaign(app, config)
    # Future prompt registry edits must not change this campaign's requests.
    from tools.knowledge_graph.playground import prompt_registry
    monkeypatch.setattr(prompt_registry, 'variants', lambda: [])
    events = []
    before = digest(app.summaries_db.read_bytes())
    result = run_campaign(app.store, campaign, workers=2, dry_run=True, progress=events.append)
    assert result['states'] == {'prepared': 2}
    assert not calls
    assert all(e['worker_pid'] != os.getpid() for e in events if 'worker_pid' in e)
    # Very short dry-runs may both finish in the first ready child before the
    # second spawn finishes startup. Distinct active workers are tested below.
    assert 1 <= len({e['worker_pid'] for e in events if 'worker_pid' in e}) <= 2
    runs_before = {r['id']: app.store.get_run(r['id'])['prepared'] for r in app.store.list_runs(campaign)}
    result = run_campaign(app.store, campaign, workers=2, max_jobs=1)
    assert result['states'] == {'completed': 1, 'prepared': 1}
    result = run_campaign(app.store, campaign, workers=2)
    assert result['states'] == {'completed': 2}
    assert len(calls) == 2
    assert run_campaign(app.store, campaign)['submitted'] == 0
    assert len(calls) == 2
    for identifier, prepared in runs_before.items():
        assert app.store.get_run(identifier)['prepared'] == prepared
    assert digest(app.summaries_db.read_bytes()) == before
    assert result['journal_mode'] == 'wal'


def test_two_processes_can_make_simultaneous_requests(app, fake_api):
    add_generation(app, 2, page=72)
    fake_api[2]['barrier'] = threading.Barrier(2)
    campaign = create_campaign(app, fake_api[0])
    events = []
    result = run_campaign(app.store, campaign, workers=2, progress=events.append)
    assert result['states'] == {'completed': 2}
    pids = {e['worker_pid'] for e in events if 'worker_pid' in e}
    assert len(pids) == 2 and os.getpid() not in pids


def test_retry_errors_append_runs_but_invalid_does_not_retry(app, fake_api):
    config, calls, settings = fake_api
    settings['fail'] = True
    campaign = create_campaign(app, config, max_pages=1)
    assert run_campaign(app.store, campaign)['states'] == {'error': 1}
    old = app.store.get_run(app.store.list_runs(campaign)[0]['id'])
    assert run_campaign(app.store, campaign)['submitted'] == 0
    settings.update(fail=False, invalid=True)
    assert run_campaign(app.store, campaign, retry_errors=True)['states'] == {'invalid': 1}
    new = app.store.get_run(app.store.list_runs(campaign)[0]['id'])
    assert new['prepared']['retry_of'] == old['id']
    assert app.store.get_run(old['id'])['result'] == old['result']
    assert run_campaign(app.store, campaign, retry_errors=True)['submitted'] == 0
    assert len(calls) == 2


def test_recovery_reconciles_completed_and_reuses_planned(app, fake_api):
    config, calls, _ = fake_api
    campaign = create_campaign(app, config)
    run_campaign(app.store, campaign, dry_run=True)
    run_id = app.store.list_runs(campaign)[0]['id']
    with app.store.connect() as con:
        con.execute("UPDATE corpus_jobs SET status='preparing'")
    recover(app.store, campaign)
    assert status(app.store, campaign)['states'] == {'pending': 1}
    run_campaign(app.store, campaign)
    assert len(app.store.list_runs(campaign)) == 1
    with app.store.connect() as con:
        con.execute("UPDATE corpus_jobs SET status='running'")
    recover(app.store, campaign)
    assert status(app.store, campaign)['states'] == {'completed': 1}
    assert run_campaign(app.store, campaign)['submitted'] == 0
    assert len(calls) == 1
    assert app.store.get_run(run_id)['status'] == 'completed'


def test_interrupted_running_attempt_is_preserved(app, fake_api):
    config, _, _ = fake_api
    campaign = create_campaign(app, config)
    run_campaign(app.store, campaign, dry_run=True)
    old_id = app.store.list_runs(campaign)[0]['id']
    app.store.start(old_id)
    with app.store.connect() as con:
        con.execute("UPDATE corpus_jobs SET status='running'")
    assert run_campaign(app.store, campaign)['states'] == {'completed': 1}
    assert app.store.get_run(old_id)['status'] == 'interrupted'
    new = app.store.get_run(app.store.list_runs(campaign)[0]['id'])
    assert new['prepared']['retry_of'] == old_id


def test_missing_source_is_recorded_and_manual_retry_recovers(app, fake_api):
    config, calls, _ = fake_api
    path = app.corpus_root/'PG026/text/PG026-071.txt'
    raw = path.read_bytes(); path.unlink()
    campaign = create_campaign(app, config)
    assert run_campaign(app.store, campaign)['states'] == {'source_error': 1}
    assert not calls
    path.write_bytes(raw)
    assert run_campaign(app.store, campaign, retry_errors=True)['states'] == {'completed': 1}
    attempts = status(app.store, campaign)['recent_attempts']
    assert [a['status'] for a in attempts] == ['completed', 'source_error']


def test_worker_and_coordinator_locks_prevent_duplicate_execution(app, fake_api):
    campaign = create_campaign(app, fake_api[0])
    paths = lock_paths(app.store, campaign)
    for path in paths:
        with path.open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(ValueError, match='already executing|still active'):
                run_campaign(app.store, campaign)
    assert not fake_api[1]


def test_follow_discovers_new_pages_with_bound_on_submissions(app, fake_api):
    campaign = create_campaign(app, fake_api[0])
    def progress(event):
        if event.get('finished') == 1:
            add_generation(app, 2, page=72)
    result = run_campaign(app.store, campaign, follow=True, poll_interval=1, max_jobs=2, progress=progress)
    assert result['states'] == {'completed': 2}
    assert result['submitted'] == 2


def test_backoff_full_jitter_and_retry_after():
    config = normalize_config({'provider': 'compatible', 'model': 'test', 'retries': 3,
                               'retry_backoff_base': 2, 'retry_backoff_max': 3})
    calls, delays, bounds = [], [], []
    def transport(req, timeout):
        calls.append(1)
        if len(calls) < 4:
            header = {'Retry-After': '10'} if len(calls) == 1 else {}
            raise urllib.error.HTTPError(req.full_url, 429, 'wait', header, io.BytesIO(b'wait'))
        return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
    def uniform(low, high):
        bounds.append((low, high)); return high / 2
    result = send(config, {'url': 'http://127.0.0.1/test', 'body': {}}, transport=transport,
                  sleeper=delays.append, uniform=uniform)
    assert delays == [11, 1.5, 1.5]
    assert bounds == [(0, 2), (0, 3), (0, 3)]
    assert len(result['retry_log']) == 3
    assert retry_after_seconds('Thu, 01 Jan 1970 00:00:20 GMT', 5) == 15
    assert retry_after_seconds('invalid', 5) == 0


def test_long_retry_after_is_not_retried_early():
    config = normalize_config({'provider': 'compatible', 'model': 'test'})
    def transport(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, 'wait', {'Retry-After': '3601'}, io.BytesIO(b'wait'))
    delays = []
    with pytest.raises(ProviderError, match='wait budget'):
        send(config, {'url': 'http://127.0.0.1/test', 'body': {}}, transport=transport, sleeper=delays.append)
    assert not delays


def test_source_changed_after_discovery_is_not_silently_extracted(app, fake_api):
    campaign = create_campaign(app, fake_api[0])
    discover(app.store, campaign)
    with sqlite3.connect(app.summaries_db) as con:
        con.execute("UPDATE resumo_generations SET source_hash='changed'")
    result = run_campaign(app.store, campaign)
    assert result['states'] == {'source_error': 1}
    assert 'changed since discovery' in result['recent_attempts'][0]['error']
    assert not fake_api[1]


def test_monitor_reads_external_cli_progress_and_protects_evaluation(app, fake_api):
    from tools.knowledge_graph.playground.web_app import make_server
    campaign = create_campaign(app, fake_api[0], name='<script>untrusted label</script>')
    held = create_campaign(app, fake_api[0], evaluation=True)
    run_campaign(app.store, campaign, dry_run=True)
    http = make_server(app, port=0)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    def read(path):
        with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}{path}') as response:
            return json.load(response)
    try:
        assert [row['id'] for row in read('/api/campaigns')] == [campaign]
        assert len(read('/api/campaigns?evaluation=true')) == 2
        assert read('/api/campaign?id='+campaign)['states'] == {'prepared': 1}
        assert read('/api/progress?experiment='+campaign)['journal_mode'] == 'wal'
        with pytest.raises(urllib.error.HTTPError): read('/api/campaign?id='+held)
        assert read('/api/campaign?id='+held+'&evaluation=true')['total_pages'] == 0
        assert read('/api/report?experiment='+campaign)['states'] == {'prepared': 1}
        rows = read('/api/runs?experiment='+campaign+'&limit=1')
        assert len(rows) == 1
        assert read('/api/runs?experiment='+campaign+'&before='+str(rows[0]['id'])) == []
        run_campaign(app.store, campaign)
        assert read('/api/campaign?id='+campaign)['states'] == {'completed': 1}
        assert len(fake_api[1]) == 1
    finally:
        http.shutdown(); http.server_close(); thread.join()


def test_sigterm_drains_inflight_and_resumes_follow_queue(app, fake_api):
    import signal
    import subprocess
    from tools.knowledge_graph.playground.common import ROOT
    campaign = create_campaign(app, fake_api[0])
    fake_api[2]['delay'] = 1.5
    command = [str(ROOT/'.venv/bin/python'), str(ROOT/'scripts/playgrounds/kg_extractor_playground.py'),
               'corpus-run', '--db', str(app.store.path), '--campaign', campaign, '--follow', '--poll-interval', '1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if fake_api[1]:
                break
            time.sleep(0.05)
        else:
            pytest.fail('Spawn executor did not start the mock request')
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stderr
        assert '"stopped": true' in stdout
        assert status(app.store, campaign)['states'] == {'completed': 1}
        assert run_campaign(app.store, campaign)['submitted'] == 0
        assert len(fake_api[1]) == 1
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=10)


def test_killed_worker_leaves_recoverable_run(app, fake_api):
    import signal
    import subprocess
    from tools.knowledge_graph.playground.common import ROOT
    campaign = create_campaign(app, fake_api[0])
    fake_api[2]['delay'] = 1.5
    command = [str(ROOT/'.venv/bin/python'), str(ROOT/'scripts/playgrounds/kg_extractor_playground.py'),
               'corpus-run', '--db', str(app.store.path), '--campaign', campaign, '--workers', '1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            info = status(app.store, campaign)
            if fake_api[1] and info['recent_attempts']:
                break
            time.sleep(0.05)
        else:
            pytest.fail('Worker did not begin mock request')
        worker_pid = info['recent_attempts'][0]['worker_pid']
        old_id = app.store.list_runs(campaign)[0]['id']
        os.kill(worker_pid, signal.SIGKILL)
        _, stderr = process.communicate(timeout=10)
        assert process.returncode != 0
        assert 'queue is saved' in stderr
        fake_api[2]['delay'] = 0.01
        assert run_campaign(app.store, campaign)['states'] == {'completed': 1}
        assert app.store.get_run(old_id)['status'] == 'interrupted'
        assert len(fake_api[1]) == 2
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=10)
