"""Conservative page-local correspondences; never canonicalize stored objects."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from itertools import combinations
from .common import digest
from .validation import NODES


def norm(text):
    return ' '.join(unicodedata.normalize('NFC', str(text)).casefold().split())


def label(obj):
    return obj.get('label') or obj.get('proposition') or obj.get('description') or obj['id']


def signature(kind, obj):
    return (kind, obj.get('type', ''), norm(label(obj)), norm(obj.get('meaning', '')),
            obj.get('origin', ''), obj.get('mode', ''), obj.get('narrative_status', ''), obj.get('time_expression'))


def evidence(obj, result):
    return sorted((e['source'], e['locator'], e['quote']) for e in result.get('evidence', []) if e['id'] in obj.get('evidence_ids', []))


def compare_pair(left, right):
    a = (left.get('validation') or {}).get('parsed')
    b = (right.get('validation') or {}).get('parsed')
    if not isinstance(a, dict) or not isinstance(b, dict) or not (left.get('validation') or {}).get('structural_valid') or not (right.get('validation') or {}).get('structural_valid'):
        return {'left': left['id'], 'right': right['id'], 'error': 'Both runs need structurally valid extractions for object matching'}
    aligned, uncertain, changes, confidence_changes = [], [], [], []
    def compare_confidence(kind, obj, other):
        if obj.get('model_confidence') != other.get('model_confidence'):
            confidence_changes.append({'kind': kind, 'left': obj['id'], 'right': other['id'],
                                       'left_model_confidence': obj.get('model_confidence'),
                                       'right_model_confidence': other.get('model_confidence'),
                                       'status': 'uncalibrated_self_reports_not_accuracy'})
    mapping_a, mapping_b = {}, {}
    unique_a, unique_b = [], []
    for kind in NODES:
        used = set()
        for obj in a[kind]:
            matches = [o for o in b[kind] if o['id'] not in used and signature(kind, o) == signature(kind, obj)]
            # A repeated same label+meaning is ambiguous, even before semantic review.
            if len(matches) == 1 and sum(signature(kind, x) == signature(kind, obj) for x in a[kind]) == 1:
                other = matches[0]
                used.add(other['id'])
                key = digest(signature(kind, obj))
                mapping_a[obj['id']] = key; mapping_b[other['id']] = key
                aligned.append({'kind': kind, 'left': obj['id'], 'right': other['id'], 'label': label(obj),
                                'status': 'same_description_not_semantic_gold'})
                compare_confidence(kind, obj, other)
                if evidence(obj, a) != evidence(other, b) or obj.get('grounding') != other.get('grounding'):
                    changes.append({'left': obj['id'], 'right': other['id'], 'left_evidence': evidence(obj, a),
                                    'right_evidence': evidence(other, b), 'left_grounding': obj.get('grounding'), 'right_grounding': other.get('grounding')})
            else:
                unique_a.append({'kind': kind, **obj})
        unique_b.extend({'kind': kind, **o} for o in b[kind] if o['id'] not in used)
    for obj in unique_a:
        tokens = set(re.findall(r'\w+', norm(label(obj))))
        for other in unique_b:
            if obj['kind'] != other['kind']:
                continue
            other_tokens = set(re.findall(r'\w+', norm(label(other))))
            overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
            shared_evidence = set(evidence(obj, a)) & set(evidence(other, b))
            if overlap >= .5 or shared_evidence:
                uncertain.append({'left': obj['id'], 'right': other['id'], 'left_label': label(obj),
                                  'right_label': label(other), 'status': 'UNCERTAIN',
                                  'reason': 'shared quote or lexical overlap; contextual equivalence requires review'})
    def triples(result, mapping, side):
        grouped = defaultdict(list)
        for r in result['relations']:
            key = (mapping.get(r['source_id'], side + ':' + r['source_id']), r['predicate'],
                   mapping.get(r['target_id'], side + ':' + r['target_id']))
            grouped[key].append(r)
        return grouped
    ra, rb = triples(a, mapping_a, 'left'), triples(b, mapping_b, 'right')
    only_left, only_right, relation_changes = [], [], []
    for key in sorted(ra.keys() | rb.keys()):
        left_group, right_group = ra.get(key, []), rb.get(key, [])
        shared = min(len(left_group), len(right_group))
        only_left.extend(left_group[shared:])
        only_right.extend(right_group[shared:])
        for rel_a, rel_b in zip(left_group, right_group):
            compare_confidence('relations', rel_a, rel_b)
            if evidence(rel_a, a) != evidence(rel_b, b) or rel_a['grounding'] != rel_b['grounding']:
                relation_changes.append({'left': rel_a, 'right': rel_b,
                                         'left_evidence': evidence(rel_a, a), 'right_evidence': evidence(rel_b, b)})
    return {'left': left['id'], 'right': right['id'], 'matched': aligned,
            'uncertain_correspondences': uncertain, 'unique_left': unique_a, 'unique_right': unique_b,
            'evidence_differences': changes, 'model_confidence_differences': confidence_changes,
            'relations_only_left': only_left, 'relations_only_right': only_right,
            'relation_evidence_differences': relation_changes}


def compare_runs(store, identifiers):
    if not 2 <= len(set(identifiers)) <= 12:
        raise ValueError('Select 2–12 distinct runs')
    runs = [store.get_run(int(i)) for i in identifiers]
    sources = [store.get_case(r['case_id'])['snapshot']['source'] for r in runs]
    if len({(s['documento'], s['physical_page']) for s in sources}) != 1:
        raise ValueError('Compare runs of the same physical page')
    return {'runs': runs, 'pairs': [compare_pair(a, b) for a, b in combinations(runs, 2)],
            'source_differences': len({digest(s) for s in sources}) > 1,
            'note': 'Descriptions and exact quotes support comparisons, not global entity resolution or verified equivalence.'}
