"""Explicit v4 revalidation; frozen validation implementations remain unchanged."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from pathlib import Path

from .common import digest
from .evidence_locator import editorial_notes
from .evidence_locator_v4 import POLICY, VERSION, locate_quote
from .validation import evaluate_response as evaluate_frozen_response
from .verification_contract import evaluate_review as evaluate_frozen_review


def _upgrade(source, checked):
    out = deepcopy(checked)
    out.update(version=VERSION, matching_policy=POLICY,
               policy_implementation={name: digest(Path(__file__).with_name(name).read_bytes())
                                      for name in ('evidence_locator_v4.py', 'validation_micro.py')})
    if not checked['schema_valid']:
        return out
    data = checked['parsed']
    originals = {'ocr_block': {b['id']: b['text'] for b in source['ocr']['blocks']},
                 'summary_segment': {s['id']: s['summary'] for s in source['v2']['segments']}}
    definitions = editorial_notes(source['ocr']['blocks'])
    names = [e['label'] for e in data['entities'] if e['type'] in {'PERSON', 'PLACE', 'GROUP', 'INSTITUTION'}]
    matches, errors = [], []
    for ev, old in zip(data['evidence'], checked['evidence_matches']):
        # Tolerance is intended for OCR, not for paraphrasing Portuguese summaries.
        match = (locate_quote(ev['quote'], originals[ev['source']].get(ev['locator']),
                              definitions=definitions, names=names, previous=old)
                 if ev['source'] == 'ocr_block' else dict(old, policy=POLICY))
        match.update(evidence_id=ev['id'], source=ev['source'], locator=ev['locator'])
        matches.append(match)
        if not match['accepted']:
            errors.append(f'{ev["id"]}: {match["reason"]} ({ev["source"]}:{ev["locator"]})')
    counts = Counter(m['method'] for m in matches)
    diagnostics = [d for d in out['diagnostics'] if d['code'] not in {'protected_quote_difference', 'approximate_quote_match'}]
    for match in matches:
        if match.get('acceptance_rule'):
            diagnostics.append({'object_id': match['evidence_id'], 'code': match['acceptance_rule'],
                                'message': 'Citação localizada sob v4; diferenças e offsets preservados. Suporte semântico não avaliado.',
                                'rules': match['diagnostics']})
        elif not match['accepted'] and match.get('review_candidate'):
            diagnostics.append({'object_id': match['evidence_id'], 'code': 'protected_quote_difference',
                                'message': f'Candidato requer revisão: {match.get("reason")}.'})
    valid = checked['structural_valid'] and not errors and not checked.get('completion', {}).get('output_limit_reached')
    out.update(evidence_matches=matches, evidence_counts={m: counts[m] for m in checked['evidence_counts']},
               source_errors=errors, source_valid=not errors,
               source_exact=all(m['method'] == 'exact' for m in matches),
               valid=valid, validated_extraction=data if valid else None, diagnostics=diagnostics)
    return out


def evaluate_response(source, result, prepared):
    base = evaluate_frozen_response(source, result, prepared, matching_policy='local-quote-v3')
    return _upgrade(source, base)


def evaluate_review(source, result, prepared):
    # Do not change the frozen prepared payload, schema, decisions or stored run.
    legacy_prepared = dict(prepared, validation_policy='local-quote-v3')
    out = evaluate_frozen_review(source, result, legacy_prepared)
    out.update(version='kg-stance-review-validation-2', matching_policy=POLICY,
               frozen_matching_policy=prepared['validation_policy'])
    if out['derived_validation']:
        derived = _upgrade(source, out['derived_validation'])
        out.update(derived_validation=derived, source_valid=derived['source_valid'],
                   source_errors=derived['source_errors'], valid=out['structural_valid'] and derived['valid'])
        out['policy_implementation'] = derived['policy_implementation']
    return out
