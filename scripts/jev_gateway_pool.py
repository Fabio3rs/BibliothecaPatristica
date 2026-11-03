"""Bounded process pool: parent owns budgets, retries, audit and database writes.

At most `workers` associations are active (running or in retry backoff). Each
submission reserves ONE HTTP attempt. Workers never retry, open SQLite or logs.
The parent drains dispatched work after budget exhaustion, timeout or Ctrl+C.
"""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import multiprocessing
import os
import signal
import threading
import time

import requests

from scripts.jev_gateway_http import request_once

_SESSION = None
_CONFIG = None


def initialize_worker(api_key, endpoint, timeout, deadline):
    global _SESSION, _CONFIG
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _SESSION = requests.Session()
    _CONFIG = (api_key, endpoint, timeout, deadline)


def evaluate_once(payload, call_number):
    key, endpoint, timeout, deadline = _CONFIG
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {'call_number': call_number, 'attempted': False, 'error': 'time_budget', 'latency_ms': 0}
    attempt = request_once(_SESSION, endpoint, key, payload, min(timeout, remaining))
    attempt['call_number'] = call_number
    attempt['worker_pid'] = os.getpid()
    return attempt


def run_pool(items, args, gateway, endpoint, finish):
    """Consume prepared states; call finish(state) exactly once, in the parent.

`gateway.calls` includes reservations while running and actual/uncertain attempts
on return. `submitted` is monotonic, so expired reservations never reuse an ID.
Input iteration and finish can access the corpus/DB; neither runs in a child.
"""
    pending, retries = {}, []
    iterator = iter(items)
    exhausted = False
    submitted = 0
    max_in_flight = 0
    previous_handler = None
    pool = None

    def request_stop(_signum, _frame):
        gateway.stop_reason = 'interrupted'
        print('Interruption requested: draining in-flight Jev requests and saving results.', flush=True)

    def reason():
        if gateway.stop_reason:
            return gateway.stop_reason
        if time.monotonic() >= gateway.deadline:
            return 'time_budget'
        if gateway.calls >= args.limit:
            return 'limit'
        return None

    def send(state):
        nonlocal submitted, pool, max_in_flight
        if pool is None:
            # Spawn avoids inheriting the potentially large parsed-report corpus.
            pool = ProcessPoolExecutor(max_workers=args.workers,
                mp_context=multiprocessing.get_context('spawn'),
                initializer=initialize_worker,
                initargs=(gateway.api_key, endpoint, args.timeout, gateway.deadline))
        submitted += 1
        try:
            future = pool.submit(evaluate_once, state['payload'], submitted)
        except Exception:
            gateway.stop_reason = gateway.stop_reason or 'worker_error'
            state['attempts'].append({'call_number': submitted, 'attempted': False,
                                      'error': 'worker_error', 'latency_ms': 0})
            finish(state)
            return
        gateway.calls += 1
        pending[future] = (state, submitted)
        max_in_flight = max(max_in_flight, len(pending))

    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGINT, request_stop)
    try:
        while True:
            stop = reason()
            if stop:
                # Retried requests not yet dispatched do not consume the quota.
                waiting, retries = retries, []
                for _, state in waiting:
                    state['attempts'][-1]['retry_not_sent_reason'] = stop
                    finish(state)
            else:
                now = time.monotonic()
                ready = [r for r in retries if r[0] <= now]
                retries = [r for r in retries if r[0] > now]
                for due, state in ready:
                    if reason():
                        retries.append((due, state))
                    else:
                        send(state)
                while not exhausted and not reason() and len(pending) + len(retries) < args.workers:
                    state = next(iterator, None)
                    if state is None:
                        exhausted = True
                        break
                    # Preparation/cached application may take us past the deadline.
                    if reason():
                        state['attempts'] = []
                        state['not_sent_reason'] = reason()
                        finish(state)
                        break
                    state['attempts'] = []
                    send(state)
            if not pending:
                if reason() or (exhausted and not retries):
                    break
                if retries:
                    time.sleep(min(.05, max(0, min(due for due, _ in retries) - time.monotonic())))
                continue
            done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
            completed = []
            for future in done:
                state, call_number = pending.pop(future)
                try:
                    attempt = future.result()
                except Exception:
                    # A crashed worker may already have sent/billed the request.
                    # Do not leak exception text or retry an uncertain outcome.
                    attempt = {'call_number': call_number, 'error': 'worker_error',
                               'outcome_unknown': True, 'latency_ms': 0}
                    gateway.stop_reason = gateway.stop_reason or 'worker_error'
                if attempt.get('attempted') is False:
                    gateway.calls -= 1
                gateway.observe(attempt)
                state['attempts'].append(attempt)
                completed.append(state)
            # Account for every completed cost before deciding to dispatch retries.
            for state in completed:
                last = state['attempts'][-1]
                retryable = last.get('http_status') == 429 or 500 <= last.get('http_status', 0) < 600
                retry = len(state['attempts']) - 1
                due = time.monotonic() + min(2 ** retry, 4)
                if retryable and retry < args.max_retries and not reason() and due < gateway.deadline:
                    retries.append((due, state))
                else:
                    if retryable and retry < args.max_retries:
                        last['retry_not_sent_reason'] = reason() or 'time_budget'
                    finish(state)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
    return {'stop_reason': reason() or 'candidates_exhausted', 'submitted_attempts': submitted,
            'max_in_flight': max_in_flight}
