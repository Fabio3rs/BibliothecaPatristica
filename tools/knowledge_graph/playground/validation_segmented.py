"""Appendable v6 source validation over frozen structural validators."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .common import digest
from .evidence_locator_v6 import POLICY, VERSION, editorial_notes, locate_on_page, locate_quote


def upgrade(source, checked):
    out = deepcopy(checked)
    out.update(version=VERSION, matching_policy=POLICY)
    out.setdefault('policy_implementation', {}).update({name: digest(Path(__file__).with_name(name).read_bytes())
        for name in ('evidence_locator_v6.py', 'validation_segmented.py')})
    if not checked['schema_valid']:
        return out
    data = checked['parsed']
    blocks = source['ocr']['blocks']
    summaries = {s['id']: s['summary'] for s in source['v2']['segments']}
    definitions = editorial_notes(blocks)
    names = [e['label'] for e in data.get('entities', [])
             if e['type'] in {'PERSON', 'PLACE', 'GROUP', 'INSTITUTION'}]
    matches, errors = [], []
    diagnostics = [d for d in out['diagnostics'] if d['code'] not in
                   {'located_quote_requires_review', 'protected_quote_difference', 'approximate_quote_match'}]
    for ev in data['evidence']:
        exists = ev['locator'] in ({b['id'] for b in blocks} if ev['source'] == 'ocr_block' else summaries)
        match = (locate_on_page(ev['quote'], ev['locator'], blocks, definitions=definitions, names=names)
                 if exists and ev['source'] == 'ocr_block' else
                 locate_quote(ev['quote'], summaries.get(ev['locator']) if ev['source'] == 'summary_segment' else None))
        match.update(evidence_id=ev['id'], source=ev['source'], locator=ev['locator'])
        matches.append(match)
        if not match['accepted']:
            errors.append(f'{ev["id"]}: {match.get("reason", "Passage not located")} ({ev["source"]}:{ev["locator"]})')
        elif match['method'] != 'exact' or match.get('resolved_locator', ev['locator']) != ev['locator']:
            diagnostics.append({'object_id': ev['id'], 'code': 'located_quote_requires_review',
                'message': 'Trecho localizado; confira diferenças, omissões e posição. Localização não confirma interpretação.'})
    counts = Counter(m['method'] for m in matches)
    valid = checked['structural_valid'] and not errors and not checked.get('completion', {}).get('output_limit_reached')
    out.update(evidence_matches=matches, evidence_counts={k: counts[k] for k in dict.fromkeys([*checked['evidence_counts'], 'segmented'])},
               source_errors=errors, source_valid=not errors, source_exact=all(m['method'] == 'exact' for m in matches),
               valid=valid, validated_extraction=data if valid else None, diagnostics=diagnostics)
    return out
