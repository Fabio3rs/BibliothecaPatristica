"""Experimental discourse/action contract. References and anchors are not semantic gold."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .common import digest
from .evidence_locator_v5 import locate_quote
from .statement_contract import ROLE_PREDICATES

VERSION = 'kg-discourse-actions-1'
SCHEMA = 'kg-extraction-v3.1'
GROUPS = ('discourse_units', 'concepts', 'entities', 'claims', 'events', 'relations', 'evidence')
ACTION_ROLES = ('agent', 'opponent', 'recipient', 'content', 'theme', 'instrument', 'location', 'other')
RESERVED = {'AFFIRMS', 'DEFENDS', 'REFUTES', 'REPORTS', 'QUESTIONS', 'ATTRIBUTES_TO',
            'PARTICIPATES_IN', 'VOICED_BY', 'INTRODUCED_BY', 'IN_DISCOURSE', 'PRESENTED_IN', 'DESCRIBES_EVENT',
            *ROLE_PREDICATES.values(), *('HAS_' + r.upper() for r in ACTION_ROLES)}


def locate_anchor(anchor, evidence, match, source):
    """Search only inside the evidence's located span(s), preserving Unicode offsets."""
    result = {'evidence_id': anchor['evidence_id'], 'text': anchor['text'],
              'source': evidence['source'], 'locator': evidence['locator'],
              'accepted': False, 'position_resolved': False, 'candidates': [],
              'offset_unit': 'unicode_codepoints', 'semantic_support': 'not_evaluated'}
    if not match or not match.get('accepted'):
        return dict(result, reason='Parent evidence is not located')
    spans = [match] if match.get('position_resolved', match.get('start') is not None) else match.get('candidates', [])
    spans = [fragment for span in spans for fragment in (span.get('segments') or [span])]
    originals = ({b['id']: b['text'] for b in source['ocr']['blocks']}
                 if evidence['source'] == 'ocr_block' else
                 {s['id']: s['summary'] for s in source['v2']['segments']})
    candidates = {}
    for span in spans:
        locator = span.get('resolved_locator') or span.get('locator') or evidence['locator']
        original = originals.get(locator)
        start, end = span.get('start'), span.get('end')
        if original is None or start is None or end is None:
            continue
        located = locate_quote(anchor['text'], original[start:end])
        if not located.get('accepted'):
            continue
        local = [located] if located.get('position_resolved') else located.get('candidates', [])
        for candidate in local:
            a, b = candidate['start'] + start, candidate['end'] + start
            candidates[(locator, a, b)] = {
                'locator': locator, 'start': a, 'end': b, 'matched_quote': original[a:b],
                'method': located['method'], 'diagnostics': located.get('diagnostics', []),
                'edit_operations': candidate.get('edit_operations', []),
                'edit_offset_basis': 'matched_window',
            }
    result['candidates'] = list(candidates.values())
    result['accepted'] = bool(candidates)
    result['candidate_count'] = len(candidates)
    if len(candidates) == 1:
        result.update(next(iter(candidates.values())), position_resolved=True)
    elif candidates:
        result.update(method='ambiguous', reason='Multiple positions within the referenced evidence')
    else:
        result['reason'] = 'Anchor not located inside the referenced evidence span'
    return result


def project(data):
    edges = []

    def edge(obj, field, index, source, target, predicate, evidence_ids, **qualifiers):
        edges.append({'id': f'@v31:{obj["id"]}:{field}:{index}',
                      'source_id': source, 'target_id': target, 'predicate': predicate,
                      'evidence_ids': list(evidence_ids),
                      'derived_from': {'object_id': obj['id'], 'field': field, 'index': index},
                      'semantic_status': 'pending_review', **qualifiers})

    for unit in data['discourse_units']:
        for field, predicate in [('speaker_id', 'VOICED_BY'), ('parent_unit_id', 'INTRODUCED_BY')]:
            if unit[field]:
                edge(unit, field, 0, unit['id'], unit[field], predicate,
                     unit['evidence_ids'], scope=unit['kind'])
    for claim in data['claims']:
        if claim['discourse_unit_id']:
            edge(claim, 'discourse_unit_id', 0, claim['id'], claim['discourse_unit_id'],
                 'IN_DISCOURSE', claim['evidence_ids'], claim_mode=claim['mode'])
        for field in ('attributions', 'participants'):
            for index, item in enumerate(claim[field]):
                attribution = field == 'attributions'
                edge(claim, field, index,
                     item['entity_id'] if attribution else claim['id'],
                     claim['id'] if attribution else item['entity_id'],
                     item['stance'] if attribution else ROLE_PREDICATES[item['role']], item['evidence_ids'],
                     scope=item['scope'] if attribution else 'within_proposition', claim_mode=claim['mode'])
    for event in data['events']:
        qualifiers = {'event_modality': event['modality'], 'action': event['action'],
                      'scope': event['modality'], 'condition_anchor': event['condition_anchor']}
        if event['discourse_unit_id']:
            edge(event, 'discourse_unit_id', 0, event['id'], event['discourse_unit_id'],
                 'PRESENTED_IN', event['evidence_ids'], **qualifiers)
        for index, claim_id in enumerate(event['claim_ids']):
            edge(event, 'claim_ids', index, claim_id, event['id'], 'DESCRIBES_EVENT',
                 event['evidence_ids'], **qualifiers)
        for index, argument in enumerate(event['arguments']):
            if argument['node_id']:
                edge(event, 'arguments', index, event['id'], argument['node_id'],
                     'HAS_' + argument['role'].upper(),
                     list(dict.fromkeys(event['evidence_ids'] + argument['evidence_ids'])),
                     trigger=event['trigger'], mention=argument['mention'], antecedent=argument['antecedent'],
                     **qualifiers)
    return {'version': VERSION, 'relations': edges,
            'note': 'Explicit fields projected with modality. Event agents never generate stance edges; interpretation requires review.'}


