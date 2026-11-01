"""Bounded spawn-based process pool over the durable corpus queue."""
from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import signal
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy

from .common import now
from .corpus_queue import (campaign_plan, discover, finish_job, recover, status,
                           requeue_source_drift_errors, set_source_policy)
from .experiment_runner import execute_run, prepare
from .providers import redact
from .service import Playground

_WORKER = None


def lock_paths(store, campaign):
    directory = store.path.parent / (store.path.stem + '_locks')
    directory.mkdir(exist_ok=True)
    return directory / (campaign + '.lock'), directory / (campaign + '.workers.lock')


def init_worker(db, campaign, owner):
    global _WORKER
    # Parent handles Ctrl-C by draining in-flight work before releasing its lock.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from .storage import Store
    plan = campaign_plan(Store(db), campaign)
    app = Playground(db=db, **plan['paths'])
    _WORKER = (app, campaign, owner, plan)


def process_job(job_id, dry_run=False):
    app, campaign, owner, plan = _WORKER
    store = app.store
    _, worker_lock = lock_paths(store, campaign)
    # Surviving children keep this lock after a coordinator crash. A new coordinator
    # cannot recover their jobs while they are still sending requests.
    with worker_lock.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        with store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT owner FROM corpus_campaigns WHERE id=?', (campaign,)).fetchone()[0] != owner:
                return {'job_id': job_id, 'status': 'stale_worker'}
            row = con.execute('SELECT * FROM corpus_jobs WHERE id=? AND campaign_id=?', (job_id, campaign)).fetchone()
            if row is None or row['status'] != 'queued':
                return {'job_id': job_id, 'status': 'already_claimed'}
            job = dict(row)
            attempt = job['attempts'] + 1
            con.execute("UPDATE corpus_jobs SET status='preparing',attempts=?,updated_at=? WHERE id=?",
                        (attempt, now(), job_id))
            con.execute('INSERT INTO corpus_attempts(job_id,number,worker_pid,status,started_at) VALUES(?,?,?,?,?)',
                        (job_id, attempt, os.getpid(), 'preparing', now()))
        try:
            if job['run_id']:
                old = store.get_run(job['run_id'])
                if old['status'] == 'planned':
                    run_id = old['id']
                elif old['status'] in {'completed', 'cached', 'invalid'}:
                    finish_job(store, job_id, attempt, old['status'])
                    return {'job_id': job_id, 'run_id': old['id'], 'status': old['status']}
                else:
                    if old['status'] == 'running':
                        store.finish(old['id'], 'interrupted', {'error': 'Recovered interrupted attempt; remote outcome unknown',
                                                               'usage': {}, 'cost_usd': None})
                    prepared = deepcopy(old['prepared'])
                    prepared['retry_of'] = old['id']
                    run_id = store.add_run(campaign, store.get_case(old['case_id']), prepared, 1, queue_job=job_id)
            else:
                case = app.load(page=f"{job['documento']}:{job['pagina_num']}",
                                generation_id=job['generation_id'], evaluation=plan['evaluation'],
                                allow_source_drift=plan['source_policy']['allow_source_drift'])
                generation = json.loads(job['generation_json'])
                source = case['snapshot']['source']
                if source['v2_output_hash'] != generation['output_hash'] or source['v2_source_hash'] != generation['source_hash']:
                    raise ValueError('Generation changed since discovery; create a new campaign after inspection')
                template = plan['template']
                prepared = prepare(case, plan['config'], variant=template['variant']['id'], mode=template['mode'],
                                   facsimile=plan['facsimile'], template=template)
                prepared['corpus_selection'] = generation
                prepared['corpus_source_policy'] = plan['source_policy']
                run_id = store.add_run(campaign, case, prepared, 1, queue_job=job_id)
            if dry_run:
                state, error = 'prepared', None
            else:
                with store.connect() as con:
                    con.execute("UPDATE corpus_jobs SET status='running',updated_at=? WHERE id=?", (now(), job_id))
                result = execute_run(store, run_id, bypass_cache=plan['bypass_cache'])
                state, error = result['status'], (result['result'] or {}).get('error')
            finish_job(store, job_id, attempt, state, error)
            return {'job_id': job_id, 'run_id': run_id, 'status': state, 'worker_pid': os.getpid()}
        except Exception as exc:
            error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
            error = redact(error, plan['config'])
            finish_job(store, job_id, attempt, 'source_error', error)
            return {'job_id': job_id, 'status': 'source_error', 'error': error, 'worker_pid': os.getpid()}


