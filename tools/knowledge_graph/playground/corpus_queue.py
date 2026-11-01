"""Persistent, page-deduplicated corpus campaigns. Production SQLite stays read-only."""
from __future__ import annotations

import fcntl
import re
import uuid
from contextlib import closing

from .common import dumps, now, readonly
from .prompt_registry import freeze_template
from .providers import normalize_config
from .snapshot_v2 import SOURCE_DRIFT_ERROR, benchmark_records, check_schema

DDL = '''
CREATE TABLE IF NOT EXISTS corpus_campaigns (
 id TEXT PRIMARY KEY REFERENCES experiments(id), owner TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corpus_jobs (
 id INTEGER PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES corpus_campaigns(id),
 documento TEXT NOT NULL, pagina_num INTEGER NOT NULL, generation_id INTEGER NOT NULL,
 generation_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 run_id INTEGER REFERENCES runs(id), attempts INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, UNIQUE(campaign_id,documento,pagina_num));
CREATE INDEX IF NOT EXISTS corpus_job_state ON corpus_jobs(campaign_id,status,id);
CREATE TABLE IF NOT EXISTS corpus_attempts (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES corpus_jobs(id),
 number INTEGER NOT NULL, run_id INTEGER REFERENCES runs(id), worker_pid INTEGER,
 status TEXT NOT NULL, error TEXT, started_at TEXT NOT NULL, finished_at TEXT,
 UNIQUE(job_id,number));
CREATE TABLE IF NOT EXISTS corpus_source_policies (
 id INTEGER PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES corpus_campaigns(id),
 allow_source_drift INTEGER NOT NULL CHECK(allow_source_drift IN (0,1)),
 reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS corpus_policy_latest ON corpus_source_policies(campaign_id,id);
'''


def init_queue(store):
    with store.connect() as con:
        con.executescript(DDL)


def create_campaign(app, config, *, variant='concepts-v2.2-confidence', mode='concepts_relations',
                    facsimile='off', volumes=(), collections=(), max_pages=None,
                    page_from=1, page_to=None, evaluation=False, name='KG corpus', bypass_cache=False,
                    allow_source_drift=False):
    config = normalize_config(config)
    if config['provider'] == 'fixture':
        raise ValueError('Fixture replay is not allowed for corpus campaigns')
    if type(allow_source_drift) is not bool:
        raise ValueError('allow_source_drift must be boolean')
    if facsimile not in {'off', 'on', 'auto'}:
        raise ValueError('Unknown facsimile policy')
    if type(page_from) is not int or page_from < 1 or (page_to is not None and (type(page_to) is not int or page_to < page_from)):
        raise ValueError('Invalid physical page range')
    if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
        raise ValueError('max_pages must be positive')
    volumes, collections = sorted(set(volumes)), sorted(set(collections))
    if any(not re.fullmatch(r'[A-Z]+\d+(?:\.\d+)?', v) for v in volumes):
        raise ValueError('Invalid volume')
    if any(not re.fullmatch(r'[A-Z]+', c) for c in collections):
        raise ValueError('Invalid collection')
    with closing(readonly(app.summaries_db)) as con:
        check_schema(con)
    plan = {'kind': 'corpus', 'version': 'kg-corpus-1', 'config': config,
            'template': freeze_template(variant, mode), 'facsimile': facsimile,
            'volumes': volumes, 'collections': collections, 'max_pages': max_pages,
            'page_from': page_from, 'page_to': page_to, 'evaluation': evaluation,
            'bypass_cache': bypass_cache, 'allow_source_drift': allow_source_drift,
            'paths': {k: str(getattr(app, k).resolve()) for k in
                      ('summaries_db', 'indices_db', 'corpus_root', 'benchmark_dir', 'ocr_versions_db')}}
    init_queue(app.store)
    identifier = 'EXP-' + uuid.uuid4().hex[:12]
    with app.store.connect() as con:
        con.execute('INSERT INTO experiments VALUES(?,?,?,?,?)',
                    (identifier, name, now(), evaluation, dumps(plan)))
        con.execute('INSERT INTO corpus_campaigns VALUES(?,NULL,?)', (identifier, now()))
    return identifier


def campaign_plan(store, campaign):
    plan = store.experiment_plan(campaign)
    if plan.get('kind') != 'corpus':
        raise ValueError('Not a corpus campaign')
    policy = {'allow_source_drift': plan.get('allow_source_drift', False),
              'origin': 'original_campaign_plan', 'revision_id': None}
    with store.connect() as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_source_policies'").fetchone():
            row = con.execute('SELECT id,allow_source_drift,reason,created_at FROM corpus_source_policies '
                              'WHERE campaign_id=? ORDER BY id DESC LIMIT 1', (campaign,)).fetchone()
            if row:
                policy = dict(row, allow_source_drift=bool(row['allow_source_drift']),
                              origin='campaign_policy_revision', revision_id=row['id'])
    plan['source_policy'] = policy
    return plan


