"""Run only with INDEX_MULTIMODAL_LOOPBACK=1 inside Native Lab MCP."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

import pytest
from test_multimodal_reextraction import database, make_sources, main, reading_for
from tools.indexing.multimodal.sources import read_page

pytestmark = pytest.mark.skipif(os.getenv('INDEX_MULTIMODAL_LOOPBACK') != '1', reason='Native Lab loopback opt-in')


@pytest.fixture
def server(tmp_path):
    state = {'calls': 0, 'active': 0, 'max_active': 0, 'retry_once': False, 'requests': [], 'invalid_dto': False}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                state['calls'] += 1
                number = state['calls']
                state['requests'].append(body)
                state['active'] += 1
                state['max_active'] = max(state['max_active'], state['active'])
            time.sleep(.08)
            if state['retry_once'] and number == 1:
                status, result = 429, {'error': {'message': 'Flex capacity unavailable'}}
            else:
                if self.path == '/v1/chat/completions':
                    parts = body['messages'][1]['content']
                else:
                    parts = body['input'][0]['content']
                xml = next(p['text'].split('OCR XML:\n', 1)[1] for p in parts if 'OCR XML:\n' in p.get('text', ''))
                source = tmp_path / 'PG113' / 'text' / f'loopback-{number:03d}.txt'
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(xml)
                schema = (body.get('response_format', {}).get('json_schema') or body.get('text', {}).get('format', {}))
                if schema.get('name') == 'alphabetical_literal_v1':
                    from test_multimodal_literal import reading
                    dto = reading()
                else:
                    dto = reading_for({'pages': [read_page(source)]})
                if state['invalid_dto']:
                    dto = {}
                result = {'service_tier': 'flex', 'model': body['model'],
                          'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}
                if self.path == '/v1/chat/completions':
                    result['choices'] = [{'finish_reason': 'stop', 'message': {'content': json.dumps(dto)}}]
                else:
                    result.update(status='completed', output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(dto)}]}])
                status = 200
            with lock:
                state['active'] -= 1
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '0')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}/v1', state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def arguments(tmp_path, endpoint, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'loopback-test-not-a-real-key')
    root, _ = make_sources(tmp_path, 1)
    make_sources(tmp_path, 2)
    db = database(tmp_path/'fixture.sqlite')
    args = ['--root', str(root), '--db', str(db), '--volume', 'PG113', '--pages', '1-2',
        '--neighbors', '0', '--mode', 'ocr-image', '--base-url', endpoint,
        '--workers', '2', '--max-rps', '0', '--max-seconds', '20', '--timeout', '3', '--limit', '2',
        '--input-usd-per-million', '.375', '--output-usd-per-million', '2.25',
        '--out', str(tmp_path/'run')]
    return args, db


@pytest.mark.parametrize('mode', ['chat', 'responses'])
def test_loopback_concurrency_resume_and_readonly(tmp_path, server, monkeypatch, mode, capsys):
    endpoint, state = server
    args, db = arguments(tmp_path, endpoint, monkeypatch)
    args += ['--api-mode', mode]
    before = db.read_bytes()
    assert main(args) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['calls'] == 2 and summary['statuses'] == {'validated_staging': 2}, summary
    assert state['max_active'] == 2
    assert all(r['service_tier'] == 'flex' for r in state['requests'])
    for request in state['requests']:
        assert not {'max_tokens', 'max_output_tokens', 'max_completion_tokens'} & request.keys()
        assert (request.get('reasoning_effort') or request.get('reasoning', {}).get('effort')) == 'medium'
    assert 'data:image/png;base64,' in json.dumps(state['requests'])
    capsys.readouterr()
    assert main(args + ['--resume', '--verbose']) == 0
    cached_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"task_id"')]
    assert len(cached_lines) == 2
    assert all(row['cache_hit'] and row['attempts'] == 0 and row['validation_errors'] == [] for row in cached_lines)
    assert state['calls'] == 2
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['calls'] == 0 and summary['cache_hits'] == 2
    assert db.read_bytes() == before
    assert not any('loopback-test-not-a-real-key' in p.read_text() for p in (tmp_path/'run').rglob('*.json'))


def test_loopback_retry_and_new_task_limit(tmp_path, server, monkeypatch):
    endpoint, state = server
    state['retry_once'] = True
    args, _ = arguments(tmp_path, endpoint, monkeypatch)
    assert main(args + ['--limit', '1']) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['calls'] == 2 and summary['new_tasks_submitted'] == 1
    assert summary['stop_reason'] == 'task_limit'
    assert summary['statuses'] == {'validated_staging': 1}
    assert all(r['service_tier'] == 'flex' for r in state['requests'])


def test_loopback_budget_blocks_dispatch(tmp_path, server, monkeypatch):
    endpoint, state = server
    args, _ = arguments(tmp_path, endpoint, monkeypatch)
    assert main(args + ['--max-cost-usd', '.000001']) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['stop_reason'] == 'cost_reservation_limit'
    assert state['calls'] == 0


def test_loopback_invalid_dto_is_logged(tmp_path, server, monkeypatch):
    endpoint, state = server
    state['invalid_dto'] = True
    args, _ = arguments(tmp_path, endpoint, monkeypatch)
    assert main(args + ['--limit', '1']) == 1
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['statuses'] == {'validation_failed': 1}


@pytest.mark.parametrize('mode', ['chat', 'responses'])
def test_literal_loopback_concurrency_resume_and_readonly(tmp_path, server, monkeypatch, mode):
    endpoint, state = server
    args, db = arguments(tmp_path, endpoint, monkeypatch)
    args += ['--extraction-profile', 'literal-v1', '--context-images', 'never', '--api-mode', mode]
    before = db.read_bytes()
    assert main(args) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['statuses'] == {'literal_staged': 2}
    assert summary['calls'] == 2 and state['max_active'] == 2
    for body in state['requests']:
        parts = body['messages'][1]['content'] if mode == 'chat' else body['input'][0]['content']
        assert sum(p['type'] in ('image_url', 'input_image') for p in parts) == 1
        assert sum('OCR XML:' in p.get('text', '') for p in parts) == 1
        assert body['service_tier'] == 'flex'
    assert main(args + ['--resume']) == 0
    assert state['calls'] == 2 and db.read_bytes() == before


def test_literal_retry_limit_and_budget(tmp_path, server, monkeypatch):
    endpoint, state = server
    args, db = arguments(tmp_path, endpoint, monkeypatch)
    args += ['--extraction-profile', 'literal-v1', '--context-images', 'never', '--limit', '1']
    assert main(args + ['--max-cost-usd', '.000001']) == 0
    assert state['calls'] == 0
    state['retry_once'] = True
    assert main(args + ['--resume']) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['calls'] == 2 and summary['new_tasks_submitted'] == 1
    assert summary['statuses'] == {'literal_staged': 1}
