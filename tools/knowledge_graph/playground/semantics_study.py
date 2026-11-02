"""Add operational semantics as arm D in a new, immutable four-arm experiment."""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .ab_runner import policy_manifest, verify_source
from .common import ROOT, digest, now, read_json, readonly
from .prompt_registry import freeze_template
from .providers import normalize_config
from .stance_study import STUDY_ARMS, reprompt, schedule_cases, study_templates

VARIANT = 'concepts-v2.5-semantics-confidence'


def semantics_templates():
    templates = study_templates()
    candidate = freeze_template(VARIANT, 'concepts_relations')
    directory = ROOT / 'tools/knowledge_graph/prompts'
    contract = (directory / 'semantics_v2.5_contract.txt').read_text(encoding='utf-8').rstrip('\n')
    modes = read_json(directory / 'semantics_v2.5_modes.json')
    prefix, marker, _ = templates['A']['system_prompt'].rpartition('\n\nEXPERIMENT MODE\n')
    expected = contract + '\n\n' + prefix + marker + modes['concepts_relations']
    if candidate['system_prompt'] != expected or candidate['schema_hash'] != templates['A']['schema_hash']:
        raise ValueError('Arm D must differ from A only by the semantic contract and mode instructions')
    templates['D'] = candidate
    return templates


