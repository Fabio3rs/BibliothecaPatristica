"""Coordinator for bounded HTTP tasks, durable staging and conservative budgets."""
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import random
import statistics
import time

from .contract import SCHEMA as STAGING_SCHEMA, contracts, digest, encoded, write_json
from .reading import SCHEMA, VERSION, PROMPT, convert
from .http import call, payload, reservation
from .sources import check_sources, prepare_task
from .staging import canonical, continuations, db_snapshot, diff
from .validation import validate


def checkpoint_matches(path, fingerprint):
    try:
        checkpoint = json.loads((path / 'checkpoint.json').read_text())
        data = (path / 'response.json').read_bytes()
        if checkpoint['input_fingerprint'] == fingerprint and checkpoint['response_sha256'] == digest(data):
            response = json.loads(data)
            if response['status'] == 'received':
                return response
    except (OSError, ValueError, KeyError):
        pass
    return None


def stage(job, response, db):
    task, directory = job['task'], job['directory']
    check_sources(task)
    result = {'task_id': task['task_id'], 'input_fingerprint': job['fingerprint'],
              'status': response['status'], 'usage': {k: response.get(k) for k in
              ('input_tokens', 'output_tokens', 'reported_cost_usd', 'estimated_cost_usd', 'latency_ms',
               'provider', 'requested_model', 'returned_model', 'requested_service_tier', 'returned_service_tier')},
              'cache_hit': job.get('cache_hit', False)}
    if response['status'] == 'received':
        dto = response['dto']
        if task.get('extraction_profile') == 'literal-v1':
            from .literal import stage as stage_literal
            return stage_literal(dto, task, directory, db, result)
        alignment = None
        if job.get('expected_schema_version') == VERSION or (isinstance(dto, dict) and dto.get('schema_version') == VERSION):
            write_json(directory / 'reading.json', dto)
            dto, alignment = convert(dto, task)
            write_json(directory / 'alignment.json', alignment)
            result['alignment_complete'] = alignment['complete']
            if dto is None:
                result.update(status='validation_failed', validation={'valid': False, 'errors': alignment['errors']})
                write_json(directory / 'result.json', result)
                return result
        validation = validate(dto, task)
        candidate, report = canonical(dto, validation, task)
        result.update(status=('validated_staging' if report['valid'] else 'canonical_validation_failed')
                      if validation['valid'] else 'validation_failed',
                      validation=validation, canonical_report=report)
        if alignment is not None and not alignment['complete'] and validation['valid'] and report['valid']:
            result['status'] = 'partial_extraction'
        write_json(directory / 'extraction.json', dto)
        write_json(directory / 'validation.json', validation)
        write_json(directory / 'canonical_report.json', report)
        if candidate is not None:
            write_json(directory / 'canonical_candidate.json', candidate)
        snapshot = db_snapshot(db, task)
        write_json(directory / 'db_snapshot.json', snapshot)
        write_json(directory / 'diff.json', diff(snapshot, candidate if report['valid'] else None))
        rules = dto.get('editorial_rules', []) if validation['valid'] else []
        mappings = dto.get('edition_mappings', []) if validation['valid'] else []
        write_json(directory / 'editorial_rules.json', {'rules': rules,
            'edition_mappings': mappings, 'sources': [{'page_id': p['page_id'], 'sha256': p['ocr_sha256']} for p in task['pages']],
            'validated_spans': {i['id']: validation['spans'].get(i['id']) for i in rules + mappings},
            'status': 'proposed_not_a_verified_map'})
    else:
        result['error'] = response.get('error')
    write_json(directory / 'result.json', result)
    return result


def error_preview(errors):
    lines = [line for error in errors for line in error.splitlines() if line.strip()]
    preview = [line if len(line) <= 350 else line[:350] + '…' for line in lines[:3]]
    return preview, len(lines) > 3 or any(len(line) > 350 for line in lines[:3])


def print_result(job, result, accounted):
    validation, validation_shortened = error_preview(result.get('validation', {}).get('errors', []))
    canonical_errors, canonical_shortened = error_preview(result.get('canonical_report', {}).get('errors', []))
    print(json.dumps({'task_id': result['task_id'], 'status': result['status'],
        'cache_hit': result.get('cache_hit', False), 'attempts': job['attempt'],
        'budget_accounted_usd': accounted,
        'counts': result.get('validation', {}).get('counts'),
        'validation_errors': validation,
        'canonical_errors': canonical_errors,
        'error_preview_truncated': validation_shortened or canonical_shortened,
        'alignment_complete': result.get('alignment_complete'),
        'result_file': str(job['directory'] / 'result.json')}, ensure_ascii=False), flush=True)