def run_campaign(store, campaign, *, workers=2, dry_run=False, follow=False,
                 poll_interval=60, retry_errors=False, max_jobs=None, progress=None, allow_source_drift=None):
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError('Workers must be between 1 and 32')
    if type(poll_interval) is not int or not 1 <= poll_interval <= 86400:
        raise ValueError('Invalid poll interval')
    if max_jobs is not None and (type(max_jobs) is not int or max_jobs < 1):
        raise ValueError('max_jobs must be positive')
    if allow_source_drift is not None and type(allow_source_drift) is not bool:
        raise ValueError('allow_source_drift must be boolean')
    plan = campaign_plan(store, campaign)
    coordinator_path, worker_path = lock_paths(store, campaign)
    stop = threading.Event()
    handlers = {}
    emit = progress or (lambda event: None)
    with coordinator_path.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Campaign is already executing') from None
        with worker_path.open('a') as worker_lock:
            try:
                fcntl.flock(worker_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Previous campaign workers are still active; retry after they finish') from None
            if allow_source_drift is not None:
                set_source_policy(store, campaign, allow_source_drift)
            plan = campaign_plan(store, campaign)
            recover(store, campaign, retry_errors=retry_errors)
            requeued = requeue_source_drift_errors(store, campaign)
            if requeued:
                emit({'campaign': campaign, 'requeued_source_drift': requeued})
            owner = uuid.uuid4().hex
            with store.connect() as con:
                con.execute('UPDATE corpus_campaigns SET owner=? WHERE id=?', (owner, campaign))
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, lambda *_: stop.set())
        submitted, finished = 0, 0
        try:
            emit(dict(campaign=campaign, **discover(store, campaign, stop=stop.is_set)))
            next_scan = time.monotonic() + poll_interval
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                     initializer=init_worker, initargs=(str(store.path), campaign, owner)) as pool:
                futures = {}
                while True:
                    if follow and not stop.is_set() and time.monotonic() >= next_scan:
                        emit(dict(campaign=campaign, **discover(store, campaign, stop=stop.is_set)))
                        next_scan = time.monotonic() + poll_interval
                    slots = workers - len(futures)
                    if max_jobs is not None:
                        slots = min(slots, max_jobs - submitted)
                    if slots > 0 and not stop.is_set():
                        with store.connect() as con:
                            eligible = "('pending')" if dry_run else "('pending','prepared')"
                            rows = con.execute(f'''SELECT id FROM corpus_jobs WHERE campaign_id=?
                                AND status IN {eligible} ORDER BY id LIMIT ?''', (campaign, slots)).fetchall()
                            for row in rows:
                                con.execute("UPDATE corpus_jobs SET status='queued',updated_at=? WHERE id=?", (now(), row['id']))
                        for row in rows:
                            futures[pool.submit(process_job, row['id'], dry_run)] = row['id']
                            submitted += 1
                    if not futures:
                        if stop.is_set() or not follow or (max_jobs is not None and submitted >= max_jobs):
                            break
                        if plan['max_pages'] is not None and status(store, campaign, limit=0)['total_pages'] >= plan['max_pages']:
                            break
                        stop.wait(min(1, max(0, next_scan - time.monotonic())))
                        continue
                    done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.pop(future)
                        event = future.result()
                        finished += 1
                        emit(dict(campaign=campaign, finished=finished, submitted=submitted, **event))
        except BrokenProcessPool:
            raise ValueError('A corpus worker exited unexpectedly. The queue is saved; run corpus-run again to recover interrupted jobs.') from None
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
    return dict(status(store, campaign), stopped=stop.is_set(), submitted=submitted, finished=finished)