def set_source_policy(store, campaign, allow_source_drift, *, reason='Explicit CLI source policy'):
    """Append a policy revision; existing plans, snapshots and runs remain unchanged."""
    if type(allow_source_drift) is not bool or not isinstance(reason, str) or not reason.strip():
        raise ValueError('Source policy requires a boolean and a reason')
    campaign_plan(store, campaign)
    init_queue(store)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT id,allow_source_drift FROM corpus_source_policies '
                          'WHERE campaign_id=? ORDER BY id DESC LIMIT 1', (campaign,)).fetchone()
        if row and bool(row['allow_source_drift']) == allow_source_drift:
            return row['id']
        return con.execute('INSERT INTO corpus_source_policies(campaign_id,allow_source_drift,reason,created_at) '
                           'VALUES(?,?,?,?)', (campaign, allow_source_drift, reason, now())).lastrowid


def requeue_source_drift_errors(store, campaign):
    if not campaign_plan(store, campaign)['source_policy']['allow_source_drift']:
        return 0
    with store.connect() as con:
        return con.execute('''UPDATE corpus_jobs AS j SET status='pending',updated_at=?
            WHERE campaign_id=? AND status='source_error' AND run_id IS NULL
            AND EXISTS(SELECT 1 FROM corpus_attempts a WHERE a.job_id=j.id AND a.number=j.attempts AND a.error=?)''',
                           (now(), campaign, SOURCE_DRIFT_ERROR)).rowcount


def discover(store, campaign, *, chunk_size=500, stop=lambda: False):
    """Keyset pagination of metadata only; existing jobs retain their generation."""
    if type(chunk_size) is not int or not 1 <= chunk_size <= 2000:
        raise ValueError('Discovery chunk size must be between 1 and 2000')
    plan = campaign_plan(store, campaign)
    blocked = set() if plan['evaluation'] else {
        r['source']['documento'] for r in benchmark_records(plan['paths']['benchmark_dir'])
        if r['split'] == 'blind_holdout'}
    volume, after_volume, after_page = None, '', 0
    added, excluded, ambiguous = 0, 0, 0
    with store.connect() as con:
        total = con.execute('SELECT count(*) FROM corpus_jobs WHERE campaign_id=?', (campaign,)).fetchone()[0]
    while not stop() and (plan['max_pages'] is None or total < plan['max_pages']):
        conditions = ["status IN ('valid','metadata_pending')", 'tainted_by_page IS NULL',
                      'is_current IN (0,1)', 'pagina_num >= ?']
        params = [plan['page_from']]
        if plan['page_to'] is not None:
            conditions.append('pagina_num <= ?'); params.append(plan['page_to'])
        if plan['volumes']:
            conditions.append('documento IN (' + ','.join('?' for _ in plan['volumes']) + ')')
            params.extend(plan['volumes'])
        if plan['collections']:
            conditions.append('(' + ' OR '.join('documento GLOB ?' for _ in plan['collections']) + ')')
            params.extend(c + '[0-9]*' for c in plan['collections'])
        with closing(readonly(plan['paths']['summaries_db'])) as con:
            check_schema(con)
            if volume is None:
                next_volume = con.execute('SELECT documento FROM resumo_generations WHERE ' +
                                          ' AND '.join(conditions) + ' AND documento>? '
                                          'GROUP BY documento ORDER BY documento LIMIT 1', [*params, after_volume]).fetchone()
                if next_volume is None:
                    break
                volume, after_page = next_volume[0], 0
            # Existing production index starts with documento,status,pagina_num.
            # Restrict the GROUP BY to one volume instead of repeatedly sorting
            # every remaining volume for each 500-page chunk. No production DDL.
            query = '''WITH selected AS (
                SELECT documento,pagina_num,
                       COALESCE(MAX(CASE WHEN is_current=1 THEN id END),MAX(id)) AS chosen,
                       SUM(CASE WHEN is_current=1 THEN 1 ELSE 0 END) AS current_count
                FROM resumo_generations WHERE ''' + ' AND '.join(conditions) + '''
                AND documento=? AND pagina_num>?
                GROUP BY documento,pagina_num ORDER BY documento,pagina_num LIMIT ?)
                SELECT g.id,g.documento,g.pagina_num,g.is_current,g.status,g.output_hash,g.source_hash,
                       s.current_count FROM selected s JOIN resumo_generations g ON g.id=s.chosen
                ORDER BY g.documento,g.pagina_num'''
            rows = con.execute(query, [*params, volume, after_page, chunk_size]).fetchall()
        if not rows:
            after_volume, volume = volume, None
            continue
        with store.connect() as con:
            for row in rows:
                row = dict(row)
                after_page = row['pagina_num']
                if row['documento'] in blocked:
                    excluded += 1
                    continue
                if row['current_count'] > 1:
                    ambiguous += 1
                    continue
                changed = con.execute('''INSERT OR IGNORE INTO corpus_jobs
                    (campaign_id,documento,pagina_num,generation_id,generation_json,updated_at)
                    VALUES(?,?,?,?,?,?)''', (campaign, row['documento'], row['pagina_num'], row['id'], dumps(row), now())).rowcount
                total += changed
                added += changed
                if plan['max_pages'] is not None and total >= plan['max_pages']:
                    break
    return {'discovered': added, 'total_pages': total, 'excluded_holdout': excluded,
            'ambiguous_current': ambiguous}


