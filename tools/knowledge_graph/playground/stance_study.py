"""Prepare a frozen three-arm stance study; no inference or source rewriting."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .ab_runner import input_hash, policy_manifest, verify_source
from .common import ROOT, digest, now, read_json, readonly
from .prompt_registry import freeze_template
from .providers import normalize_config

VARIANTS = {
    'A': 'concepts-v2.3-navigation-confidence',
    'B': 'concepts-v2.4-stance-confidence',
    'C': 'concepts-v2.4-stance-examples-confidence',
}
STUDY_ARMS = {'stance-v24': ('A', 'B', 'C'), 'semantics-v25': ('A', 'B', 'C', 'D')}


def study_templates():
    templates = {arm: freeze_template(variant, 'concepts_relations') for arm, variant in VARIANTS.items()}
    directory = ROOT / 'tools/knowledge_graph/prompts'
    a = templates['A']['system_prompt']
    start = a.index('FINAL CHECK\n')
    end = a.index('\n\n\nSUBJECT DISCOVERY', start)
    check = (directory / 'stance_v2.4_final_check.txt').read_text(encoding='utf-8').rstrip('\n')
    b = a[:start] + check + a[end:]
    examples = (directory / 'stance_v2.4_contrastive_examples.txt').read_text(encoding='utf-8').rstrip('\n')
    c = b.replace('MODEL-REPORTED CONFIDENCE —', examples + '\n\n\nMODEL-REPORTED CONFIDENCE —', 1)
    if templates['B']['system_prompt'] != b or templates['C']['system_prompt'] != c:
        raise ValueError('Stance prompt differences exceed the versioned check/examples')
    if len({t['schema_hash'] for t in templates.values()}) != 1:
        raise ValueError('All arms must use the identical v2 schema')
    return templates


def schedule_cases(cases, seed, *, arms=('A', 'B', 'C')):
    if not cases or len({c['page'] for c in cases}) != len(cases) or len({c['run_id'] for c in cases}) != len(cases):
        raise ValueError('Study needs distinct source runs and physical pages')
    if not arms or len(set(arms)) != len(arms):
        raise ValueError('Study needs distinct arms')
    schedule = [dict(page=c['page'], baseline_run_id=c['run_id'], arm=arm, repetition=rep)
                for c in cases for arm in arms for rep in (1, 2)]
    random.Random(seed).shuffle(schedule)
    return schedule


def select_diagnostic(rows, excluded_pages, reserved_volumes, count, seed):
    """Select by metadata only, before loading any candidate OCR, summaries or results."""
    if type(count) is not int or count < 0:
        raise ValueError('extra-pages must be a nonnegative integer')
    excluded = set(excluded_pages)
    for page in excluded_pages:
        volume, number = page.rsplit(':', 1)
        excluded.update(f'{volume}:{int(number)+d}' for d in (-1, 1))
    groups = defaultdict(list)
    seen = set()
    for row in sorted(rows, key=lambda r: r['id']):
        page = row['page']
        if (page in excluded or page in seen or row['split'] == 'blind_holdout'
                or page.split(':')[0] in reserved_volumes or row['status'] not in ('completed', 'invalid')):
            continue
        seen.add(page)
        groups[(page.split(':')[0], row['status'])].append(dict(row))
    for values in groups.values():
        values.sort(key=lambda r: digest([seed, r['page'], r['id']]))
    chosen = []
    while len(chosen) < count:
        available = False
        for key in sorted(groups):
            if groups[key] and len(chosen) < count:
                chosen.append(groups[key].pop(0))
                available = True
        if not available:
            raise ValueError(f'Only {len(chosen)} eligible new diagnostic pages; requested {count}')
    return chosen


def reprompt(original, template):
    p = deepcopy(original)
    p.update({k: template[k] for k in ('variant', 'system_prompt', 'system_hash')})
    p['request']['body']['messages'][0]['content'] = p['system_prompt']
    p['estimated_input_tokens'] = (len(p['system_prompt']) + len(p['user_prompt'])) // 3
    p['input_hash'] = input_hash(p)
    for key in ('ab', 'retry_of'):
        p.pop(key, None)
    return p


def prepare_stance_study(store, audit_dir, *, source_experiment='EXP-13073b566bc1',
                         name='KG stance v2.4 A/B/C', extra_pages=8, seed=20261102,
                         output=None, benchmark_dir=ROOT / '.work/kg_benchmark_v1'):
    directory = Path(audit_dir)
    if output and Path(output).exists():
        raise ValueError('Preflight file already exists; resume its experiment or choose another output path')
    audit_sample = read_json(directory / 'sample_manifest.json')
    additional_reviewed = set()
    review_hashes = {}
    for filename in ('page_reviews.jsonl', 'evidence_review_sample.json'):
        path = directory / filename
        if not path.exists():
            continue
        review_hashes[filename] = digest(path.read_bytes())
        with path.open(encoding='utf-8') as stream:
            records = (json.loads(line) for line in stream if line.strip()) if path.suffix == '.jsonl' else json.load(stream)
            additional_reviewed.update(record['page'] for record in records if record.get('page'))
    templates = study_templates()
    design_path = Path(benchmark_dir) / 'sample_design.json'
    design = read_json(design_path) if design_path.exists() else {}
    reserved = set(design.get('reserved_holdout_volumes', [])) | set(audit_sample.get('excluded_holdout_volumes', []))
    frozen, manifests = {}, []
    observed_cost = 0.
    estimated_cost = 0.
    with readonly(store.path) as con:
        row = con.execute('SELECT plan_json FROM experiments WHERE id=?', (source_experiment,)).fetchone()
        source_plan = json.loads(row[0]) if row else {}
        if (source_plan.get('kind') != 'ab' or len(source_plan.get('cases', [])) != 24
                or source_plan.get('arms', {}).get('B', {}).get('system_hash') != templates['A']['system_hash']):
            raise ValueError('Source experiment must be the 24-page v2.3 navigation comparison')
        campaign = source_plan['campaign']
        identities = [dict(r) for r in con.execute('''SELECT r.id,r.status,r.experiment_id,c.page,c.split,
            json_extract(r.prepared_json,'$.ab.arm') arm,r.repetition
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''', (source_experiment,))]
        selected = []
        for old_case in source_plan['cases']:
            eligible = [r for r in identities if r['page'] == old_case['page'] and r['arm'] == 'B'
                        and r['repetition'] == 1 and r['status'] in ('completed', 'invalid')]
            if not eligible:
                raise ValueError(f"Missing terminal v2.3 source for {old_case['page']}")
            selected.append(dict(eligible[-1], stratum='regression', expected_case=old_case))
        excluded = {p['page'] for p in audit_sample['pages']} | {c['page'] for c in source_plan['cases']} | additional_reviewed
        pool = con.execute('''SELECT r.id,r.status,r.experiment_id,c.page,c.split
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''', (campaign,)).fetchall()
        selected.extend(dict(r, stratum='diagnostic_new_pages') for r in
                        select_diagnostic(pool, excluded, reserved, extra_pages, seed))
        reference_config = None
        for identity in selected:
            if identity['split'] == 'blind_holdout' or identity['page'].split(':')[0] in reserved:
                raise ValueError('Blind holdout excluded before source loading')
            row = con.execute('''SELECT r.*,c.page,c.snapshot_hash,c.snapshot_json
                FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.id=?''', (identity['id'],)).fetchone()
            case = dict(id=row['case_id'], page=row['page'], snapshot_hash=row['snapshot_hash'],
                        snapshot=json.loads(row['snapshot_json']))
            snap = case['snapshot']
            old_case = identity.get('expected_case')
            if old_case and (case['id'] != old_case['case_id'] or case['snapshot_hash'] != old_case['snapshot_hash']):
                raise ValueError('Regression snapshot differs from original A/B manifest')
            held_keys = set(design.get('held_work_keys', []))
            if any(s.get('work_key') in held_keys for s in snap['v2']['segments']):
                raise ValueError('Holdout work excluded')
            original = json.loads(row['prepared_json'])
            config = json.loads(row['config_json'])
            if config != original['config'] or normalize_config(config) != config:
                raise ValueError('Frozen configuration cannot be reproduced')
            if (config['provider'] != 'meta' or config['api_key_env'] != 'META_API_KEY'
                    or config.get('reasoning_effort') is not None or 'reasoning_effort' in original['request']['body']):
                raise ValueError('Study requires frozen Meta configuration with reasoning effort omitted')
            if reference_config is None:
                reference_config = config
            if config != reference_config:
                raise ValueError('Selected pages have different inference configurations/prices')
            if (original['schema'] != templates['A']['schema'] or original['mode'] != 'concepts_relations'
                    or original['facsimile_policy'] != 'on' or not original['facsimile_selected']):
                raise ValueError('Frozen schema/mode/attached image differs from study')
            expected_hash = (templates['A']['system_hash'] if identity['stratum'] == 'regression'
                             else source_plan['arms']['A']['system_hash'])
            if original['system_hash'] != expected_hash:
                raise ValueError('Unexpected historical source prompt')
            verify_source(case, original)
            baseline = reprompt(original, templates['A'])
            if identity['stratum'] == 'regression' and baseline['request'] != original['request']:
                raise ValueError('Arm A does not exactly reproduce prior v2.3 request')
            frozen[row['id']] = (case, baseline)
            cost = json.loads(row['result_json'] or '{}').get('cost_usd')
            if cost is None:
                raise ValueError('Source cost unavailable; cannot provide historical budget estimate')
            observed_cost += cost
            # Character-based prompt-token estimate is explicitly not provider tokenization.
            extra_chars = sum(len(t['system_prompt']) - len(original['system_prompt']) for t in templates.values())
            estimated_cost += 6 * cost + 2 * max(0, extra_chars) / 3 * config['prices']['input_per_million'] / 1e6
            manifests.append(dict(run_id=row['id'], page=case['page'], case_id=case['id'],
                stratum=identity['stratum'], source_experiment_id=row['experiment_id'], source_status=row['status'],
                input_hash=baseline['input_hash'], original_input_hash=original['input_hash'],
                original_request_hash=digest(original['request']), request_hash=digest(baseline['request']),
                original_system_hash=original['system_hash'], baseline_reprompted=original['system_hash'] != baseline['system_hash'],
                snapshot_hash=case['snapshot_hash'], generation_id=snap['source']['v2_generation_id'],
                ocr_sha256=snap['source']['ocr_sha256'], image_sha256=snap['facsimile']['sha256'],
                user_hash=baseline['user_hash'], transport_hash=baseline['image_transport_sha256'],
                config_hash=digest(config), prices=config['prices']))
    schedule = schedule_cases(manifests, seed)
    policy = policy_manifest()
    plan = dict(kind='ab', version='kg-ab-stance-2', study='stance-v24', evaluation=False,
        size=len(schedule), bypass_cache=True, created_at=now(), seed=seed, campaign=campaign,
        source_experiment=source_experiment, source_plan_hash=digest(source_plan), audit_sample_hash=digest(audit_sample),
        additional_review_file_hashes=review_hashes,
        selection={'regression_pages':24, 'diagnostic_new_pages':extra_pages, 'adjacent_exclusion':1,
                   'algorithm':'volume/status round robin; seeded SHA256 rank; lowest source ID per page',
                   'excluded_pages':sorted(excluded), 'reserved_holdout_volumes':sorted(reserved),
                   'limitation':'New relative to recorded audit, not independent works or a blind holdout'},
        schedule=schedule, cases=manifests, repetitions=2,
        arms={a:{'system_hash':t['system_hash'], 'variant':VARIANTS[a]} for a,t in templates.items()},
        schema_hash=templates['A']['schema_hash'], validation=policy,
        observed_source_cost_usd=observed_cost, estimated_calls_usd=estimated_cost,
        cost_note='Six times historical source cost plus approximate prompt delta (3 chars/token); output length and retries can change the total.')

    def tasks():
        for index, task in enumerate(schedule):
            case, baseline = frozen[task['baseline_run_id']]
            p = reprompt(baseline, templates[task['arm']])
            check = deepcopy(p['request'])
            check['body']['messages'][0]['content'] = baseline['system_prompt']
            if check != baseline['request']:
                raise ValueError('Arm isolation failed: non-system request content changed')
            p['validation_policy'] = policy['matching_policy']
            p['ab'] = dict(task, schedule_index=index, request_hash=digest(p['request']), validation_manifest=policy)
            yield case, p, task['repetition']

    experiment = store.save_plan(name, plan, tasks())
    preflight = dict(plan, experiment=experiment, inference_calls=0, status='prepared', checks={
        'regression_requests_reproduced':True, 'new_page_sources_reproduced_before_reprompt':True,
        'prompt_only_difference':True, 'archived_images_reproduced':True, 'reasoning_effort_omitted':True,
        'independent_repetitions':True, 'holdout_excluded':True, 'historical_rows_reused_without_updates':True})
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for arm, template in templates.items():
            target.with_name(f'arm_{arm}.system.txt').write_text(template['system_prompt'], encoding='utf-8')
    return preflight