def validate_discourse(source, checked, *, mode=None):
    out = deepcopy(checked)
    out.update(discourse_contract=VERSION,
               discourse_implementation_sha256=digest(Path(__file__).read_bytes()))
    out['version'] += '+' + VERSION
    if not out.get('schema_valid'):
        return out
    data = out['parsed']
    groups = {k: {o['id']: o for o in data[k]} for k in GROUPS}
    errors, anchor_errors, anchor_matches = [], [], []
    matches = {m['evidence_id']: m for m in out['evidence_matches']}
    ids = [o['id'] for group in GROUPS for o in data[group]]
    for identifier, count in Counter(ids).items():
        if count > 1:
            errors.append(f'duplicate id across discourse objects: {identifier}')
        if not identifier.strip() or identifier.startswith(('@v3:', '@v31:')):
            errors.append(f'Empty or reserved projection id: {identifier}')
    if mode == 'concepts_only' and data['discourse_units']:
        errors.append('mode: concepts_only requires empty discourse_units')

    def reference(value, group, path, nullable=False):
        if not (nullable and value is None) and value not in groups[group]:
            errors.append(f'{path}: missing {group} reference {value}')

    def evidence_refs(item, path):
        for eid in item['evidence_ids']:
            reference(eid, 'evidence', path)

    def anchor_ref(anchor, path, parent):
        if anchor is None:
            return
        eid = anchor['evidence_id']
        reference(eid, 'evidence', path)
        if eid not in parent['evidence_ids']:
            errors.append(f'{path}: anchor evidence must belong to its containing object')
        if eid in groups['evidence']:
            located = locate_anchor(anchor, groups['evidence'][eid], matches.get(eid), source)
            anchor_matches.append(dict(located, path=path))
            if not located['accepted']:
                anchor_errors.append(f'{path}: {located["reason"]}')

    for unit in data['discourse_units']:
        path = 'discourse_units/' + unit['id']
        reference(unit['speaker_id'], 'entities', path + '/speaker_id', True)
        reference(unit['parent_unit_id'], 'discourse_units', path + '/parent_unit_id', True)
        evidence_refs(unit, path)
        seen, cursor = set(), unit['id']
        while cursor in groups['discourse_units']:
            if cursor in seen:
                errors.append(f'{path}: discourse nesting cycle')
                break
            seen.add(cursor)
            cursor = groups['discourse_units'][cursor]['parent_unit_id']
    for claim in data['claims']:
        path = 'claims/' + claim['id']
        reference(claim['discourse_unit_id'], 'discourse_units', path + '/discourse_unit_id', True)
        for field in ('attributions', 'participants'):
            identities = []
            for index, item in enumerate(claim[field]):
                ref = f'{path}/{field}/{index}'
                reference(item['entity_id'], 'entities', ref)
                evidence_refs(item, ref)
                identities.append((item['entity_id'], item.get('stance', item.get('role')), item.get('scope')))
            if any(n > 1 for n in Counter(identities).values()):
                errors.append(f'{path}/{field}: duplicate role or attribution')
        if not claim['attributions']:
            out['diagnostics'].append({'code': 'statement_attribution_unresolved', 'object_id': claim['id'],
                                       'message': 'No stance inferred from discourse voice or action participants.'})
    for event in data['events']:
        path = 'events/' + event['id']
        reference(event['discourse_unit_id'], 'discourse_units', path + '/discourse_unit_id', True)
        for cid in event['claim_ids']:
            reference(cid, 'claims', path + '/claim_ids')
        if len(set(event['claim_ids'])) != len(event['claim_ids']):
            errors.append(f'{path}: duplicate claim_ids')
        if event['modality'] == 'conditional' and event['condition_anchor'] is None:
            errors.append(f'{path}: conditional action requires condition_anchor')
        anchor_ref(event['trigger'], path + '/trigger', event)
        anchor_ref(event['condition_anchor'], path + '/condition_anchor', event)
        seen = set()
        for index, item in enumerate(event['arguments']):
            ref = f'{path}/arguments/{index}'
            reference(item['node_id'], 'claims' if item['role'] == 'content' else 'entities', ref, True)
            evidence_refs(item, ref)
            anchor_ref(item['mention'], ref + '/mention', item)
            anchor_ref(item['antecedent'], ref + '/antecedent', item)
            identity = digest(item)
            if identity in seen:
                errors.append(f'{ref}: duplicate action argument')
            seen.add(identity)
            if item['node_id'] is None or item['mention'] is None:
                out['diagnostics'].append({'code': 'unresolved_action_argument', 'object_id': event['id'],
                                           'message': f'{ref}: unresolved referent or implicit argument requires semantic review.'})
    for relation in data['relations']:
        if relation['predicate'].strip().upper() in RESERVED or relation['predicate'].strip().upper().startswith('HAS_'):
            errors.append(f'relations/{relation["id"]}: predicate belongs in discourse, attribution or action fields')
    out['structural_errors'].extend(errors)
    out['source_errors'].extend(anchor_errors)
    out['structural_valid'] = not out['structural_errors']
    out['source_valid'] = not out['source_errors']
    out['valid'] = out['structural_valid'] and out['source_valid'] and not out.get('completion', {}).get('output_limit_reached')
    out['graph_inspectable'] = out['structural_valid']
    out['validated_extraction'] = data if out['valid'] else None
    out['anchor_matches'] = anchor_matches
    out['graph_projection'] = project(data) if out['structural_valid'] else None
    return out
