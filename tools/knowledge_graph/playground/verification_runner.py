"""Explicit, bounded and resumable execution of frozen review calls."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy

from .common import digest
from .providers import ProviderError, estimate_cost, provider_error_message, redact, send
from .verification_contract import evaluate_review
from .verification_study import check_prepared, verification_policy


def execute_review(store, identifier, *, caller=send):
    run = store.get_run(identifier)
    p, config = run['prepared'], run['config']
    case = store.get_case(run['case_id'])
    store.start(identifier)
    started = time.perf_counter()
    result = None
    try:
        result = caller(config, p['request'])
        result.update(cost_usd=estimate_cost(result['usage'], config['prices']),
                      inference_performed=True, facsimile_attached=p['facsimile_selected'])
        validation = evaluate_review(case['snapshot'], result, p)
        result['parsed_json'] = validation['parsed']
        result['derived_extraction'] = validation['derived_extraction']
        result['semantic_status'] = 'pending_independent_review'
        store.finish(identifier, 'completed' if validation['valid'] else 'invalid', result, validation)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ProviderError, ValueError)) else type(exc).__name__
        # If local validation fails unexpectedly, retain the received raw response and its usage.
        result = result or {'raw_response': '', 'usage': {}, 'cost_usd': None,
                            'provider_response': getattr(exc, 'response', ''),
                            'attempts': getattr(exc, 'attempts', 0), 'retry_log': getattr(exc, 'retry_log', []),
                            'latency_seconds': time.perf_counter() - started, 'inference_performed': None}
        result['error'] = redact(message, config)
        store.finish(identifier, 'error', result)
    return store.get_run(identifier)


def error_summary(store, experiment):
    """Include stored causes even when resuming submits no new calls."""
    grouped = {}
    with store.connect() as con:
        rows = con.execute("SELECT id,result_json,config_json FROM runs WHERE experiment_id=? AND status='error' ORDER BY id",
                           (experiment,))
        for row in rows:
            result, config = json.loads(row['result_json'] or '{}'), json.loads(row['config_json'])
            message = provider_error_message(result.get('error') or 'Unknown error',
                                             result.get('provider_response'), config)
            item = grouped.setdefault(message, {'message': message, 'count': 0, 'example_run_ids': []})
            item['count'] += 1
            if len(item['example_run_ids']) < 5:
                item['example_run_ids'].append(row['id'])
    return list(grouped.values())


def execute_verification(store, experiment, *, concurrency=2, max_calls=None, retry_interrupted=False,
                         progress=None, caller=send, stop_event=None):
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError('Concurrency must be a positive integer')
    if max_calls is not None and (type(max_calls) is not int or max_calls < 1):
        raise ValueError('max-calls must be positive')
    plan = store.experiment_plan(experiment)
    if plan.get('kind') != 'verification':
        raise ValueError('verify-run requires a prepared verification experiment')
    if plan['policy'] != verification_policy():
        raise ValueError('Frozen validation implementation changed; prepare a new experiment')
    tasks = plan['schedule']
    if (len(tasks) != plan['size'] or plan['size'] != 24 or len(set(plan['pages'])) != 12
            or {t['schedule_index'] for t in tasks} != set(range(24))
            or len({t['source_run_id'] for t in tasks}) != 24
            or Counter(t['page'] for t in tasks) != dict.fromkeys(plan['pages'], 2)):
        raise ValueError('Invalid frozen verification schedule')
    if digest(store.experiment_plan(plan['source_experiment'])) != plan['source_plan_hash']:
        raise ValueError('Source experiment plan changed')
    by_source = {t['source_run_id']: t for t in tasks}
    lock_dir = store.path.parent / (store.path.stem + '_locks')
    lock_dir.mkdir(exist_ok=True)
    stop = stop_event or threading.Event()
    submitted = finished = 0
    handlers = {}
    with (lock_dir / (experiment + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Experiment is already executing') from None
        rows = store.list_runs(experiment)
        # Validate before changing even this new experiment's interruption state.
        for row in rows:
            if row['status'] in ('planned', 'running', 'interrupted'):
                run = store.get_run(row['id'])
                source_id = run['prepared'].get('source_run_id')
                if source_id not in by_source:
                    raise ValueError('Unknown source run in prepared task')
                check_prepared(store, run, plan, by_source[source_id])
        retried = store.retried_runs(experiment)
        for row in rows:
            if row['status'] == 'running':
                store.finish(row['id'], 'interrupted', {'error': 'Remote outcome unknown after interruption',
                                                       'usage': {}, 'cost_usd': None})
            if retry_interrupted and row['status'] in ('running', 'interrupted') and row['id'] not in retried:
                run = store.get_run(row['id'])
                p = deepcopy(run['prepared'])
                p['retry_of'] = row['id']
                store.add_run(experiment, store.get_case(row['case_id']), p, row['repetition'])
        pending = []
        for row in store.list_runs(experiment):
            if row['status'] == 'planned':
                p = store.get_run(row['id'])['prepared']
                pending.append((by_source[p['source_run_id']]['schedule_index'], row['id']))
        pending.sort()
        if max_calls is not None:
            pending = pending[:max_calls]
        if pending and caller is send and not os.environ.get('META_API_KEY'):
            raise ValueError('Set META_API_KEY before explicitly executing verify-run')
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(pending)))) as pool:
                active, iterator, exhausted = {}, iter(pending), False
                while active or not exhausted:
                    while not exhausted and not stop.is_set() and len(active) < concurrency:
                        task = next(iterator, None)
                        if task is None:
                            exhausted = True
                            break
                        index, identifier = task
                        active[pool.submit(execute_review, store, identifier, caller=caller)] = index
                        submitted += 1
                    if stop.is_set():
                        exhausted = True
                    if not active:
                        break
                    done, _ = wait(active, timeout=.25, return_when=FIRST_COMPLETED)
                    for future in done:
                        index = active.pop(future)
                        result = future.result()
                        finished += 1
                        if progress:
                            update = dict(run_id=result['id'], schedule_index=index, status=result['status'],
                                          submitted=submitted, finished=finished, stopping=stop.is_set())
                            if result['status'] == 'error':
                                output = result['result'] or {}
                                update['error'] = provider_error_message(output.get('error') or 'Unknown error',
                                                                        output.get('provider_response'), result['config'])
                            progress(update)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return dict(experiment=experiment, submitted=submitted, finished=finished, stopped=stop.is_set(),
                states=dict(Counter(r['status'] for r in store.list_runs(experiment))),
                errors=error_summary(store, experiment),
                interruption_note='Unknown hard-kill outcomes require --retry-interrupted for another paid attempt.')
