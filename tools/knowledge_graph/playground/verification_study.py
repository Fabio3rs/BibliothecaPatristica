"""Freeze 24 second-pass checks of existing D outputs, without generating again."""
from __future__ import annotations

import base64
import json
import random
from copy import deepcopy
from pathlib import Path

from .ab_runner import input_hash, policy_manifest, verify_source
from .common import ROOT, digest, dumps, now, read_json, readonly
from .providers import normalize_config, request_spec
from .validation_dispatch import evaluate_response
from .verification_contract import VERSION, candidate_payload, review_schema

SOURCE_EXPERIMENT = 'EXP-55b6938cd135'
PAGES = ('PG001:595', 'PG001:463', 'PG004:276', 'PG001:100', 'PG001:350', 'PG001:638',
         'PG001:455', 'PG001:503', 'PG001:514', 'PG001:5', 'PG004:149', 'PG004:192')
PROMPT_PATH = ROOT / 'tools/knowledge_graph/prompts/verify_stance_v1.system.txt'


def verification_policy():
    return dict(extraction=policy_manifest(), review_contract=VERSION,
                review_implementation_sha256=digest(Path(__file__).with_name('verification_contract.py').read_bytes()))


def frozen_image(prepared):
    if not prepared['facsimile_selected']:
        return None
    content = prepared['request']['body']['messages'][1]['content']
    images = [x['image_url']['url'] for x in content if x.get('type') == 'image_url']
    if len(images) != 1:
        raise ValueError('Expected one archived image attachment')
    header, encoded = images[0].split(',', 1)
    if not header.startswith('data:') or not header.endswith(';base64'):
        raise ValueError('Expected a frozen image data URL')
    if digest(base64.b64decode(encoded, validate=True)) != prepared['image_transport_sha256']:
        raise ValueError('Frozen transport image hash mismatch')
    return {'mime': header[5:-7], 'base64': encoded}


def make_prepared(original, extraction, source_run_id):
    p = deepcopy(original)
    p.pop('ab', None)
    p.pop('retry_of', None)
    p.update(system_prompt=PROMPT_PATH.read_text(encoding='utf-8'),
             variant={'id': VERSION, 'description': 'Frozen second-pass claim/relation decisions'},
             mode='stance_review', task='stance_review', schema_version=VERSION,
             extraction_schema=deepcopy(original['schema']), baseline_extraction=deepcopy(extraction),
             baseline_extraction_hash=digest(extraction), source_run_id=source_run_id)
    p['schema'] = review_schema(original['schema'])
    p['user_payload'] = deepcopy(original['user_payload'])
    p['user_payload']['response_schema'] = deepcopy(p['schema'])
    p['user_payload']['candidate_extraction'] = candidate_payload(extraction)
    p['user_payload']['review_constraints'] = {
        'frozen_groups': ['concepts', 'entities', 'events', 'original_evidence'],
        'new_claim_prefix': 'pV', 'new_relation_prefix': 'rV', 'new_evidence_prefix': 'evV',
        'source_status': 'Historical report, not independently established fact',
        'cumulative_summary': 'Context only, never current-page evidence',
    }
    p['user_prompt'] = dumps(p['user_payload'])
    for field in ('system', 'user', 'schema'):
        p[field + '_hash'] = digest(p['schema'] if field == 'schema' else p[field + '_prompt'])
    p['request'] = request_spec(p['config'], p, frozen_image(original))
    p['input_hash'] = input_hash(p)
    p['estimated_input_tokens'] = (len(p['system_prompt']) + len(p['user_prompt'])) // 3
    p['validation_policy'] = verification_policy()['extraction']['matching_policy']
    return p


