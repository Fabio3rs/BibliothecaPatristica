"""Versioned decision contract and conservative, local application of a review."""
from __future__ import annotations

import json
from copy import deepcopy

from jsonschema import Draft202012Validator

from .common import digest
from .validation import evaluate, completion_details, response_text

VERSION = 'kg-stance-review-v1'
VALIDATION_VERSION = 'kg-stance-review-validation-1'
FROZEN_GROUPS = ('concepts', 'entities', 'events', 'evidence', 'warnings')


def without_confidence(value):
    if isinstance(value, list):
        return [without_confidence(v) for v in value]
    if isinstance(value, dict):
        return {k: without_confidence(v) for k, v in value.items() if k != 'model_confidence'}
    return value


def candidate_payload(extraction):
    return without_confidence({k: v for k, v in extraction.items() if k != 'warnings'})


def review_schema(extraction_schema):
    """Reuse the exact v2 replacement objects; do not fork extraction semantics."""
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False,
                'properties': properties, 'required': list(properties)}
    def array(items):
        return {'type': 'array', 'items': deepcopy(items)}
    def decision(group, id_name, actions):
        return obj({id_name: {'type': 'string'},
                    'action': {'type': 'string', 'enum': actions},
                    'replacements': array(extraction_schema['properties'][group]['items']),
                    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 800},
                    'evidence_ids': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'minItems': 1}})
    schema = obj({'schema_version': {'const': VERSION},
                  'claim_decisions': array(decision('claims', 'claim_id', ['keep', 'remove', 'replace', 'split'])),
                  'relation_decisions': array(decision('relations', 'relation_id', ['keep', 'remove', 'replace'])),
                  'new_evidence': array(extraction_schema['properties']['evidence']['items']),
                  'warnings': array(extraction_schema['properties']['warnings']['items'])})
    schema.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                   '$defs': deepcopy(extraction_schema['$defs']), 'title': 'Source-based stance review decisions v1'})
    return schema


def parse_review(raw):
    text, _ = response_text(raw)
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate JSON property: ' + key)
            out[key] = value
        return out
    def reject(value):
        raise ValueError('Non-JSON numeric constant: ' + value)
    return json.loads(text, object_pairs_hook=unique, parse_constant=reject)


def apply_decisions(baseline, review):
    """Enforce coverage, fresh IDs, fixed nodes, decisions and reference closure."""
    derived = deepcopy(baseline)
    original_ids = {x['id'] for group in ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence')
                    for x in baseline[group]}
    new_ids = set()
    for group, key, prefix in (('claims', 'claim_id', 'pV'), ('relations', 'relation_id', 'rV')):
        decisions = review['claim_decisions' if group == 'claims' else 'relation_decisions']
        ids = [d[key] for d in decisions]
        if len(ids) != len(set(ids)) or set(ids) != {o['id'] for o in baseline[group]}:
            raise ValueError(f'Exactly one decision is required for every original {group} object')
        by_id = {d[key]: d for d in decisions}
        output = []
        for old in baseline[group]:
            d = by_id[old['id']]
            action, replacements = d['action'], d['replacements']
            if action in ('keep', 'remove') and replacements:
                raise ValueError('keep/remove must not contain replacements')
            if action == 'replace' and (not replacements or (group == 'claims' and len(replacements) != 1)):
                raise ValueError('replace requires one claim or at least one relation')
            if action == 'split' and len(replacements) < 2:
                raise ValueError('split requires at least two claims')
            if action == 'keep':
                output.append(deepcopy(old))
            for replacement in replacements:
                identifier = replacement['id']
                if not identifier.startswith(prefix) or identifier in original_ids | new_ids:
                    raise ValueError('Replacement requires a fresh ' + prefix + ' ID')
                new_ids.add(identifier)
                output.append(deepcopy(replacement))
        derived[group] = output
    for ev in review['new_evidence']:
        if not ev['id'].startswith('evV') or ev['id'] in original_ids | new_ids:
            raise ValueError('New evidence requires a fresh evV ID')
        new_ids.add(ev['id'])
    derived['evidence'].extend(deepcopy(review['new_evidence']))
    derived['warnings'].extend(deepcopy(review['warnings']))
    evidence_ids = {e['id'] for e in derived['evidence']}
    for d in review['claim_decisions'] + review['relation_decisions']:
        if not set(d['evidence_ids']) <= evidence_ids:
            raise ValueError('Decision references missing evidence')
    nodes = {n['id'] for g in ('concepts', 'entities', 'claims', 'events') for n in derived[g]}
    if any(r[k] not in nodes for r in derived['relations'] for k in ('source_id', 'target_id')):
        raise ValueError('Review leaves dangling relation endpoints; update all affected relations')
    return derived


def evaluate_review(snapshot, result, prepared):
    errors, parsed, derived, checked = [], None, None, None
    try:
        parsed = parse_review(result.get('raw_response', ''))
        errors = [e.message for e in Draft202012Validator(prepared['schema']).iter_errors(parsed)]
        if not errors:
            derived = apply_decisions(prepared['baseline_extraction'], parsed)
            checked = evaluate(snapshot, derived, prepared['extraction_schema'], 'concepts_relations',
                               matching_policy=prepared['validation_policy'])
            errors.extend(checked['structural_errors'])
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    completion = completion_details(result, prepared)
    if completion['output_limit_reached']:
        errors.append('provider_output_limit_reached')
    structural = not errors
    return {'version': VALIDATION_VERSION, 'valid': structural and bool(checked and checked['source_valid']),
            'structural_valid': structural, 'schema_valid': structural,
            'source_valid': checked['source_valid'] if checked else None,
            'parsed': parsed, 'derived_extraction': derived if structural else None,
            'validated_extraction': None, 'graph_inspectable': False,
            'structural_errors': errors, 'source_errors': checked['source_errors'] if checked else [],
            'derived_validation': checked, 'completion': completion,
            'semantic_status': 'pending_independent_review', 'gold_status': 'not_gold',
            'baseline_extraction_hash': digest(prepared['baseline_extraction'])}
