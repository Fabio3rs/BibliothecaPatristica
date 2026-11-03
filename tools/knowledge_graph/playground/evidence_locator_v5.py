"""Textual location, independently of quotation fidelity or semantic support.

Reuse the bounded v3/v4 search (90% normalized similarity). Lexical guards are
diagnostics, not vetoes on an otherwise located passage. Frozen policies stay
unchanged; never rewrite quotations, source text or original locators.
"""
from __future__ import annotations

from copy import deepcopy
import re

from . import evidence_locator as v3
from . import evidence_locator_v4 as v4

POLICY = 'local-quote-v5'
VERSION = 'kg-validation-9'
INLINE_DEFINITION = re.compile(r'(?<!\S)([' + v3.SUPERSCRIPTS + r']{1,3})\s+(?=[^\W\d_])')
BOUNDARY_GUARDS = {'truncated_word', 'truncated_word_or_operator'}


def editorial_notes(blocks):
    annotated = []
    for block in blocks:
        item = dict(block)
        if 'aparato' in str(block.get('type', '')).casefold():
            item['type'] = 'apparatus'
        if 'aparato' in str(block.get('tag', '')).casefold():
            item['tag'] = 'apparatus'
        annotated.append(item)
    definitions = v3.editorial_notes(annotated)
    for block in annotated:
        kind = ' '.join(str(block.get(k, '')) for k in ('type', 'tag')).casefold()
        if not any(term in kind for term in ('nota', 'note', 'apparat', 'rodape', 'footnote')):
            continue
        for match in INLINE_DEFINITION.finditer(block.get('text', '')):
            marker = match.group(1)
            record = {'locator': block['id'], 'start': match.start(1),
                      'end': match.end(1), 'text': marker}
            entries = definitions.setdefault(v3._fold(marker), [])
            if record not in entries:
                entries.append(record)
    return definitions


def _locatable(candidate):
    return (candidate.get('guard') not in BOUNDARY_GUARDS
            and candidate.get('score', 0) >= v3.legacy.MIN_SCORE)


def locate_quote(quote, text, *, definitions=None, names=()):
    result = deepcopy(v4.locate_quote(quote, text, definitions=definitions, names=names))
    result.update(policy=POLICY, validation_target='textual_location')
    candidate = result.get('review_candidate')
    if result['method'] == 'review_only' and candidate and _locatable(candidate):
        guard = result.get('reason')
        result.update({k: v for k, v in candidate.items() if k != 'guard'})
        result.update(accepted=True, located=True, position_resolved=True,
                      status='accepted', candidate_count=1,
                      acceptance_rule='located_similar_passage',
                      diagnostics=list(dict.fromkeys(result['diagnostics'] + [guard])))
        result.pop('review_candidate', None)
        result.pop('reason', None)
        result['candidates'] = []
    elif result['method'] == 'ambiguous' and result['candidates']:
        # Finding the passage does not entitle us to choose one occurrence.
        result['accepted'] = all(_locatable(c) for c in result['candidates'])
        result['diagnostics'] = list(dict.fromkeys(result['diagnostics'] + ['multiple_source_positions']))
    result['quote_fidelity'] = ('exact' if result['method'] == 'exact' and result['accepted'] else
                                'normalized' if result['method'] in {'normalized', 'note_marker'} and result['accepted'] else
                                'requires_review')
    result['semantic_support'] = 'not_evaluated'
    return result


def locate_on_page(quote, locator, blocks, *, definitions=None, names=()):
    originals = {b['id']: b['text'] for b in blocks}
    result = locate_quote(quote, originals.get(locator), definitions=definitions, names=names)
    result.update(locator=locator, resolved_locator=locator if result['position_resolved'] else None,
                  search_scope='referenced_block')
    for candidate in result.get('candidates', []):
        candidate['locator'] = locator
    if result['located']:
        return result
    candidates = []
    for block_id, text in originals.items():
        if block_id == locator:
            continue
        other = locate_quote(quote, text, definitions=definitions, names=names)
        if not other['accepted']:
            continue
        if other['position_resolved']:
            candidates.append(dict(other, locator=block_id))
        else:
            candidates.extend(dict(c, locator=block_id) for c in other['candidates'])
    if not candidates:
        return result
    candidates.sort(key=lambda c: (-c['score'], c['locator'], c['start']))
    near = [c for c in candidates if candidates[0]['score'] - c['score'] < v3.legacy.AMBIGUITY_MARGIN]
    if len(near) == 1:
        result = deepcopy(near[0])
        result.update(locator=locator, resolved_locator=near[0]['locator'],
                      search_scope='current_page', accepted=True, located=True,
                      position_resolved=True)
    else:
        result.update(accepted=True, located=True, position_resolved=False,
                      method='ambiguous', status='review', candidate_count=len(near),
                      candidates=near[:20], search_scope='current_page',
                      diagnostics=['multiple_source_positions'],
                      reason='Located on current page; choose an occurrence for a precise position')
    result['diagnostics'] = list(dict.fromkeys(result.get('diagnostics', []) + ['locator_mismatch']))
    return result