def prepare_verification(store, *, source_experiment=SOURCE_EXPERIMENT, seed=20261103,
                         name='KG D + independent stance check v1', output=None,
                         benchmark_dir=ROOT / '.work/kg_benchmark_v1', pages=PAGES):
    if output and Path(output).exists():
        raise ValueError('Preflight already exists; resume its experiment')
    if len(pages) != 12 or len(set(pages)) != 12:
        raise ValueError('Verification study requires exactly 12 distinct pages')
    design_path = Path(benchmark_dir) / 'sample_design.json'
    design = read_json(design_path) if design_path.exists() else {}
    items = []
    with readonly(store.path) as con:
        row = con.execute('SELECT plan_json FROM experiments WHERE id=?', (source_experiment,)).fetchone()
        source_plan = json.loads(row[0]) if row else {}
        if source_plan.get('study') != 'semantics-v25' or source_plan.get('kind') != 'ab':
            raise ValueError('Source must be a completed semantics-v25 study')
        if source_plan['arms']['D']['variant'] != 'concepts-v2.5-semantics-confidence':
            raise ValueError('Expected frozen D semantics prompt')
        reserved = set(design.get('reserved_holdout_volumes', [])) | set(source_plan.get('selection', {}).get('reserved_holdout_volumes', []))
        for page in pages:
            identities = list(con.execute('''SELECT r.id,r.status,r.repetition,c.split FROM runs r
                JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? AND c.page=?
                AND json_extract(r.prepared_json,'$.ab.arm')='D' ORDER BY r.repetition''', (source_experiment, page)))
            if len(identities) != 2 or {r['repetition'] for r in identities} != {1, 2}:
                raise ValueError('Exactly two unambiguous D source repetitions required for ' + page)
            if page.split(':')[0] in reserved or any(r['split'] == 'blind_holdout' for r in identities):
                raise ValueError('Blind holdout excluded before source loading')
            for identity in identities:
                if identity['status'] not in ('completed', 'invalid'):
                    raise ValueError('Source output must be terminal; no new baseline calls allowed')
                row = con.execute('''SELECT r.*,c.page,c.snapshot_hash,c.snapshot_json FROM runs r
                    JOIN cases c ON c.id=r.case_id WHERE r.id=?''', (identity['id'],)).fetchone()
                p, config, result = (json.loads(row[k]) for k in ('prepared_json', 'config_json', 'result_json'))
                case = dict(id=row['case_id'], page=page, snapshot_hash=row['snapshot_hash'], snapshot=json.loads(row['snapshot_json']))
                if any(s.get('work_key') in set(design.get('held_work_keys', [])) for s in case['snapshot']['v2']['segments']):
                    raise ValueError('Holdout work excluded')
                if (config != p['config'] or normalize_config(config) != config or config['provider'] != 'meta'
                        or config['api_key_env'] != 'META_API_KEY' or config.get('reasoning_effort') is not None
                        or 'reasoning_effort' in p['request']['body']):
                    raise ValueError('Preserve the frozen Meta profile with reasoning effort omitted')
                if (p['system_hash'] != source_plan['arms']['D']['system_hash']
                        or p['schema_hash'] != source_plan['schema_hash'] or p['schema_version'] != 'kg-extraction-v2'
                        or p['facsimile_policy'] != 'on' or not p['facsimile_selected']):
                    raise ValueError('Frozen D prompt/schema/facsimile differs')
                verify_source(case, p)
                checked = evaluate_response(case['snapshot'], result, p)
                if not checked['structural_valid'] or checked['completion']['output_limit_reached']:
                    raise ValueError('Cannot review missing/truncated/structurally invalid baseline: ' + str(row['id']))
                if result.get('parsed_json') != checked['parsed']:
                    raise ValueError('Stored parsed baseline differs from original raw response')
                reviewed = make_prepared(p, checked['parsed'], row['id'])
                manifest = dict(page=page, source_run_id=row['id'], repetition=row['repetition'], case_id=case['id'],
                                snapshot_hash=case['snapshot_hash'], source_result_hash=digest(result),
                                source_prepared_hash=digest(p), baseline_hash=digest(checked['parsed']),
                                prepared_hash=digest(reviewed), request_hash=digest(reviewed['request']),
                                input_hash=reviewed['input_hash'], config_hash=digest(config),
                                image_transport_sha256=p['image_transport_sha256'], image_sha256=case['snapshot']['facsimile']['sha256'],
                                generation_id=case['snapshot']['source']['v2_generation_id'],
                                estimated_cost_usd=result.get('cost_usd'), prices=config['prices'])
                items.append((case, reviewed, manifest))
    schemas = {p['schema_hash'] for _, p, _ in items}
    if len(schemas) != 1:
        raise ValueError('Response schema must remain identical across all checks')
    random.Random(seed).shuffle(items)
    plan = dict(kind='verification', study=VERSION, size=24, evaluation=False, bypass_cache=True,
                created_at=now(), seed=seed, source_experiment=source_experiment, source_plan_hash=digest(source_plan),
                pages=list(pages), policy=verification_policy(), arms={'A': 'frozen D output', 'B': 'D plus one source check'},
                system_hash=items[0][1]['system_hash'], schema_hash=next(iter(schemas)),
                schedule=[dict(m, schedule_index=i) for i, (_, _, m) in enumerate(items)],
                estimated_calls_usd=sum(m['estimated_cost_usd'] or 0 for _, _, m in items),
                cost_note='Sum of historical D call costs, not a quote; review usage and transient retries can differ.',
                offline_controls=['PG001:2', 'PG004:2'],
                semantic_status='pending_independent_review')
    experiment = store.save_plan(name, plan, ((case, p, m['repetition']) for case, p, m in items))
    preflight = dict(plan, experiment=experiment, status='prepared', inference_calls=0, checks={
        '12_pages_24_frozen_outputs': True, 'baseline_calls': 0, 'shadow_provenance_preserved': True,
        'archived_images_reproduced': True, 'confidence_and_generator_warnings_withheld': True,
        'reasoning_effort_omitted': True, 'baseline_records_unchanged': True})
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        target.with_name('verifier.system.txt').write_text(items[0][1]['system_prompt'], encoding='utf-8')
        target.with_name('verifier.schema.json').write_text(json.dumps(items[0][1]['schema'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return preflight


def check_prepared(store, run, plan, manifest):
    p = run['prepared']
    check = deepcopy(p)
    check.pop('retry_of', None)
    if digest(check) != manifest['prepared_hash'] or p['input_hash'] != manifest['input_hash']:
        raise ValueError('Prepared review changed since preflight')
    if (run['case_id'] != manifest['case_id'] or run['repetition'] != manifest['repetition']
            or run['config'] != p['config'] or digest(p['config']) != manifest['config_hash']):
        raise ValueError('Review identity/configuration changed')
    if p['system_hash'] != plan['system_hash'] or p['schema_hash'] != plan['schema_hash']:
        raise ValueError('Review prompt/schema changed')
    original = store.get_run(manifest['source_run_id'])
    if (digest(original['result']) != manifest['source_result_hash']
            or digest(original['prepared']) != manifest['source_prepared_hash']
            or digest(p['baseline_extraction']) != manifest['baseline_hash']
            or original['result']['parsed_json'] != p['baseline_extraction']):
        raise ValueError('Frozen source result changed; never substitute a different extraction')
    if p['user_payload']['candidate_extraction'] != candidate_payload(p['baseline_extraction']):
        raise ValueError('Candidate contains confidence or differs from the frozen baseline')
    verify_source(store.get_case(run['case_id']), p)
