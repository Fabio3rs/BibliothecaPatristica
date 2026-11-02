"""Frozen, page-paired research experiments. Preparation never calls a provider."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import random
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from pathlib import Path

from facsimile_transport import encode_facsimile_for_transport
from .common import ROOT, digest, dumps, now, read_json, readonly
from .evidence_locator import POLICY
from .experiment_runner import execute_run
from .prompt_registry import freeze_template
from .providers import normalize_config, request_spec, send

CAMPAIGN = 'EXP-76db1db8b118'
VARIANT = 'concepts-v2.3-navigation-confidence'


def check_schedule(plan):
    cases, schedule = plan['cases'], plan['schedule']
    ids = {c['run_id'] for c in cases}
    if len(cases) != 24 or len(ids) != 24 or len({c['page'] for c in cases}) != 24:
        raise ValueError('A/B requires 24 distinct source runs and physical pages')
    expected = [{'page':c['page'], 'baseline_run_id':c['run_id'], 'arm':arm, 'repetition':rep}
                for c in cases for arm in ('A', 'B') for rep in (1, 2)]
    random.Random(plan['seed']).shuffle(expected)
    if schedule != expected or plan.get('paid_requests_if_authorized') != 96:
        raise ValueError('A/B schedule must reproduce all 96 randomized calls from the stored seed')
    return schedule


def policy_manifest():
    directory = Path(__file__).parent
    return {'matching_policy':POLICY, 'validation_version':'kg-validation-7',
            'files':{name:digest((directory/name).read_bytes()) for name in
                     ('evidence_locator.py', 'evidence_matching.py', 'validation.py')},
            'semantic_status':'pending_independent_review'}


def input_hash(prepared):
    return digest({'case_hash':prepared['case_hash'], 'request':prepared['request'],
                   'schema_hash':prepared['schema_hash'], 'config':prepared['config'],
                   'ocr_preparation':prepared['ocr_preparation'],
                   'facsimile_policy':prepared['facsimile_policy']})


def verify_source(case, prepared, expected=None, *, encode_image=True):
    """Reproduce frozen inputs, never load a newer OCR/summary or render a new payload."""
    snap = case['snapshot']
    if digest(snap) != case['snapshot_hash'] or prepared['case_hash'] != case['snapshot_hash']:
        raise ValueError('Frozen snapshot hash mismatch')
    if digest(snap['ocr_raw']) != snap['source']['ocr_sha256']:
        raise ValueError('Frozen OCR hash mismatch')
    for key in ('system', 'user', 'schema'):
        content = prepared['schema'] if key == 'schema' else prepared[key+'_prompt']
        if digest(content) != prepared[key+'_hash']:
            raise ValueError(f'Frozen {key} hash mismatch')
    if dumps(prepared['user_payload']) != prepared['user_prompt']:
        raise ValueError('Frozen user payload is not reproducible')
    for key in ('source', 'v2'):
        if prepared['user_payload'][key] != snap[key]:
            raise ValueError(f'Frozen payload differs from snapshot: {key}')
    source_blocks = {b['id']:b for b in snap['ocr']['blocks']}
    model_blocks = {b['id']:b for b in prepared['user_payload']['ocr']['blocks']}
    records = prepared['ocr_preparation']['blocks']
    if set(source_blocks) != set(model_blocks) or len(records) != len(source_blocks):
        raise ValueError('Frozen OCR block set differs')
    for block in records:
        a, b = source_blocks[block['id']], model_blocks[block['id']]
        if digest(a['text']) != block['source_text_sha256'] or digest(b['text']) != block['model_text_sha256']:
            raise ValueError('Frozen OCR preparation differs')
        if {k:v for k,v in a.items() if k!='text'} != {k:v for k,v in b.items() if k!='text'}:
            raise ValueError('Frozen OCR metadata differs')
    if expected:
        actual = {'page':case['page'], 'input_hash':prepared['input_hash'],
                  'snapshot_hash':case['snapshot_hash'], 'generation_id':snap['source']['v2_generation_id'],
                  'ocr_sha256':snap['source']['ocr_sha256'], 'image_sha256':snap['facsimile'].get('sha256')}
        if any(expected.get(k) != v for k,v in actual.items()):
            raise ValueError('Audit case identity/hash mismatch')
    image = None
    if prepared['facsimile_selected']:
        info = snap['facsimile']
        path = Path(info['archived_path'])
        if not path.is_file() or digest(path.read_bytes()) != info['sha256']:
            raise ValueError('Archived image missing or changed; no substitution allowed')
        if encode_image:
            encoded, mime = encode_facsimile_for_transport(path)
            if digest(base64.b64decode(encoded)) != prepared['image_transport_sha256']:
                raise ValueError('Archived image transport cannot be reproduced exactly')
            image = {'base64':encoded, 'mime':mime}
    if encode_image and request_spec(prepared['config'], prepared, image) != prepared['request']:
        raise ValueError('Rebuilt arm A/request differs from the archived provider request')
    if input_hash(prepared) != prepared['input_hash']:
        raise ValueError('Frozen inference input hash mismatch')


def prepare_ab(store, audit_dir, *, name='KG navigation v2.3 A/B', output=None,
               benchmark_dir=ROOT/'.work/kg_benchmark_v1', campaign=CAMPAIGN):
    directory = Path(audit_dir)
    audit = read_json(directory/'ab_plan.json')
    schedule = check_schedule(audit)
    system_a = (directory/'stored_system_prompt.txt').read_text(encoding='utf-8')
    candidate = freeze_template(VARIANT, 'concepts_relations')
    if digest(system_a) != audit['arms']['A']['system_hash']:
        raise ValueError('Archived arm A prompt differs from audit')
    delta = (ROOT/'tools/knowledge_graph/prompts/navigation_v2.3_delta.txt').read_text(encoding='utf-8')
    # Only the explicitly versioned delta may differ. Existing instructions stay byte-identical.
    if candidate['system_prompt'].replace(delta+'\n\n', '', 1) != system_a:
        raise ValueError('Candidate is not the intended isolated prompt delta')
    if candidate['schema_hash'] != audit['schema_hash']:
        raise ValueError('A/B must use the frozen v2 response schema')
    design = Path(benchmark_dir)/'sample_design.json'
    reserved = set(read_json(design).get('reserved_holdout_volumes', [])) if design.exists() else set()
    frozen = {}; manifests = []; observed_cost = 0
    with readonly(store.path) as con:
        for expected in audit['cases']:
            identity = con.execute('''SELECT r.experiment_id,c.page,c.split FROM runs r
                JOIN cases c ON c.id=r.case_id WHERE r.id=?''',(expected['run_id'],)).fetchone()
            if identity is None or identity['experiment_id'] != campaign:
                raise ValueError('Source run is absent or does not belong to frozen campaign')
            if identity['split'] == 'blind_holdout' or identity['page'].split(':')[0] in reserved:
                raise ValueError('Blind holdout excluded from prompt A/B')
            row = con.execute('''SELECT r.*,c.page,c.snapshot_hash,c.snapshot_json,c.split
                FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.id=?''', (expected['run_id'],)).fetchone()
            if row is None or row['experiment_id'] != campaign:
                raise ValueError('Source run is absent or does not belong to frozen campaign')
            if row['split'] == 'blind_holdout' or row['page'].split(':')[0] in reserved:
                raise ValueError('Blind holdout excluded from prompt A/B')
            case = {'id':row['case_id'], 'page':row['page'], 'snapshot_hash':row['snapshot_hash'],
                    'snapshot':json.loads(row['snapshot_json'])}
            p = json.loads(row['prepared_json']); config = json.loads(row['config_json'])
            if config != p['config'] or normalize_config(config) != config:
                raise ValueError('Frozen provider configuration cannot be reproduced')
            if config['provider'] != 'meta' or config['api_key_env'] != 'META_API_KEY':
                raise ValueError('This audit A/B requires the archived Meta profile and META_API_KEY')
            if config.get('reasoning_effort') is not None or 'reasoning_effort' in p['request']['body']:
                raise ValueError('Reasoning effort must remain omitted, as in the campaign')
            for key in ('provider','model','max_output_tokens','temperature'):
                if config[key] != audit['config'][key]:
                    raise ValueError(f'Frozen configuration differs from audit: {key}')
            if p['system_prompt'] != system_a or p['schema'] != candidate['schema']:
                raise ValueError('Source run prompt/schema differs from frozen baseline')
            if p['mode'] != audit['config']['mode'] or p['facsimile_policy'] != audit['config']['facsimile']:
                raise ValueError('Frozen mode/facsimile differs from audit')
            verify_source(case, p, expected)
            frozen[expected['run_id']] = (case, p)
            observed_cost += (json.loads(row['result_json'] or '{}').get('cost_usd') or 0)
            manifests.append(dict(expected, case_id=case['id'], request_hash=digest(p['request']),
                                  user_hash=p['user_hash'], transport_hash=p['image_transport_sha256'],
                                  config_hash=digest(config), prices=config['prices']))
    policy = policy_manifest()
    plan = {'kind':'ab', 'version':'kg-ab-1', 'evaluation':False, 'size':96, 'bypass_cache':True,
            'created_at':now(), 'seed':audit['seed'], 'campaign':campaign,
            'audit_plan_hash':digest(audit), 'schedule':schedule, 'cases':manifests,
            'arms':{'A':{'system_hash':digest(system_a), 'variant':audit['arms']['A']['variant']},
                    'B':{'system_hash':candidate['system_hash'], 'variant':VARIANT,
                         'superseded_audit_proposal_hash':audit['arms']['B']['system_hash']}},
            'schema_hash':candidate['schema_hash'], 'validation':policy,
            'observed_baseline_cost_24_usd':observed_cost,
            'estimated_96_calls_usd':4*observed_cost,
            'cost_note':'Historical usage × frozen rates; longer prompt/output and transport retries may change total.'}
    def tasks():
        for index, task in enumerate(schedule):
            case, original = frozen[task['baseline_run_id']]
            p = deepcopy(original)
            if task['arm'] == 'B':
                p.update({k:candidate[k] for k in ('system_prompt','system_hash','variant')})
                p['request']['body']['messages'][0]['content'] = p['system_prompt']
                p['estimated_input_tokens'] = (len(p['system_prompt'])+len(p['user_prompt']))//3
            p['input_hash'] = input_hash(p)
            p['validation_policy'] = POLICY
            p['ab'] = dict(task, schedule_index=index, request_hash=digest(p['request']),
                           validation_manifest=policy)
            yield case, p, task['repetition']
    experiment = store.save_plan(name, plan, tasks())
    preflight = dict(plan, experiment=experiment, inference_calls=0, status='prepared',
                     checks={'24_frozen_cases':True, '96_independent_calls':True,
                             'baseline_request_reproduced':True, 'prompt_only_difference':True,
                             'archived_images_reproduced':True, 'reasoning_effort_omitted':True,
                             'historical_rows_reused_without_updates':True})
    if output:
        target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(preflight,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return preflight


def execute_ab(store, experiment, *, concurrency=2, max_calls=None, retry_interrupted=False,
               progress=None, caller=send, stop_event=None):
    if type(concurrency) is not int or concurrency<1:
        raise ValueError('Concurrency must be a positive integer')
    if max_calls is not None and (type(max_calls) is not int or max_calls<1):
        raise ValueError('max-calls must be a positive integer')
    plan = store.experiment_plan(experiment)
    if plan.get('kind') != 'ab':
        raise ValueError('ab-run only accepts a prepared A/B experiment')
    if plan.get('study') in {'stance-v24', 'semantics-v25'}:
        from .stance_study import schedule_cases, STUDY_ARMS
        arms = STUDY_ARMS[plan['study']]
        if (tuple(plan['arms']) != arms or plan['schedule'] != schedule_cases(plan['cases'], plan['seed'], arms=arms)
                or plan['size'] != len(plan['schedule'])):
            raise ValueError('Frozen experiment schedule changed')
    if caller is send and not os.environ.get('META_API_KEY'):
        raise ValueError('Set META_API_KEY in this terminal before explicitly executing ab-run')
    if plan['validation'] != policy_manifest():
        raise ValueError('Frozen validation implementation changed; prepare a new experiment')
    lock_dir = store.path.parent/(store.path.stem+'_locks'); lock_dir.mkdir(exist_ok=True)
    stop = stop_event or threading.Event(); handlers = {}
    with (lock_dir/(experiment+'.lock')).open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Experiment is already executing') from None
        rows = store.list_runs(experiment)
        retried = store.retried_runs(experiment)
        for row in rows:
            if row['status'] == 'running':
                store.finish(row['id'], 'interrupted', {'error':'Recovered interrupted process; remote outcome unknown',
                                                       'usage':{}, 'cost_usd':None})
            if retry_interrupted and row['status'] in {'running','interrupted'} and row['id'] not in retried:
                old = store.get_run(row['id']); p = deepcopy(old['prepared']); p['retry_of'] = row['id']
                store.add_run(experiment, store.get_case(row['case_id']), p, row['repetition'])
        pending = []
        source_manifest = {c['run_id']:c for c in plan['cases']}
        for row in store.list_runs(experiment):
            if row['status'] != 'planned':
                continue
            run = store.get_run(row['id']); p = run['prepared']
            index = p['ab']['schedule_index']; expected = plan['schedule'][index]
            source = source_manifest[expected['baseline_run_id']]
            if (p['config'] != run['config'] or digest(p['config']) != source['config_hash']
                    or run['case_id'] != source['case_id'] or p['case_hash'] != source['snapshot_hash']
                    or p['user_hash'] != source['user_hash'] or p['image_transport_sha256'] != source['transport_hash']
                    or p['ab']['validation_manifest'] != plan['validation']):
                raise ValueError('Prepared source/configuration differs from preflight manifest')
            if expected['arm']=='A' and digest(p['request'])!=source['request_hash']:
                raise ValueError('Arm A no longer reproduces the archived request')
            if any(p['ab'][k] != v for k,v in expected.items()) or run['repetition'] != expected['repetition']:
                raise ValueError('Prepared schedule changed')
            if p['system_hash'] != plan['arms'][expected['arm']]['system_hash'] or p['schema_hash'] != plan['schema_hash']:
                raise ValueError('Prepared prompt/schema changed')
            if digest(p['request']) != p['ab']['request_hash'] or p['validation_policy'] != POLICY:
                raise ValueError('Prepared request/policy changed')
            verify_source(store.get_case(run['case_id']), p)
            pending.append((index,row['id']))
        pending.sort()
        if max_calls is not None:
            pending = pending[:max_calls]
        workers = max(1, min(concurrency, len(pending)))
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_:stop.set())
        completed = 0; submitted = 0
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                active = {}; iterator = iter(pending)
                exhausted = False
                while active or not exhausted:
                    while not exhausted and not stop.is_set() and len(active)<workers:
                        item = next(iterator,None)
                        if item is None:
                            exhausted = True; break
                        index, identifier = item
                        future = pool.submit(execute_run,store,identifier,bypass_cache=True,caller=caller)
                        active[future] = index; submitted += 1
                    if stop.is_set(): exhausted = True
                    if not active: break
                    done, _ = wait(active,timeout=.25,return_when=FIRST_COMPLETED)
                    for future in done:
                        index = active.pop(future); run = future.result(); completed += 1
                        if progress:
                            progress({'run_id':run['id'],'status':run['status'],'schedule_index':index,
                                      'finished':completed,'submitted':submitted,'stopping':stop.is_set()})
        finally:
            for sig, handler in handlers.items(): signal.signal(sig,handler)
    from collections import Counter
    states = Counter(r['status'] for r in store.list_runs(experiment))
    return {'experiment':experiment,'submitted':submitted,'finished':completed,'stopped':stop.is_set(),
            'states':dict(states),'interruption_note':'Hard-kill outcomes remain unknown; --retry-interrupted explicitly permits another paid attempt.'}
