"""Experimental v3 role checks and deterministic, qualified page-local projection.

This checks references, not historical interpretation. A wrong speaker remains
a semantic error even if its ID exists. Never manufacture a stance from a role.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .common import digest

SCHEMA = 'kg-extraction-v3'
VERSION = 'kg-statements-1'
ROLE_PREDICATES = {'agent': 'HAS_AGENT', 'target': 'HAS_TARGET',
                   'cited_authority': 'CITES_AUTHORITY', 'subject': 'HAS_SUBJECT'}
RESERVED = {'AFFIRMS', 'DEFENDS', 'REFUTES', 'REPORTS', 'QUESTIONS', 'ATTRIBUTES_TO',
            *ROLE_PREDICATES.values()}


def project(data):
    """Project explicit fields only; caller must validate references first."""
    edges = []
    for claim in data['claims']:
        for field in ('attributions', 'participants'):
            for index, item in enumerate(claim[field]):
                attribution = field == 'attributions'
                edges.append({
                    'id': f'@v3:{claim["id"]}:{field}:{index}',
                    'source_id': item['entity_id'] if attribution else claim['id'],
                    'target_id': claim['id'] if attribution else item['entity_id'],
                    'predicate': item['stance'] if attribution else ROLE_PREDICATES[item['role']],
                    'scope': item['scope'] if attribution else 'within_proposition',
                    'claim_mode': claim['mode'], 'evidence_ids': list(item['evidence_ids']),
                    'derived_from': {'claim_id': claim['id'], 'field': field, 'index': index},
                    'semantic_status': 'pending_review',
                })
    return {'version': VERSION, 'relations': edges,
            'note': 'Deterministic projection of explicit model fields; not additional model output or verified facts.'}


def validate_roles(checked):
    if not checked.get('schema_valid') or checked['parsed'].get('schema_version') != SCHEMA:
        return checked
    out = deepcopy(checked)
    data = out['parsed']
    entities = {e['id'] for e in data['entities']}
    evidence = {e['id'] for e in data['evidence']}
    errors = []
    for group in ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence'):
        if any(o['id'].startswith('@v3:') for o in data[group]):
            errors.append('Reserved projection ID prefix @v3: cannot be supplied by model')
    for claim in data['claims']:
        for field in ('attributions', 'participants'):
            identities = []
            for index, item in enumerate(claim[field]):
                ref = f'claims/{claim["id"]}/{field}/{index}'
                if item['entity_id'] not in entities:
                    errors.append(f'{ref}: entity_id must reference an emitted entity')
                for eid in item['evidence_ids']:
                    if eid not in evidence:
                        errors.append(f'{ref}: missing evidence {eid}')
                identities.append((item['entity_id'], item.get('stance', item.get('role')), item.get('scope')))
            if any(n > 1 for n in Counter(identities).values()):
                errors.append(f'claims/{claim["id"]}/{field}: duplicate role or attribution')
        if not claim['attributions']:
            out['diagnostics'].append({'code': 'statement_attribution_unresolved', 'object_id': claim['id'],
                                       'message': 'Proposição preservada sem atribuição; nenhum titular foi inferido dos participantes.'})
    for relation in data['relations']:
        if relation['predicate'].strip().upper() in RESERVED:
            errors.append(f'relations/{relation["id"]}: {relation["predicate"]} belongs in claim attributions/participants')
    out['version'] += '+' + VERSION
    out['statement_contract'] = VERSION
    out['statement_implementation_sha256'] = digest(Path(__file__).read_bytes())
    if errors:
        out['structural_errors'].extend(errors)
        out.update(structural_valid=False, graph_inspectable=False, valid=False, validated_extraction=None)
    out['graph_projection'] = project(data) if out['structural_valid'] else None
    return out


def graph_view(validation):
    """Separate viewer/comparison representation; raw parsed extraction is untouched."""
    data = deepcopy(validation['parsed'])
    if validation.get('structural_valid') and validation.get('graph_projection'):
        data['relations'].extend(deepcopy(validation['graph_projection']['relations']))
    return data
