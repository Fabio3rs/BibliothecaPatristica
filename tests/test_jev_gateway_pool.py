"""Real spawned workers against a local fake Gateway; no external API calls."""
from collections import Counter
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import sqlite3
import threading
import time

import pytest

from scripts import jev_gateway_pool as pool
from scripts import validate_index_jev as jev
from test_jev_index_results import bundle


@pytest.fixture
def server():
    state = {'active': 0, 'peak': 0, 'requests': [], 'authorizations': [], 'counts': Counter(), 'status': 200, 'cost': .01}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            key = payload.get('id', 0)
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                state['requests'].append(payload)
                state['authorizations'].append(self.headers.get('Authorization'))
                state['counts'][key] += 1
                count = state['counts'][key]
            time.sleep(.15)
            status = 429 if payload.get('retry') and count == 1 else state['status']
            body = {'answers': {'topic_present': {'probability': .98}},
                    'usage': {'inputTokens': 100, 'outputTokens': 2},
                    'providerMetadata': {'gateway': {'cost': state['cost']}}}
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            with lock:
                state['active'] -= 1
            self.wfile.write(raw)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{http.server_port}/evaluate', state
    http.shutdown()
    http.server_close()
    thread.join()


def execute(server, options=(), payloads=None, on_finish=None):
    endpoint, state = server
    args = jev.parser().parse_args(['--workers', '3', '--limit', '7', *options])
    gateway = jev.Gateway(args, time.monotonic() + 15, 'local-test-key')
    finished = []
    parent = os.getpid()

    def finish(item):
        assert os.getpid() == parent
        finished.append(item)
        if on_finish:
            on_finish(item, gateway)

    items = ({'payload': p} for p in (payloads if payloads is not None else [{'id': i} for i in range(30)]))
    try:
        details = pool.run_pool(items, args, gateway, endpoint, finish)
    finally:
        gateway.session.close()
    return gateway, finished, details


def test_real_concurrency_and_global_call_limit(server):
    gateway, finished, details = execute(server)
    assert gateway.calls == gateway.completed_calls == len(server[1]['requests']) == 7
    assert len(finished) == 7 and details['stop_reason'] == 'limit'
    assert 1 < server[1]['peak'] <= details['max_in_flight'] == 3
    attempts = [a for item in finished for a in item['attempts']]
    assert sorted(a['call_number'] for a in attempts) == list(range(1, 8))
    assert len({a['worker_pid'] for a in attempts}) > 1
    assert all(a['worker_pid'] != os.getpid() for a in attempts)


def test_retries_share_global_limit(server):
    gateway, finished, details = execute(server, ['--workers', '2', '--limit', '3'],
                                        [{'id': i, 'retry': True} for i in range(2)])
    assert gateway.calls == len(server[1]['requests']) == 3
    assert len(finished) == 2
    assert sorted(len(s['attempts']) for s in finished) == [1, 2]
    assert sum('probability' in s['attempts'][-1] for s in finished) == 1
    assert details['stop_reason'] == 'limit'


@pytest.mark.parametrize('status,cost,reason', [(200, .01, 'cost_budget'),
    (403, .01, 'authorization_error'), (200, None, 'cost_unavailable')])
def test_budget_or_auth_stops_dispatch_and_drains(server, status, cost, reason):
    server[1].update(status=status, cost=cost)
    gateway, finished, details = execute(server, ['--max-cost-usd', '.005'])
    assert gateway.calls == gateway.completed_calls == len(finished) == 3
    assert len(server[1]['requests']) == 3 and details['stop_reason'] == reason


def test_ctrl_c_drains_and_restores_signal_handler(server):
    previous = signal.getsignal(signal.SIGINT)

    def interrupt(item, gateway):
        if not gateway.stop_reason:
            os.kill(os.getpid(), signal.SIGINT)

    gateway, finished, details = execute(server, on_finish=interrupt)
    assert gateway.calls == gateway.completed_calls == len(finished) == 3
    assert details['stop_reason'] == 'interrupted'
    assert signal.getsignal(signal.SIGINT) == previous


def fail_worker(payload, call_number):
    raise RuntimeError('secret that must never reach logs')


