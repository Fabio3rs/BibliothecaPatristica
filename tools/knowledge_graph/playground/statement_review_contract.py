"""Second-pass decisions over v3 statements, with the extraction kept immutable."""
from copy import deepcopy

from jsonschema import Draft202012Validator

from .common import digest, dumps
from .validation import completion_details
from .verification_contract import apply_decisions, parse_review, review_schema

VERSION = 'kg-statement-review-v1'
TASK = 'statement_review'


def schema_for(extraction_schema):
    if extraction_schema['properties']['schema_version'].get('const') != 'kg-extraction-v3':
        raise ValueError('Statement review requires extraction schema v3')
    schema = review_schema(extraction_schema)
    schema['properties']['schema_version']['const'] = VERSION
    schema['title'] = 'Page-local statement boundary and attribution decisions v1'
    return schema


def evaluate_review(snapshot, result, prepared):
    from .validation_dispatch import evaluate_response

    errors, parsed, derived, checked = [], None, None, None
    schema_valid = False
    try:
        parsed = parse_review(result.get('raw_response', ''))
        errors = [e.message for e in Draft202012Validator(prepared['schema']).iter_errors(parsed)]
        schema_valid = not errors
        if schema_valid:
            derived = apply_decisions(prepared['baseline_extraction'], parsed)
            extraction_prepared = {k: deepcopy(prepared[k]) for k in
                                   ('config', 'user_payload', 'ocr_preparation', 'validation_policy')}
            extraction_prepared.update(schema=prepared['extraction_schema'], mode='concepts_relations')
            checked = evaluate_response(snapshot, {'raw_response': dumps(derived)}, extraction_prepared)
            errors.extend(checked['structural_errors'])
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    completion = completion_details(result, prepared)
    if completion['output_limit_reached']:
        errors.append('provider_output_limit_reached')
    structural = schema_valid and not errors
    return {'version': VERSION + '-validation-1', 'matching_policy': prepared['validation_policy'],
            'valid': structural and bool(checked and checked['source_valid']),
            'schema_valid': schema_valid, 'structural_valid': structural,
            'source_valid': checked['source_valid'] if checked else None,
            'parsed': parsed, 'derived_extraction': derived if structural else None,
            'derived_validation': checked, 'validated_extraction': None, 'graph_inspectable': False,
            'structural_errors': errors, 'source_errors': checked['source_errors'] if checked else [],
            'completion': completion, 'baseline_extraction_hash': digest(prepared['baseline_extraction']),
            'semantic_status': 'pending_independent_review', 'gold_status': 'not_gold'}