def prepare_semantics_study(store, source_experiment, *, name='KG object semantics v2.5 A/B/C/D',
                            output=None, benchmark_dir=ROOT / '.work/kg_benchmark_v1'):
    if output and Path(output).exists():
        raise ValueError('Preflight file already exists; resume its experiment or choose another output path')
    templates = semantics_templates()
    policy = policy_manifest()
    design_path = Path(benchmark_dir) / 'sample_design.json'
    design = read_json(design_path) if design_path.exists() else {}
    frozen, provenance = {}, []
    estimated_cost = 0.
    with readonly(store.path) as con:
        row = con.execute('SELECT plan_json FROM experiments WHERE id=?', (source_experiment,)).fetchone()
        source_plan = json.loads(row[0]) if row else {}
        if source_plan.get('kind') != 'ab' or source_plan.get('study') != 'stance-v24':
            raise ValueError('Source must be a frozen stance-v24 A/B/C experiment')
        if (tuple(source_plan['arms']) != STUDY_ARMS['stance-v24']
                or source_plan['schedule'] != schedule_cases(source_plan['cases'], source_plan['seed'])
                or source_plan['size'] != len(source_plan['schedule'])):
            raise ValueError('Source A/B/C schedule changed')
        if source_plan['validation'] != policy or source_plan['schema_hash'] != templates['A']['schema_hash']:
            raise ValueError('Frozen source schema or validation policy changed')
        for arm in STUDY_ARMS['stance-v24']:
            if source_plan['arms'][arm]['system_hash'] != templates[arm]['system_hash']:
                raise ValueError('Existing A/B/C prompts must remain byte-identical')
        # Read identities before any page text, including for source runs still planned.
        identities = [dict(r) for r in con.execute('''SELECT r.id,r.case_id,r.status,r.repetition,c.page,c.split,
            json_extract(r.prepared_json,'$.ab') ab_json
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''', (source_experiment,))]
        if len(identities) != source_plan['size'] or any(r['status'] != 'planned' for r in identities):
            raise ValueError('Source A/B/C must be entirely unexecuted; do not silently duplicate paid calls')
        source_states = dict(Counter(r['status'] for r in identities))
        reserved = set(design.get('reserved_holdout_volumes', [])) | set(source_plan['selection']['reserved_holdout_volumes'])
        for manifest in source_plan['cases']:
            candidates = [r for r in identities if r['page'] == manifest['page']
                          and json.loads(r['ab_json'])['arm'] == 'A' and r['repetition'] == 1]
            if len(candidates) != 1:
                raise ValueError('Expected exactly one frozen A/repetition-1 request per source page')
            identity = candidates[0]
            if identity['split'] == 'blind_holdout' or identity['page'].split(':')[0] in reserved:
                raise ValueError('Blind holdout excluded before source loading')
            row = con.execute('''SELECT r.*,c.page,c.snapshot_hash,c.snapshot_json
                FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.id=?''', (identity['id'],)).fetchone()
            case = dict(id=row['case_id'], page=row['page'], snapshot_hash=row['snapshot_hash'],
                        snapshot=json.loads(row['snapshot_json']))
            if any(s.get('work_key') in set(design.get('held_work_keys', [])) for s in case['snapshot']['v2']['segments']):
                raise ValueError('Holdout work excluded')
            p = json.loads(row['prepared_json'])
            config = json.loads(row['config_json'])
            if (case['id'] != manifest['case_id'] or p['config'] != config or normalize_config(config) != config
                    or digest(config) != manifest['config_hash'] or p['user_hash'] != manifest['user_hash']
                    or p['image_transport_sha256'] != manifest['transport_hash']
                    or digest(p['request']) != manifest['request_hash']):
                raise ValueError('Frozen source request/configuration differs from its manifest')
            expected_task = source_plan['schedule'][p['ab']['schedule_index']]
            if (any(p['ab'][k] != v for k,v in expected_task.items())
                    or expected_task['baseline_run_id'] != manifest['run_id']
                    or expected_task['arm'] != 'A' or expected_task['repetition'] != 1
                    or p['ab']['validation_manifest'] != policy or p['validation_policy'] != policy['matching_policy']):
                raise ValueError('Frozen source task identity/policy changed')
            if (p['system_hash'] != templates['A']['system_hash'] or p['schema'] != templates['A']['schema']
                    or p['mode'] != 'concepts_relations' or p['facsimile_policy'] != 'on' or not p['facsimile_selected']):
                raise ValueError('Frozen source prompt/schema/mode/image changed')
            if (config['provider'] != 'meta' or config['api_key_env'] != 'META_API_KEY'
                    or config.get('reasoning_effort') is not None or 'reasoning_effort' in p['request']['body']):
                raise ValueError('Preserve the Meta profile with reasoning effort omitted')
            verify_source(case, p, manifest)
            frozen[manifest['run_id']] = (case, p)
            provenance.append(dict(page=case['page'], prepared_source_run_id=row['id'],
                                   request_hash=digest(p['request']), prepared_hash=digest(p)))
            cost_row = con.execute('''SELECT json_extract(result_json,'$.cost_usd') cost,
                length(json_extract(prepared_json,'$.system_prompt')) prompt_chars FROM runs WHERE id=?''',
                (manifest['run_id'],)).fetchone()
            if cost_row is None or cost_row['cost'] is None or cost_row['prompt_chars'] is None:
                raise ValueError('Historical source cost unavailable for budget estimate')
            delta_chars = sum(len(t['system_prompt']) - cost_row['prompt_chars'] for t in templates.values())
            estimated_cost += 8 * cost_row['cost'] + 2 * max(0, delta_chars) / 3 * config['prices']['input_per_million'] / 1e6
    plan = deepcopy(source_plan)
    plan.update(version='kg-ab-semantics-1', study='semantics-v25', created_at=now(),
                source_stance_experiment=source_experiment, source_stance_plan_hash=digest(source_plan),
                source_stance_states=source_states, prepared_source_requests=provenance,
                arms={arm:{'system_hash':t['system_hash'], 'variant':t['variant']['id']} for arm,t in templates.items()},
                primary_comparison='D_minus_A',
                intervention='D = A + object semantics preamble + operational mode; no B check or C examples',
                comparison_limit='The preamble and operational coverage are a joint intervention; their separate effects are not identified.',
                estimated_calls_usd=estimated_cost,
                cost_note='Eight times historical source cost plus approximate prompt delta (3 chars/token); output length/retries may change total.')
    plan['schedule'] = schedule_cases(plan['cases'], plan['seed'], arms=STUDY_ARMS['semantics-v25'])
    plan['size'] = len(plan['schedule'])

    def tasks():
        for index, task in enumerate(plan['schedule']):
            case, baseline = frozen[task['baseline_run_id']]
            p = reprompt(baseline, templates[task['arm']])
            check = deepcopy(p['request'])
            check['body']['messages'][0]['content'] = baseline['system_prompt']
            if check != baseline['request']:
                raise ValueError('Only the system prompt may differ between arms')
            p['ab'] = dict(task, schedule_index=index, request_hash=digest(p['request']), validation_manifest=policy)
            yield case, p, task['repetition']

    experiment = store.save_plan(name, plan, tasks())
    preflight = dict(plan, experiment=experiment, inference_calls=0, status='prepared', checks={
        'source_abc_unexecuted':True, 'same_frozen_pages':True, 'same_abc_prompts':True,
        'same_schema_and_validation':True, 'archived_images_reproduced':True,
        'prompt_only_difference':True, 'reasoning_effort_omitted':True,
        'independent_repetitions':True, 'historical_rows_reused_without_updates':True})
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for arm, template in templates.items():
            target.with_name(f'arm_{arm}.system.txt').write_text(template['system_prompt'], encoding='utf-8')
    return preflight