def test_worker_failure_is_auditable_and_not_retried(server, monkeypatch):
    monkeypatch.setattr(pool, 'evaluate_once', fail_worker)
    gateway, finished, details = execute(server)
    assert details['stop_reason'] == 'worker_error'
    assert gateway.calls == len(finished) == 3
    assert not server[1]['requests']
    assert all(item['attempts'][0]['outcome_unknown'] for item in finished)
    assert 'secret' not in json.dumps(finished)


def test_expired_deadline_never_starts_workers(monkeypatch):
    args = jev.parser().parse_args(['--workers', '2'])
    gateway = jev.Gateway(args, time.monotonic() - 1, 'local')
    def forbidden(*a, **kw):
        raise AssertionError('pool must not start')
    monkeypatch.setattr(pool, 'ProcessPoolExecutor', forbidden)
    try:
        result = pool.run_pool([{'payload': {}}], args, gateway, 'unused', forbidden)
    finally:
        gateway.session.close()
    assert result['stop_reason'] == 'time_budget' and gateway.calls == 0


def configure_run(bundle, monkeypatch, tmp_path):
    args, spec, result = bundle
    args.workers = 2
    args.diagnosis = args.marker_report = tmp_path/'diagnosis.json'
    args.diagnosis.write_text('{}')
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'local-test-key')
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([deepcopy(spec)], []))
    return args, spec, result


def test_parallel_run_applies_in_parent_and_reuses_log(server, bundle, monkeypatch, tmp_path):
    args, spec, result = configure_run(bundle, monkeypatch, tmp_path)
    args.apply = True
    monkeypatch.setattr(jev, 'ENDPOINT', server[0])
    parent = os.getpid()
    original = jev.ResolutionWriter.apply
    def apply(self, *a):
        assert os.getpid() == parent
        return original(self, *a)
    monkeypatch.setattr(jev.ResolutionWriter, 'apply', apply)
    summary = jev.run(args)
    assert summary['calls'] == summary['applied'] == 1
    with sqlite3.connect(args.db) as con:
        assert con.execute('SELECT locator_status FROM alphabetical_refs').fetchone()[0] == 'resolved'
    logs = [json.loads(line) for line in args.log.read_text().splitlines()]
    assert logs[-1] == summary and any(r.get('status') == 'evaluated' for r in logs)
    args.resume_log = [args.log]
    args.log = tmp_path/'resumed.jsonl'
    summary = jev.run(args)
    assert summary['calls'] == 0 and summary['cache_hits'] == 1
    assert len(server[1]['requests']) == 1


def test_no_api_with_workers_does_not_create_pool(bundle, monkeypatch, tmp_path):
    args, _, _ = configure_run(bundle, monkeypatch, tmp_path)
    args.no_api = True
    def forbidden(*a, **kw):
        raise AssertionError('pool must not start')
    monkeypatch.setattr(pool, 'ProcessPoolExecutor', forbidden)
    summary = jev.run(args)
    assert summary['calls'] == 0 and summary['prepared'] == 1


def test_worker_skips_http_after_deadline(monkeypatch):
    monkeypatch.setattr(pool, '_CONFIG', ('local', 'unused', 30, time.monotonic() - 1))
    def forbidden(*a, **kw):
        raise AssertionError('HTTP must not start')
    monkeypatch.setattr(pool, 'request_once', forbidden)
    attempt = pool.evaluate_once({}, 5)
    assert attempt['attempted'] is False and attempt['error'] == 'time_budget'


def test_db_failure_preserves_all_dispatched_evaluations(server, bundle, monkeypatch, tmp_path):
    args, spec, _ = configure_run(bundle, monkeypatch, tmp_path)
    args.apply = True
    monkeypatch.setattr(jev, 'ENDPOINT', server[0])
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([deepcopy(spec) for _ in range(10)], []))
    def fail(*a):
        raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(jev.ResolutionWriter, 'apply', fail)
    summary = jev.run(args)
    assert summary['calls'] == summary['evaluated'] == 2
    assert summary['applied'] == 0 and summary['stop_reason'] == 'application_error'
    records = [json.loads(line) for line in args.log.read_text().splitlines()]
    assert sum(r.get('status') == 'application_error' for r in records) == 2
    with sqlite3.connect(args.db) as con:
        assert con.execute('SELECT locator_status FROM alphabetical_refs').fetchone()[0] == 'unresolved'
