"""Local-only standard-library HTTP application; no frontend build or CDN."""
from __future__ import annotations

import json
import mimetypes
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .common import digest, dumps
from .comparison import compare_runs
from .experiment_runner import execute_experiment, plan_experiment
from .prompt_registry import MODES, variants
from .providers import DEFAULTS, DEFAULT_MAX_OUTPUT_TOKENS
from .pricing import catalog as price_catalog, lookup as price_lookup
from .reporting import csv_report, export_jsonl, markdown, report
from .snapshot_v2 import guard_holdout
from .validation import evaluate_response
from .corpus_queue import list_campaigns, status as corpus_status

STATIC = Path(__file__).parent / 'static'


def make_server(app, host='127.0.0.1', port=8766):
    if host not in {'127.0.0.1', 'localhost'}:
        raise ValueError('This research server is restricted to localhost')
    csrf = secrets.token_urlsafe(32)
    execution_lock = threading.Lock()
    progress = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'KGPlayground/1'

        def log_message(self, fmt, *args):
            # Access logs never include bodies, keys, headers, or query strings.
            pass

        def allowed_origin(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                return False
            origin = self.headers.get('Origin')
            return origin is None or origin in {'http://' + item for item in allowed}

        def reply(self, value, status=200, content_type='application/json; charset=utf-8', filename=None):
            data = dumps(value).encode('utf-8') if content_type.startswith('application/json') else value if isinstance(value, bytes) else value.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def case_access(self, identifier, evaluation):
            case = app.store.get_case(int(identifier))
            if case['split'] == 'blind_holdout' and not evaluation:
                raise ValueError('Evaluation mode required')
            if not case['synthetic']:
                guard_holdout(app.benchmark_dir, case['page'], evaluation)
            return case

        def run_access(self, identifier, evaluation):
            run = app.store.get_run(int(identifier))
            self.case_access(run['case_id'], evaluation)
            return run

        def experiment_access(self, identifier, evaluation):
            plan = app.store.experiment_plan(identifier)
            if plan['evaluation'] and not evaluation:
                raise ValueError('Evaluation mode required for this experiment')
            for case_id in plan.get('case_ids', []):
                self.case_access(case_id, evaluation)
            return plan

        def do_GET(self):
            if not self.allowed_origin():
                return self.reply({'error': 'Local origin required'}, 403)
            parts = urlsplit(self.path)
            params = {k: values[0] for k, values in parse_qs(parts.query).items()}
            evaluation = params.get('evaluation') == 'true'
            path = parts.path
            try:
                if path == '/api/bootstrap':
                    return self.reply({'csrf': csrf, 'providers': DEFAULTS, 'pricing': price_catalog(), 'default_max_output_tokens': DEFAULT_MAX_OUTPUT_TOKENS, 'variants': variants(), 'modes': list(MODES),
                                       'summary_db_available': app.summaries_db.is_file(),
                                       'benchmark_available': (app.benchmark_dir / 'selected_pages.jsonl').is_file()})
                if path == '/api/catalog':
                    return self.reply(app.catalog(evaluation))
                if path == '/api/pricing':
                    return self.reply(price_lookup(params))
                if path == '/api/generations':
                    return self.reply(app.generations(params['page'], evaluation))
                if path == '/api/case':
                    return self.reply(self.case_access(params['id'], evaluation))
                if path == '/api/run':
                    return self.reply(self.run_access(params['id'], evaluation))
                if path == '/api/runs':
                    limit = int(params.get('limit', 100))
                    if not 1 <= limit <= 200:
                        raise ValueError('Run page size must be between 1 and 200')
                    rows = app.store.list_runs(params.get('experiment'), limit=limit,
                                               before=int(params['before']) if params.get('before') else None)
                    visible = []
                    for row in rows:
                        try:
                            self.case_access(row['case_id'], evaluation)
                        except ValueError:
                            continue
                        row.pop('config_json', None)
                        visible.append(row)
                    return self.reply(visible)
                if path == '/api/experiments':
                    return self.reply([e for e in app.store.list_experiments() if evaluation or not e['evaluation']])
                if path == '/api/campaigns':
                    return self.reply(list_campaigns(app.store, evaluation))
                if path == '/api/campaign':
                    self.experiment_access(params['id'], evaluation)
                    return self.reply(corpus_status(app.store, params['id']))
                if path == '/api/progress':
                    plan = self.experiment_access(params['experiment'], evaluation)
                    if plan.get('kind') == 'corpus':
                        return self.reply(corpus_status(app.store, params['experiment']))
                    return self.reply(progress.get(params['experiment'], {'state': 'idle'}))
                if path == '/api/image':
                    case = self.case_access(params['case'], evaluation)
                    info = case['snapshot']['facsimile']
                    image_path = Path(info.get('archived_path', '')).resolve()
                    if not image_path.is_relative_to(app.store.assets.resolve()) or not image_path.is_file():
                        raise ValueError('No archived image for this case')
                    blob = image_path.read_bytes()
                    if digest(blob) != info['sha256']:
                        raise ValueError('Archived image hash mismatch')
                    return self.reply(blob, content_type=mimetypes.guess_type(image_path.name)[0] or 'application/octet-stream')
                if path in {'/api/report', '/api/export'}:
                    exp = params['experiment']
                    plan = self.experiment_access(exp, evaluation)
                    fmt = params.get('format', 'json')
                    if plan.get('kind') == 'corpus':
                        data = corpus_status(app.store, exp)
                        if path == '/api/export':
                            raise ValueError('Use CLI export for corpus runs; monitor data is available at /api/campaign')
                        if fmt == 'markdown':
                            return self.reply('# ' + exp + '\n\n' + '\n'.join(f'- {k}: {v}' for k,v in data['states'].items()),
                                              content_type='text/markdown; charset=utf-8', filename=exp + '.md')
                        if fmt == 'csv':
                            return self.reply('status,pages\n' + ''.join(f'{k},{v}\n' for k,v in data['states'].items()),
                                              content_type='text/csv; charset=utf-8', filename=exp + '.csv')
                        return self.reply(data)
                    if path == '/api/export':
                        return self.reply(''.join(export_jsonl(app.store, exp)), content_type='application/x-ndjson', filename=exp + '.jsonl')
                    data = report(app.store, exp)
                    if fmt == 'markdown':
                        return self.reply(markdown(data), content_type='text/markdown; charset=utf-8', filename=exp + '.md')
                    if fmt == 'csv':
                        return self.reply(csv_report(data), content_type='text/csv; charset=utf-8', filename=exp + '.csv')
                    return self.reply(data)
                static_names = {'/': 'index.html', '/playground.js': 'playground.js', '/playground.css': 'playground.css', '/favicon.svg': 'favicon.svg'}
                if path in static_names:
                    file = STATIC / static_names[path]
                    return self.reply(file.read_bytes(), content_type=(mimetypes.guess_type(file.name)[0] or 'text/plain') + '; charset=utf-8')
                return self.reply({'error': 'Not found'}, 404)
            except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                return self.reply({'error': str(exc)}, 400)

        def do_POST(self):
            if not self.allowed_origin() or not secrets.compare_digest(self.headers.get('X-KG-CSRF', ''), csrf):
                return self.reply({'error': 'Local origin and session token required'}, 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply({'error': 'JSON body required'}, 415)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 5_000_000:
                    return self.reply({'error': 'Request size outside allowed range'}, 413)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSON body must be an object')
                evaluation = data.get('evaluation') is True
                path = urlsplit(self.path).path
                if path == '/api/load':
                    allowed = {'page', 'generation_id', 'run_id', 'benchmark_id', 'demo', 'evaluation', 'allow_source_drift'}
                    if set(data) - allowed:
                        raise ValueError('Unknown page selection setting')
                    return self.reply(app.load(**data))
                if path == '/api/preview':
                    cases = [self.case_access(i, evaluation) for i in data['case_ids']]
                    exp = plan_experiment(app.store, cases, data['configs'], variants=data.get('variants', ['concepts-v1']),
                                          modes=data.get('modes', ['concepts_relations']), facsimiles=data.get('facsimiles', ['off']),
                                          repetitions=data.get('repetitions', 1), name=data.get('name', 'Browser experiment'),
                                          evaluation=evaluation, bypass_cache=data.get('bypass_cache', False),
                                          system_override=data.get('system_override'))
                    runs = app.store.list_runs(exp)
                    return self.reply({'experiment': exp, 'runs': runs,
                                       'previews': [{'run_id': r['id'], 'prepared': app.store.get_run(r['id'])['prepared']} for r in runs]})
                if path == '/api/execute':
                    exp = data['experiment']
                    plan = self.experiment_access(exp, evaluation)
                    if plan.get('kind') == 'corpus':
                        raise ValueError('Start/resume this campaign using corpus-run --campaign in the CLI; the browser monitors it')
                    if data.get('execute') is not True:
                        raise ValueError('Explicit execution action required')
                    concurrency = data.get('concurrency', 1)
                    if type(concurrency) is not int or not 1 <= concurrency <= 16:
                        raise ValueError('Concurrency must be 1–16')
                    if not execution_lock.acquire(blocking=False):
                        raise ValueError('Another experiment is active; wait before starting a new batch')
                    progress[exp] = {'state': 'running', 'completed': 0}
                    def worker():
                        try:
                            def update(event):
                                progress[exp] = dict(event, state='running')
                            execute_experiment(app.store, exp, concurrency=concurrency, resume=data.get('resume', False),
                                               retry_errors=data.get('retry_errors', False), progress=update)
                            progress[exp] = dict(progress[exp], state='finished')
                        except Exception as exc:
                            progress[exp] = {'state': 'error', 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
                        finally:
                            execution_lock.release()
                    threading.Thread(target=worker, daemon=True).start()
                    return self.reply({'experiment': exp, 'state': 'running'}, 202)
                if path == '/api/compare':
                    for identifier in data['runs']:
                        self.run_access(identifier, evaluation)
                    return self.reply(compare_runs(app.store, data['runs']))
                if path == '/api/review':
                    self.run_access(data['run_id'], evaluation)
                    identifier = app.store.review(data['run_id'], data['object_id'], data['decision'],
                                                  data.get('dimension', 'overall'), data.get('comment', ''), data.get('reviewer', 'local'))
                    return self.reply({'review_id': identifier})
                if path == '/api/validate':
                    run = self.run_access(data['run_id'], evaluation)
                    case = self.case_access(run['case_id'], evaluation)
                    validation = evaluate_response(case['snapshot'], run['result'] or {}, run['prepared'])
                    app.store.add_validation(run['id'], validation)
                    return self.reply(validation)
                return self.reply({'error': 'Not found'}, 404)
            except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                return self.reply({'error': str(exc)}, 400)

    return ThreadingHTTPServer((host, port), Handler)


def serve(app, host='127.0.0.1', port=8766):
    server = make_server(app, host, port)
    print(f'KG Playground ready: http://{host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