def status(store, campaign, *, limit=20):
    plan = campaign_plan(store, campaign)
    with store.connect() as con:
        states = {r[0]: r[1] for r in con.execute(
            'SELECT status,count(*) FROM corpus_jobs WHERE campaign_id=? GROUP BY status', (campaign,))}
        recent = [dict(r) for r in con.execute('''SELECT id,documento,pagina_num,generation_id,status,run_id,attempts
            FROM corpus_jobs WHERE campaign_id=? ORDER BY updated_at DESC,id DESC LIMIT ?''', (campaign, limit))]
        attempts = [dict(r) for r in con.execute('''SELECT a.* FROM corpus_attempts a
            JOIN corpus_jobs j ON j.id=a.job_id WHERE j.campaign_id=? ORDER BY a.id DESC LIMIT ?''', (campaign, limit))]
        journal = con.execute('PRAGMA journal_mode').fetchone()[0]
    coordinator_active = False
    lock_path = store.path.parent / (store.path.stem + '_locks') / (campaign + '.lock')
    if lock_path.exists():
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                coordinator_active = True
    return {'campaign': campaign, 'total_pages': sum(states.values()), 'states': states,
            'recent_jobs': recent, 'recent_attempts': attempts, 'journal_mode': journal,
            'coordinator_active': coordinator_active, 'source_policy': plan['source_policy']}


def list_campaigns(store, evaluation=False):
    with store.connect() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_campaigns'").fetchone():
            return []
        return [dict(r) for r in con.execute('''SELECT e.id,e.name,e.created_at,e.evaluation
            FROM corpus_campaigns c JOIN experiments e ON e.id=c.id
            WHERE (? OR e.evaluation=0) ORDER BY e.created_at DESC LIMIT 100''', (evaluation,))]


def recover(store, campaign, *, retry_errors=False):
    """Caller holds coordinator and worker locks. Reconcile committed outcomes first."""
    cursor = 0
    while True:
        with store.connect() as con:
            rows = con.execute('''SELECT j.id,j.run_id,r.status AS run_status FROM corpus_jobs j
                LEFT JOIN runs r ON r.id=j.run_id WHERE j.campaign_id=? AND j.id>?
                AND j.status IN ('queued','preparing','running') ORDER BY j.id LIMIT 100''',
                               (campaign, cursor)).fetchall()
        if not rows:
            break
        for row in rows:
            cursor = row['id']
            terminal = row['run_status'] in {'completed', 'cached', 'invalid', 'error'}
            next_state = row['run_status'] if terminal else 'pending'
            if row['run_status'] == 'running':
                store.finish(row['run_id'], 'interrupted', {
                    'error': 'Recovered interrupted process; remote outcome unknown',
                    'usage': {}, 'cost_usd': None})
            with store.connect() as con:
                con.execute('UPDATE corpus_jobs SET status=?,updated_at=? WHERE id=?', (next_state, now(), row['id']))
                con.execute("UPDATE corpus_attempts SET status=?,finished_at=? WHERE job_id=? AND finished_at IS NULL",
                            (next_state if terminal else 'interrupted', now(), row['id']))
    if retry_errors:
        with store.connect() as con:
            con.execute("UPDATE corpus_jobs SET status='pending',updated_at=? WHERE campaign_id=? AND status IN ('error','source_error')",
                        (now(), campaign))


def finish_job(store, job_id, attempt, state, error=None):
    with store.connect() as con:
        con.execute('UPDATE corpus_jobs SET status=?,updated_at=? WHERE id=?', (state, now(), job_id))
        con.execute('''UPDATE corpus_attempts SET status=?,error=?,finished_at=?,
            run_id=(SELECT run_id FROM corpus_jobs WHERE id=?) WHERE job_id=? AND number=?''',
                    (state, error, now(), job_id, job_id, attempt))
