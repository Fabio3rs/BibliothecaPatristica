from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from .common import ROOT, KIT, SCHEMA_PATH, digest, dumps, read_json
from .ocr_adapter import prepare_model_ocr

REGISTRY = ROOT / 'tools/knowledge_graph/prompts/registry.json'
MODES = {
    'concepts_only': 'Extract concepts and evidence only. entities, claims, events and relations MUST be empty arrays. Keep warnings where needed.',
    'concepts_relations': 'Prioritize contextual concepts. Include other structures only when useful and supported by current-page evidence.',
    'full_multimodal': 'Prioritize contextual concepts with optional supported structures. If a facsimile is attached, use it to resolve visual ambiguity. Quotes must still be exact substrings of supplied OCR blocks or current summary segments. Do not invent text from the image as an OCR quote.',
}


def variants():
    return read_json(REGISTRY)['variants']


def freeze_template(variant_id, mode, *, system_override=None):
    if mode not in MODES:
        raise ValueError('Unknown extraction mode')
    variant = next((v for v in variants() if v['id'] == variant_id), None)
    if variant is None:
        raise ValueError('Unknown prompt variant')
    system = system_override if system_override is not None else (REGISTRY.parent / variant['system_file']).resolve().read_text(encoding='utf-8')
    if not isinstance(system, str) or not system.strip():
        raise ValueError('System prompt must be nonempty text')
    if variant.get('contract_rules_file'):
        system += '\n\n' + (REGISTRY.parent / variant['contract_rules_file']).resolve().read_text(encoding='utf-8')
    system += '\n\nEXPERIMENT MODE\n' + MODES[mode]
    if variant['few_shot']:
        sample = read_json(KIT / 'page-output.demo.json')
        if mode == 'concepts_only':
            for key in ('entities', 'claims', 'events', 'relations'):
                sample[key] = []
            sample['evidence'] = [e for e in sample['evidence'] if e['id'] == 'ev4']
        system += ('\n\nSYNTHETIC FORMAT EXAMPLE — not historical evidence and not part of the current page. '
                   'Never copy its entities unless supported by the actual input.\nINPUT:\n' +
                   dumps(read_json(KIT / 'page-input.demo.json')) + '\nOUTPUT:\n' + dumps(sample))
    schema_path = (REGISTRY.parent / variant['schema_file']).resolve() if variant.get('schema_file') else SCHEMA_PATH
    schema = read_json(schema_path)
    return {'variant': variant, 'mode': mode, 'system_prompt': system, 'schema': schema,
            'schema_version': schema['properties']['schema_version']['const'],
            'schema_hash': digest(schema), 'system_hash': digest(system)}


def render(snapshot, variant_id, mode, *, attached=False, system_override=None, template=None):
    frozen = deepcopy(template) if template is not None else freeze_template(variant_id, mode, system_override=system_override)
    if frozen['variant']['id'] != variant_id or frozen['mode'] != mode:
        raise ValueError('Frozen prompt does not match the requested variant/mode')
    if digest(frozen['schema']) != frozen['schema_hash'] or digest(frozen['system_prompt']) != frozen['system_hash']:
        raise ValueError('Frozen prompt/schema hash mismatch')
    # Include every current segment and block, but not the redundant generation/raw XML snapshots.
    payload = {k: deepcopy(snapshot[k]) for k in ('source', 'v2')}
    payload['ocr'], ocr_preparation = prepare_model_ocr(snapshot['ocr'])
    payload['facsimile'] = {'attached': attached, 'available': snapshot['facsimile'].get('available', False),
                            'sha256': snapshot['facsimile'].get('sha256') if attached else None}
    payload['response_schema'] = frozen['schema']
    user = dumps(payload)
    return dict(frozen, user_payload=payload, ocr_preparation=ocr_preparation,
                user_prompt=user, user_hash=digest(user))