def run(args, config, selections):
    out = args.out.resolve()
    if out.exists() and not args.resume:
        raise ValueError('Output directory exists; use --resume or a new --out')
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / '.run.lock').open('a+')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError('Another coordinator is using this output directory') from None
    try:
        return _run(args, config, selections, out)
    finally:
        lock.close()


def _run(args, config, selections, out):
    started = time.monotonic()
    deadline = started + args.max_seconds
    prompt, schema, version = PROMPT, SCHEMA, VERSION
    profile = getattr(args, 'extraction_profile', 'reading-v2')
    if profile == 'literal-v1':
        from .literal import PROMPT as prompt, SCHEMA as schema, VERSION as version
    bundle = contracts()
    code_hash = digest({p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))})
    run_config = {'version': version, 'extraction_profile': profile,
                  'transport': config.public(), 'contracts': bundle,
                  'schema_sha256': digest(schema), 'prompt_sha256': digest(prompt.encode()),
                  'code_sha256': code_hash, 'mode': args.mode, 'limit_new_tasks': args.limit,
                  'workers': args.workers, 'max_rps': args.max_rps, 'max_cost_usd': args.max_cost_usd,
                  'max_seconds': args.max_seconds, 'db': str(args.db.resolve()), 'db_readonly': True,
                  'no_api': args.no_api, 'resume': args.resume,
                  'budget_scope': 'new HTTP attempts this invocation, including retries; cached calls not charged again',
                  'created_at': datetime.now(timezone.utc).isoformat()}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    write_json(out / 'invocations' / (stamp + '.json'), run_config)
    write_json(out / 'schema.json', schema)
    write_json(out / 'staging.schema.json', STAGING_SCHEMA)
    (out / 'prompt.txt').write_text(prompt, encoding='utf-8')
    jobs, preparation_errors = [], []
    for volume, number in selections:
        if time.monotonic() >= deadline:
            preparation_errors.append({'volume': volume, 'physical_page': number, 'error': 'preparation_time_limit'})
            break
        try:
            task = prepare_task(args.root, volume, number, out, args.mode, args.neighbors,
                                args.context_images, getattr(args, 'fragment_line_ids', None))
            task['extraction_profile'] = profile
            if args.fragment_request:
                original = json.loads(args.fragment_request.read_text())
                if task['pages'][0]['ocr_sha256'] != original['ocr_sha256']:
                    raise ValueError('fragment request OCR hash is stale')
            request = payload(task, config, prompt)
            fingerprint = digest({'request': request, 'sources': task, 'config': config.public(), 'contracts': bundle,
                                  'code_sha256': code_hash})
            directory = out / 'tasks' / (task['task_id'] + '-' + fingerprint[:16])
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / 'task.json', task)
            write_json(directory / 'request.preview.json', request)
            write_json(directory / 'request_identity.json', {'input_fingerprint': fingerprint,
                        'schema_sha256': digest(schema), 'prompt_sha256': digest(prompt.encode()),
                        'transport': config.public(), 'contracts': bundle, 'code_sha256': code_hash})
            if args.show_payload:
                print(json.dumps({'task_id': task['task_id'], 'payload': request}, ensure_ascii=False))
            job = {'task': task, 'fingerprint': fingerprint, 'directory': directory,
                   'expected_schema_version': version,
                   'reserve': reservation(task, config, prompt), 'attempt': 0, 'ready_at': started}
            job['cached'] = checkpoint_matches(directory, fingerprint) if args.resume else None
            jobs.append(job)
        except (OSError, ValueError) as exc:
            preparation_errors.append({'volume': volume, 'physical_page': number, 'error': str(exc)})
    write_json(out / 'manifest.json', {**run_config,
        'tasks': [{'task_id': j['task']['task_id'], 'directory': str(j['directory']),
                   'input_fingerprint': j['fingerprint'], 'reservation_estimate_usd': j['reserve'],
                   'cache_available': j['cached'] is not None} for j in jobs],
        'preparation_errors': preparation_errors})
    if args.no_api:
        summary = {'status': 'prepared', 'tasks_prepared': len(jobs), 'calls': 0,
                   'images': sum(len(p['regions']) for j in jobs for p in j['task']['pages']),
                   'cache_available': sum(j['cached'] is not None for j in jobs),
                   'preparation_errors': preparation_errors, 'db_updates': 0,
                   'out': str(out), 'service_tier': config.service_tier}
        write_json(out / 'summary.json', summary)
        return summary
    results, pending, attempts = [], [], []
    for job in jobs:
        if job['cached'] is not None:
            job['cache_hit'] = True
            result = stage(job, job['cached'], args.db)
            results.append(result)
            if args.verbose:
                print_result(job, result, 0.0)
        else:
            pending.append(job)
    key = config.key() if pending and args.limit else ''
    sent_tasks, reserved, accounted = set(), 0.0, 0.0
    reported, estimated, unknown_reserved = 0.0, 0.0, 0.0
    in_flight, stop_reason, max_in_flight = {}, None, 0
    next_send, last_progress = started, started
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while pending or in_flight:
            now = time.monotonic()
            if now >= deadline:
                stop_reason = stop_reason or 'time_limit'
            if args.max_cost_usd is not None and accounted >= args.max_cost_usd:
                stop_reason = stop_reason or 'cost_limit'
            if not stop_reason:
                for job in list(pending):
                    now = time.monotonic()
                    if len(in_flight) >= args.workers or now < next_send:
                        break
                    if job['ready_at'] > now:
                        continue
                    new = job['fingerprint'] not in sent_tasks
                    if new and len(sent_tasks) >= args.limit:
                        continue
                    reserve = job['reserve']
                    if args.max_cost_usd is not None and reserve is None:
                        stop_reason = 'pricing_unavailable_configure_explicit_rates'
                        break
                    if args.max_cost_usd is not None and accounted + reserved + reserve > args.max_cost_usd:
                        if not in_flight:
                            stop_reason = 'cost_reservation_limit'
                        continue
                    try:
                        check_sources(job['task'])
                    except (ValueError, OSError) as exc:
                        pending.remove(job)
                        result = {'task_id': job['task']['task_id'], 'status': 'source_changed', 'error': str(exc)}
                        write_json(job['directory'] / 'result.json', result)
                        results.append(result)
                        continue
                    pending.remove(job)
                    sent_tasks.add(job['fingerprint'])
                    job['attempt'] += 1
                    reserved += reserve or 0
                    future = pool.submit(call, job['task'], config, prompt, key,
                                         max(.001, min(config.timeout, deadline-now)))
                    in_flight[future] = job
                    max_in_flight = max(max_in_flight, len(in_flight))
                    next_send = now + (1/args.max_rps if args.max_rps else 0)
                if not in_flight and pending and all(j['fingerprint'] not in sent_tasks for j in pending) and len(sent_tasks) >= args.limit:
                    stop_reason = stop_reason or 'task_limit'
            if stop_reason and not in_flight:
                break
            if not in_flight:
                if not pending:
                    break
                time.sleep(min(.1, max(.001, deadline-time.monotonic())))
                continue
            done, _ = wait(in_flight, timeout=.2, return_when=FIRST_COMPLETED)
            for future in done:
                job = in_flight.pop(future)
                reserved -= job['reserve'] or 0
                response = future.result()
                usage = response.get('reported_cost_usd')
                if usage is not None:
                    reported += usage
                else:
                    usage = response.get('estimated_cost_usd')
                    if usage is not None:
                        estimated += usage
                if usage is None:
                    # Unavailable Flex (429) is not charged. Other unknown outcomes,
                    # including timeouts, consume their reservation conservatively.
                    usage = 0 if response.get('http_status') == 429 and config.provider == 'openai' else (job['reserve'] or 0)
                    unknown_reserved += usage
                accounted += usage
                event = {**response, 'task_id': job['task']['task_id'], 'attempt': job['attempt'],
                         'budget_accounted_usd': usage}
                write_json(job['directory'] / f"attempt-{stamp}-{job['attempt']}.json", event)
                attempts.append(event)
                if response.get('http_status') in (401, 402, 403) or response.get('error') == 'unexpected_service_tier':
                    stop_reason = 'authentication_billing_or_service_tier_error'
                if response.get('retryable') and job['attempt'] <= args.max_retries and not stop_reason:
                    delay = max(response.get('retry_after', 0), min(60, 2**job['attempt'] + random.uniform(0, 1)))
                    job['ready_at'] = time.monotonic() + delay
                    if job['ready_at'] < deadline:
                        pending.append(job)
                        continue
                write_json(job['directory'] / 'response.json', response)
                write_json(job['directory'] / 'checkpoint.json', {'input_fingerprint': job['fingerprint'],
                           'response_sha256': digest((job['directory'] / 'response.json').read_bytes())})
                try:
                    result = stage(job, response, args.db)
                except (ValueError, OSError) as exc:
                    result = {'task_id': job['task']['task_id'], 'status': 'staging_error', 'error': str(exc)}
                    write_json(job['directory'] / 'result.json', result)
                results.append(result)
                if args.verbose:
                    print_result(job, result, accounted)
            if time.monotonic()-last_progress > 30:
                print(json.dumps({'progress': True, 'completed_attempts': len(attempts),
                    'in_flight': len(in_flight), 'pending': len(pending), 'budget_accounted_usd': accounted}), flush=True)
                last_progress = time.monotonic()
    continuation_inputs = []
    for job in jobs:
        path = job['directory'] / 'result.json'
        extraction = job['directory'] / 'extraction.json'
        if path.exists() and extraction.exists():
            result = json.loads(path.read_text())
            continuation_inputs.append({**result, 'task': job['task'], 'dto': json.loads(extraction.read_text())})
    continuity = continuations(continuation_inputs)
    write_json(out / 'continuations.json', continuity)
    for fragment in continuity['fragment_requests']:
        write_json(out / 'fragment_requests' / (fragment['request_id'] + '.json'), fragment)
    expansion_count = 0
    for result in continuation_inputs:
        if not result.get('validation', {}).get('valid'):
            continue
        for req in result['dto']['expansion_requests']:
            anchor = next(p for p in result['task']['pages'] if p['page_id'] == req['page_id'])
            direction = -1 if req['direction'] == 'before' else 1
            numbers = [anchor['physical_sequence'] + direction*n for n in range(1, req['max_files']+1)]
            request = {'volume': result['task']['volume'], 'pages': [n for n in numbers if n > 0],
                'anchor_ocr_file': anchor['ocr_file'], 'anchor_sha256': anchor['ocr_sha256'],
                'reason': req['reason'], 'source_task': result['task_id'], 'automatic_dispatch': False}
            write_json(out / 'expansion_requests' / (digest(request)[:24]+'.json'), request)
            expansion_count += 1
    latencies = sorted(a['latency_ms'] for a in attempts)
    summary = {'status': 'completed', 'stop_reason': stop_reason or 'tasks_exhausted',
        'tasks_prepared': len(jobs), 'new_tasks_submitted': len(sent_tasks), 'calls': len(attempts),
        'cache_hits': sum(r.get('cache_hit', False) for r in results),
        'statuses': dict(Counter(r['status'] for r in results)), 'not_started_or_retry_pending': len(pending),
        'successes': sum(r['status'] in {'validated_staging', 'literal_staged', 'literal_staged_with_pending'} for r in results),
        'errors': sum(r['status'] not in {'validated_staging', 'literal_staged', 'literal_staged_with_pending'} for r in results),
        'transport_successes': sum(a['status'] == 'received' for a in attempts),
        'latency_mean_ms': statistics.mean(latencies) if latencies else None,
        'latency_median_ms': statistics.median(latencies) if latencies else None,
        'latency_p95_ms': latencies[math.ceil(.95*len(latencies))-1] if latencies else None,
        'input_tokens': sum(a.get('input_tokens') or 0 for a in attempts),
        'output_tokens': sum(a.get('output_tokens') or 0 for a in attempts),
        'reported_cost_usd': reported if any(a.get('reported_cost_usd') is not None for a in attempts) else None,
        'estimated_cost_usd_for_unreported_calls': estimated,
        'unknown_outcome_reservations_usd': unknown_reserved, 'budget_accounted_usd': accounted,
        'max_in_flight': max_in_flight, 'db_updates': 0, 'preparation_errors': preparation_errors,
        'stitch_proposals': len(continuity['stitches']), 'fragment_requests': len(continuity['fragment_requests']),
        'bounded_expansion_requests': expansion_count,
        'out': str(out), 'extraction_profile': profile}
    if profile == 'literal-v1':
        summary['import_ready'] = False
        summary['literal_tasks_with_pending'] = sum(r['status'] == 'literal_staged_with_pending' for r in results)
        summary['success_definition'] = 'Schema-valid transcription saved; not verified coverage or canonical import.'
        import csv
        with (out/'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=['task_id', 'status', 'entries', 'reference_groups',
                'tables', 'unresolved', 'ocr_alignment_ratio', 'input_tokens', 'output_tokens', 'estimated_cost_usd'])
            writer.writeheader()
            for result in results:
                validation = result.get('validation', {})
                writer.writerow({'task_id': result['task_id'], 'status': result['status'],
                    **{k: validation.get('counts', {}).get(k) for k in ('entries', 'reference_groups', 'tables', 'unresolved')},
                    'ocr_alignment_ratio': validation.get('coverage', {}).get('ratio'),
                    **{k: result.get('usage', {}).get(k) for k in ('input_tokens', 'output_tokens', 'estimated_cost_usd')}})
    write_json(out / 'summary.json', summary)
    write_json(out / 'invocations' / (stamp + '.summary.json'), summary)
    return summary
