"""Freeze a small, repeatable second-pass experiment using archived v3 outputs."""
from copy import deepcopy
import json
from pathlib import Path
import random

from .ab_runner import input_hash, verify_source
from .common import ROOT, digest, dumps, now, read_json, readonly
from .providers import normalize_config, request_spec
from .statement_review_contract import VERSION, TASK, schema_for
from .validation_dispatch import DEFAULT_POLICY, evaluate_response
from .verification_contract import candidate_payload
from .verification_study import check_prepared as check_frozen, frozen_image

DEFAULT_RUNS = (1679, 1680, 1681, 1682)
PROMPT = ROOT / 'tools/knowledge_graph/prompts/review_statement_boundaries_v1.system.txt'
STUDY = 'statement-boundaries-v1'
CRITERIA = (
    'quotation_vs_narrator', 'holder_and_stance', 'condition_and_negation',
    'participant_roles', 'supported_content_retained', 'evidence_support',
)


def policy():
    directory = Path(__file__).parent
    files = ('statement_review_contract.py', 'verification_contract.py', 'validation_dispatch.py',
             'validation_location.py', 'validation_micro.py', 'validation.py', 'statement_contract.py',
             'evidence_locator_v5.py', 'evidence_locator_v4.py', 'evidence_locator.py',
             'evidence_matching.py', 'statement_review_study.py', 'verification_study.py',
             'verification_runner.py', 'providers.py', 'ab_runner.py')
    return {'review_contract': VERSION, 'matching_policy': DEFAULT_POLICY,
            'files': {f: digest((directory / f).read_bytes()) for f in files}}


def make_prepared(original, extraction, source_run_id):
    p = deepcopy(original)
    for key in ('ab', 'retry_of'):
        p.pop(key, None)
    p.update(system_prompt=PROMPT.read_text(encoding='utf-8'),
             variant={'id': VERSION, 'description': 'Statement boundaries before holder attribution'},
             task=TASK, mode=TASK, schema_version=VERSION,
             extraction_schema=deepcopy(original['schema']), baseline_extraction=deepcopy(extraction),
             baseline_extraction_hash=digest(extraction), source_run_id=source_run_id,
             validation_policy=DEFAULT_POLICY)
    p['schema'] = schema_for(original['schema'])
    p['user_payload']['response_schema'] = deepcopy(p['schema'])
    p['user_payload']['candidate_extraction'] = candidate_payload(extraction)
    p['user_payload']['review_constraints'] = {
        'frozen_groups': ['concepts', 'entities', 'events', 'original_evidence'],
        'new_claim_prefix': 'pV', 'new_relation_prefix': 'rV', 'new_evidence_prefix': 'evV',
        'source_status': 'Source assertions, not independently established facts',
    }
    p['user_prompt'] = dumps(p['user_payload'])
    for field in ('system', 'user', 'schema'):
        p[field + '_hash'] = digest(p['schema'] if field == 'schema' else p[field + '_prompt'])
    p['request'] = request_spec(p['config'], p, frozen_image(original))
    p['input_hash'] = input_hash(p)
    p['estimated_input_tokens'] = (len(p['system_prompt']) + len(p['user_prompt'])) // 3
    return p


def check_schedule(plan):
    sources, repetitions = plan['source_runs'], plan['repetitions']
    expected = [(s, r) for s in sources for r in range(1, repetitions + 1)]
    random.Random(plan['seed']).shuffle(expected)
    actual = [(t['source_run_id'], t['repetition']) for t in plan['schedule']]
    if (not sources or len(set(sources)) != len(sources) or type(repetitions) is not int
            or repetitions < 1 or actual != expected or plan['size'] != len(expected)
            or [t['schedule_index'] for t in plan['schedule']] != list(range(len(expected)))
            or not plan.get('bypass_cache') or plan.get('evaluation')):
        raise ValueError('Invalid frozen statement-review schedule')
    if set(plan['pages']) != {t['page'] for t in plan['schedule']}:
        raise ValueError('Frozen schedule page set differs')
    for source in sources:
        repeated = [t for t in plan['schedule'] if t['source_run_id'] == source]
        if len({t['prepared_hash'] for t in repeated}) != 1:
            raise ValueError('Independent repetitions must have identical inference inputs')


def check_prepared(store, run, plan, manifest):
    p = run['prepared']
    if p.get('task') != TASK or p.get('validation_policy') != plan['policy']['matching_policy']:
        raise ValueError('Statement-review task or validation policy changed')
    original = store.get_run(manifest['source_run_id'])
    if p['extraction_schema'] != original['prepared']['schema']:
        raise ValueError('Original extraction schema changed')
    for key in ('source', 'v2', 'ocr', 'facsimile'):
        if p['user_payload'][key] != original['prepared']['user_payload'][key]:
            raise ValueError('Original source payload changed')
    if p['config'] != original['config']:
        raise ValueError('Original provider configuration changed')
    check_frozen(store, run, plan, manifest)


