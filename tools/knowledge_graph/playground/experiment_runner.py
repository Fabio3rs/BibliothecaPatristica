"""Durable page-atomic plans, bounded execution and independent repetitions."""
from __future__ import annotations

import base64
import fcntl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path

from facsimile_transport import encode_facsimile_for_transport
from . import VERSION
from .common import KIT, digest, dumps, read_json
from .prompt_registry import render
from .pricing import reference
from .providers import ProviderError, estimate_cost, normalize_config, request_spec, send
from .validation import evaluate_response


def prepare(case, config, *, variant='concepts-v1', mode='concepts_relations',
            facsimile='off', system_override=None, template=None):
    config = normalize_config(config)
    if facsimile not in {'off', 'on', 'auto'}:
        raise ValueError('Facsimile must be off, on or auto')
    snap = case['snapshot']
    info, warnings = snap['facsimile'], []
    score = snap['v2'].get('metadata_context', {}).get('static_analysis', {}).get('facsimile_score', 0)
    requested = facsimile == 'on' or (facsimile == 'auto' and score >= 3)
    attached = bool(requested and info.get('available') and config['vision'])
    image = None
    if requested and not attached:
        warnings.append('facsimile_not_attached: unavailable image or profile without vision')
    if attached:
        path = Path(info['archived_path'])
        if digest(path.read_bytes()) != info['sha256']:
            raise ValueError('Archived facsimile changed')
        encoded, mime = encode_facsimile_for_transport(path)
        image = {'base64': encoded, 'mime': mime, 'transport_sha256': digest(base64.b64decode(encoded))}
    prompt = render(snap, variant, mode, attached=attached, system_override=system_override, template=template)
    request = request_spec(config, prompt, image)
    prepared = dict(prompt, playground_version=VERSION, config=config, request=request, facsimile_policy=facsimile,
                    facsimile_selected=attached, image_transport_sha256=image['transport_sha256'] if image else None,
                    warnings=warnings, case_hash=case['snapshot_hash'], pricing_reference=reference(config))
    prepared['input_hash'] = digest({'case_hash': case['snapshot_hash'], 'request': request,
                                     'schema_hash': prompt['schema_hash'], 'config': config,
                                     'ocr_preparation': prompt['ocr_preparation'],
                                     'facsimile_policy': facsimile})
    prepared['estimated_input_tokens'] = (len(prompt['system_prompt']) + len(prompt['user_prompt'])) // 3
    prepared['token_estimate_note'] = 'characters/3; not a tokenizer estimate; image tokens excluded'
    return prepared


def plan_experiment(store, cases, configs, *, variants=('concepts-v1',), modes=('concepts_relations',),
                    facsimiles=('off',), repetitions=1, name='KG experiment', evaluation=False,
                    bypass_cache=False, system_override=None):
    if not cases or not configs or not variants or not modes or not facsimiles:
        raise ValueError('Experiment matrix must be nonempty')
    if type(repetitions) is not int or not 1 <= repetitions <= 100:
        raise ValueError('Repetitions must be between 1 and 100')
    size = len(cases) * len(configs) * len(variants) * len(modes) * len(facsimiles) * repetitions
    if size > 10000:
        raise ValueError('Playground plans are limited to 10,000 runs; use bounded research batches')
    if any(c['snapshot']['source'].get('split') == 'blind_holdout' for c in cases) and not evaluation:
        raise ValueError('Blind holdout requires evaluation mode')
    configs = [normalize_config(c) for c in configs]
    def tasks():
        for case in cases:
            for config in configs:
                if config['provider'] == 'fixture' and not case['snapshot']['source'].get('synthetic'):
                    raise ValueError('Fixture provider cannot be used on real corpus pages')
                for variant in variants:
                    for mode in modes:
                        for facsimile in facsimiles:
                            prepared = prepare(case, config, variant=variant, mode=mode,
                                               facsimile=facsimile, system_override=system_override)
                            for rep in range(1, repetitions + 1):
                                yield case, prepared, rep
    plan = {'case_ids': [c['id'] for c in cases], 'configs': [normalize_config(c) for c in configs],
            'variants': list(variants), 'modes': list(modes), 'facsimiles': list(facsimiles),
            'repetitions': repetitions, 'bypass_cache': bypass_cache or repetitions > 1,
            'size': size, 'evaluation': evaluation}
    return store.save_plan(name, plan, tasks())


