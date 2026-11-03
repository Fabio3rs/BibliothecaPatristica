"""v5 quotation location; structural and historical checks remain frozen."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .common import digest
from .evidence_locator_v5 import POLICY, VERSION, editorial_notes, locate_on_page
from .validation import evaluate_response as evaluate_frozen_response


def upgrade(source, checked):
    out = deepcopy(checked)
    out.update(version=VERSION, matching_policy=POLICY, validation_target='textual_location',
               policy_implementation={n: digest(Path(__file__).with_name(n).read_bytes())
                                      for n in ('evidence_locator_v5.py', 'validation_location.py',
                                                'evidence_locator_v4.py', 'evidence_locator.py',
                                                'evidence_matching.py', 'validation.py')})
    if not checked['schema_valid']:
        return out
    definitions = editorial_notes(source['ocr']['blocks'])
    names = [e['label'] for e in checked['parsed']['entities']
             if e['type'] in {'PERSON', 'PLACE', 'GROUP', 'INSTITUTION'}]
    matches, errors = [], []
    diagnostics = [d for d in out['diagnostics'] if d['code'] not in
                   {'protected_quote_difference', 'approximate_quote_match'}]
    for ev, old in zip(checked['parsed']['evidence'], checked['evidence_matches']):
        match = (locate_on_page(ev['quote'], ev['locator'], source['ocr']['blocks'],
                               definitions=definitions, names=names)
                 if ev['source'] == 'ocr_block' else dict(old, policy=POLICY))
        match.update(evidence_id=ev['id'], source=ev['source'], locator=ev['locator'])
        matches.append(match)
        if not match['accepted']:
            errors.append(f'{ev["id"]}: {match.get("reason", "Passage not located")} ({ev["source"]}:{ev["locator"]})')
        elif match['method'] != 'exact' or match.get('resolved_locator', ev['locator']) != ev['locator']:
            diagnostics.append({'object_id': ev['id'], 'code': 'located_quote_requires_review',
                                'message': f'{ev["id"]}: trecho localizado; confira diferenças e posição. Localização não confirma fidelidade nem interpretação.',
                                'rules': match.get('diagnostics', [])})
    counts = Counter(m['method'] for m in matches)
    valid = checked['structural_valid'] and not errors and not checked.get('completion', {}).get('output_limit_reached')
    out.update(evidence_matches=matches, evidence_counts={m: counts[m] for m in checked['evidence_counts']},
               source_errors=errors, source_valid=not errors,
               source_exact=all(m['method'] == 'exact' for m in matches),
               valid=valid, validated_extraction=checked['parsed'] if valid else None,
               diagnostics=diagnostics)
    return out


def evaluate_response(source, result, prepared):
    return upgrade(source, evaluate_frozen_response(source, result, prepared, matching_policy='local-quote-v3'))


def evaluate_review(source, result, prepared):
    from .verification_contract import evaluate_review as frozen_review
    out = frozen_review(source, result, dict(prepared, validation_policy='local-quote-v3'))
    out.update(version='kg-stance-review-validation-3', matching_policy=POLICY,
               frozen_matching_policy=prepared['validation_policy'])
    if out['derived_validation']:
        derived = upgrade(source, out['derived_validation'])
        out.update(derived_validation=derived, source_valid=derived['source_valid'],
                   source_errors=derived['source_errors'], valid=out['structural_valid'] and derived['valid'])
    return out