def prepare_statement_review(store, *, source_runs=DEFAULT_RUNS, repetitions=2, seed=20261104,
                             name='KG v3 statement boundaries and attribution', output=None,
                             benchmark_dir=ROOT / '.work/kg_benchmark_v1'):
    if output and Path(output).exists():
        raise ValueError('Preflight already exists; resume its experiment')
    if (not source_runs or len(set(source_runs)) != len(source_runs)
            or any(type(i) is not int or i < 1 for i in source_runs)
            or type(repetitions) is not int or repetitions < 1):
        raise ValueError('Distinct source run IDs and positive repetitions are required')
    design_path = Path(benchmark_dir) / 'sample_design.json'
    design = read_json(design_path) if design_path.exists() else {}
    items, source_plans = [], {}
    with readonly(store.path) as con:
        for identifier in source_runs:
            identity = con.execute('''SELECT c.page,c.split,r.status,r.experiment_id FROM runs r
                JOIN cases c ON c.id=r.case_id WHERE r.id=?''', (identifier,)).fetchone()
            if identity is None:
                raise ValueError(f'Source run {identifier} does not exist')
            source_plan = store.experiment_plan(identity['experiment_id'])
            reserved = set(design.get('reserved_holdout_volumes', []))
            reserved.update(source_plan.get('selection', {}).get('reserved_holdout_volumes', []))
            if identity['split'] == 'blind_holdout' or identity['page'].split(':')[0] in reserved:
                raise ValueError('Blind holdout excluded before source loading')
            if identity['status'] not in ('completed', 'invalid'):
                raise ValueError('Source must be a terminal original inference, not a cache replay')
            source = store.get_run(identifier)
            case = store.get_case(source['case_id'])
            if any(s.get('work_key') in set(design.get('held_work_keys', []))
                   for s in case['snapshot']['v2']['segments']):
                raise ValueError('Holdout work excluded')
            original, config, result = source['prepared'], source['config'], source['result']
            if original.get('task') or original['schema_version'] != 'kg-extraction-v3':
                raise ValueError('Source must be an original schema-v3 extraction')
            if (config != original['config'] or normalize_config(config) != config
                    or config['provider'] != 'meta' or config['api_key_env'] != 'META_API_KEY'):
                raise ValueError('This study preserves a frozen Meta profile using META_API_KEY')
            verify_source(case, original)
            checked = evaluate_response(case['snapshot'], result, original)
            if not checked['structural_valid'] or checked['completion']['output_limit_reached']:
                raise ValueError('Cannot review a truncated or structurally invalid baseline')
            if checked['parsed'] != result.get('parsed_json'):
                raise ValueError('Stored extraction differs from original raw response')
            prepared = make_prepared(original, checked['parsed'], identifier)
            verify_source(case, prepared)
            source_plans[source['experiment_id']] = digest(source_plan)
            for repetition in range(1, repetitions + 1):
                manifest = dict(source_run_id=identifier, repetition=repetition, page=case['page'],
                                case_id=case['id'], snapshot_hash=case['snapshot_hash'],
                                source_result_hash=digest(result), source_prepared_hash=digest(original),
                                baseline_hash=digest(checked['parsed']), prepared_hash=digest(prepared),
                                request_hash=digest(prepared['request']), input_hash=prepared['input_hash'],
                                config_hash=digest(config), source_baseline_valid=checked['valid'],
                                image_transport_sha256=original['image_transport_sha256'],
                                image_sha256=case['snapshot']['facsimile'].get('sha256'),
                                generation_id=case['snapshot']['source'].get('v2_generation_id'),
                                prices=config['prices'], historical_call_cost_usd=result.get('cost_usd'))
                items.append((case, prepared, manifest))
    if len({p['schema_hash'] for _, p, _ in items}) != 1:
        raise ValueError('All source extractions must use the same frozen schema')
    random.Random(seed).shuffle(items)
    plan = dict(kind='verification', study=STUDY, size=len(items), created_at=now(),
                evaluation=False, bypass_cache=True, seed=seed, source_runs=list(source_runs),
                repetitions=repetitions, source_plans=source_plans, policy=policy(),
                system_hash=items[0][1]['system_hash'], schema_hash=items[0][1]['schema_hash'],
                pages=sorted({c['page'] for c, _, _ in items}),
                schedule=[dict(m, schedule_index=i) for i, (_, _, m) in enumerate(items)],
                arms={'A': 'unchanged frozen v3 output', 'B': 'v3 plus one statement-boundary review'},
                estimated_calls_usd=sum(m['historical_call_cost_usd'] or 0 for _, _, m in items),
                cost_note='Historical extraction cost proxy, not a quote or ceiling. Review tokens and transport retries differ.',
                review_protocol={'criteria': list(CRITERIA), 'unit': 'claim, attribution, participant or raw relation',
                                 'primary_outcomes': ['wrong holders corrected', 'mixed voices split correctly',
                                                      'conditions recovered', 'correct attributions retained',
                                                      'new errors and useful content lost'],
                                 'semantic_status': 'pending_independent_review', 'gold_status': 'not_gold',
                                 'limit': 'Known development pages. Repetitions are not independent pages; no corpus accuracy estimate.'})
    check_schedule(plan)
    experiment = store.save_plan(name, plan, ((c, p, m['repetition']) for c, p, m in items))
    preflight = dict(plan, experiment=experiment, status='prepared', inference_calls=0, checks={
        'frozen_v3_outputs': len(source_runs), 'physical_pages': len(plan['pages']),
        'new_baseline_calls': 0, 'cache_bypassed': True, 'source_payloads_and_images_reproduced': True,
        'provider_configuration_preserved': True, 'reasoning_efforts': sorted({str(p['config'].get('reasoning_effort')) for _, p, _ in items}),
        'generator_confidence_and_warnings_withheld': True, 'no_expected_answers_in_requests': True})
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        target.with_name('reviewer.system.txt').write_text(items[0][1]['system_prompt'], encoding='utf-8')
        target.with_name('reviewer.schema.json').write_text(json.dumps(items[0][1]['schema'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return preflight