def execute_run(store, identifier, *, bypass_cache=False, caller=send):
    run = store.get_run(identifier)
    prepared, config = run['prepared'], run['config']
    case = store.get_case(run['case_id'])
    store.start(identifier)
    started = time.perf_counter()
    try:
        cached = None if bypass_cache else store.cached(run['input_hash'], run['experiment_id'])
        if cached:
            result = deepcopy(cached['result'])
            result.update(usage={}, latency_seconds=0, cost_usd=0, attempts=0,
                          inference_performed=False, facsimile_attached=False,
                          cached_usage=cached['result'].get('usage'), cached_from=cached['id'])
        elif config['provider'] == 'fixture':
            if not case['snapshot']['source'].get('synthetic'):
                raise ValueError('Synthetic fixture replay requires a synthetic source')
            fixture_path = KIT / 'page-output.demo.json'
            if prepared['schema_version'] == 'kg-extraction-v2':
                fixture_path = Path(__file__).parent / 'fixtures/page-output.confidence.demo.json'
            data = read_json(fixture_path)
            if prepared['mode'] == 'concepts_only':
                for key in ('entities', 'claims', 'events', 'relations'):
                    data[key] = []
                data['evidence'] = [e for e in data['evidence'] if e['id'] == 'ev4']
            result = {'raw_response': dumps(data), 'provider_response': None, 'usage': {},
                      'latency_seconds': time.perf_counter() - started, 'cost_usd': 0,
                      'attempts': 0, 'inference_performed': False, 'facsimile_attached': False,
                      'synthetic_replay': True, 'finish_reason': 'fixture'}
        else:
            result = caller(config, prepared['request'])
            result['cost_usd'] = estimate_cost(result['usage'], config['prices'])
            result['inference_performed'] = True
            result['facsimile_attached'] = prepared['facsimile_selected']
        validation = evaluate_response(case['snapshot'], result, prepared)
        result['parsed_json'] = validation['parsed']
        result['validated_extraction'] = validation['validated_extraction']
        store.finish(identifier, ('cached' if cached else 'completed') if validation['valid'] else 'invalid',
                     result, validation, cached['id'] if cached else None)
    except Exception as exc:
        # Never persist arbitrary exception reprs containing URL/auth/provider secrets.
        message = str(exc) if isinstance(exc, (ProviderError, ValueError)) else type(exc).__name__
        from .providers import redact
        result = {'error': redact(message, config), 'provider_response': getattr(exc, 'response', ''),
                  'raw_response': '', 'usage': {}, 'cost_usd': None, 'attempts': getattr(exc, 'attempts', 0),
                  'latency_seconds': time.perf_counter() - started, 'inference_performed': None,
                  'facsimile_attached': None, 'retry_log': getattr(exc, 'retry_log', [])}
        store.finish(identifier, 'error', result)
    return store.get_run(identifier)


def execute_experiment(store, experiment, *, concurrency=1, resume=False, retry_errors=False,
                       progress=None, caller=send):
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError('Concurrency must be between 1 and 16')
    plan = store.experiment_plan(experiment)
    if plan.get('kind') == 'corpus':
        raise ValueError('Use corpus-run --campaign to execute this persistent corpus queue')
    # Same experiment cannot be driven by the UI and CLI simultaneously.
    lock_dir = store.path.parent / (store.path.stem + '_locks')
    lock_dir.mkdir(exist_ok=True)
    with (lock_dir / (experiment + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Experiment is already executing') from None
        runs = store.list_runs(experiment)
        if any(r['status'] != 'planned' for r in runs) and not resume:
            raise ValueError('Use resume to continue an existing experiment')
        # Preserve interrupted/error records. Recovery creates new run rows, never overwrites.
        if resume:
            retried = store.retried_runs(experiment)
            for item in runs:
                if item['id'] in retried:
                    continue
                if item['status'] == 'running' or (retry_errors and item['status'] == 'error'):
                    old = store.get_run(item['id'])
                    if item['status'] == 'running':
                        store.finish(item['id'], 'interrupted', {'error': 'Recovered interrupted process; remote outcome unknown', 'usage': {}, 'cost_usd': None})
                    prepared = deepcopy(old['prepared'])
                    prepared['retry_of'] = item['id']
                    store.add_run(experiment, store.get_case(item['case_id']), prepared, item['repetition'])
        pending = [r['id'] for r in store.list_runs(experiment) if r['status'] == 'planned']
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(execute_run, store, identifier, bypass_cache=plan['bypass_cache'], caller=caller): identifier for identifier in pending}
            for count, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if progress:
                    event = {'completed': count, 'total': len(pending), 'run_id': result['id'], 'status': result['status']}
                    validation = result.get('validation') or {}
                    if validation.get('completion', {}).get('output_limit_reached'):
                        event['completion'] = validation['completion']
                        event['message'] = next(d['message'] for d in validation['diagnostics'] if d['code'] == 'provider_output_limit_reached')
                    progress(event)
    return store.list_runs(experiment)
