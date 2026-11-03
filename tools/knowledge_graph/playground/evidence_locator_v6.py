"""Explicit omissions map to ordered, separate source intervals, never one span."""
from copy import deepcopy
import re

from . import evidence_locator_v5 as previous

POLICY = 'local-quote-v6'
VERSION = 'kg-validation-10'
editorial_notes = previous.editorial_notes
OMISSION = re.compile(r'\[\s*(?:\.{3}|…)\s*\]|\(\s*(?:\.{3}|…)\s*\)|\.{3}|…')
MAX_FRAGMENTS = 12
MAX_CHAINS = 128


def _parts(quote):
    parts, markers, start = [], [], 0
    for marker in OMISSION.finditer(quote):
        parts.append((start, marker.start()))
        markers.append({'start': marker.start(), 'end': marker.end(), 'text': marker.group()})
        start = marker.end()
    parts.append((start, len(quote)))
    trimmed = []
    for a, b in parts:
        while a < b and quote[a].isspace():
            a += 1
        while b > a and quote[b - 1].isspace():
            b -= 1
        if a < b:
            trimmed.append((a, b))
    return trimmed, markers


def locate_quote(quote, text, *, definitions=None, names=()):
    result = deepcopy(previous.locate_quote(quote, text, definitions=definitions, names=names))
    result['policy'] = POLICY
    if not text or not isinstance(quote, str) or not OMISSION.search(quote):
        return result
    # A literal ellipsis already in the source need not mean an omitted interval.
    if result['accepted'] and result['method'] == 'exact':
        return result
    parts, markers = _parts(quote)
    if len(parts) < 2:
        return result
    result.update(accepted=False, located=False, position_resolved=False,
                  method='not_found', status='review', start=None, end=None,
                  matched_quote=None, score=None, candidate_count=0, candidates=[],
                  contiguous=False, segments=[], omission_markers=markers,
                  fragment_matches=[], quote_fidelity='requires_review',
                  edit_operations=[], ignored_note_markers=[],
                  diagnostics=['explicit_omissions_require_semantic_review'])
    result.pop('review_candidate', None)
    if len(parts) > MAX_FRAGMENTS or len(quote) > 12000:
        return dict(result, reason='Discontinuous quotation exceeds bounded search limits')
    alternatives = []
    for a, b in parts:
        fragment = quote[a:b]
        if sum(c.isalnum() for c in fragment) < 6:
            return dict(result, reason='Insufficient context in an omitted-quotation fragment')
        found = previous.locate_quote(fragment, text, definitions=definitions, names=names)
        result['fragment_matches'].append(dict(found, quote_start=a, quote_end=b, quote=fragment))
        if not found['accepted']:
            return dict(result, reason='At least one quotation fragment could not be located')
        candidates = [found] if found['position_resolved'] else found['candidates']
        if not found['position_resolved'] and found['candidate_count'] > len(candidates):
            return dict(result, reason='Too many fragment occurrences to resolve safely')
        choices = []
        for candidate in candidates:
            segment = deepcopy(candidate)
            for key in ('candidates', 'review_candidate', 'reason'):
                segment.pop(key, None)
            segment.update(quote_start=a, quote_end=b, quote=fragment,
                           accepted=True, position_resolved=True,
                           edit_quote_offset_basis='fragment', policy=POLICY)
            choices.append(segment)
        alternatives.append(choices)
    chains = [[]]
    for choices in alternatives:
        next_chains = []
        for chain in chains:
            for segment in choices:
                if chain and chain[-1]['end'] > segment['start']:
                    continue
                next_chains.append(chain + [segment])
                if len(next_chains) > MAX_CHAINS:
                    return dict(result, reason='Too many ordered fragment combinations to resolve safely')
        chains = next_chains
        if not chains:
            return dict(result, reason='Quotation fragments do not occur in the quoted order without overlap')
    candidates = []
    for chain in chains:
        omitted = [{'start': left['end'], 'end': right['start'],
                    'text': text[left['end']:right['start']]}
                   for left, right in zip(chain, chain[1:])]
        candidates.append(dict(method='segmented', segments=chain, omitted_spans=omitted,
                               score=min(s['score'] for s in chain), start=None, end=None,
                               matched_quote=None, contiguous=False))
    result.update(accepted=True, located=True, candidate_count=len(candidates),
                  score=max(c['score'] for c in candidates))
    if len(candidates) == 1:
        result.update(candidates[0], position_resolved=True, status='accepted')
        result.pop('reason', None)
    else:
        result.update(method='ambiguous', status='review', candidates=candidates,
                      reason='Multiple ordered fragment locations; no unique positions')
        result['diagnostics'].append('multiple_source_positions')
    return result


def _locator(result, locator):
    result['resolved_locator'] = locator if result['position_resolved'] else None
    for segment in result.get('segments', []):
        segment['locator'] = locator
    for candidate in result.get('candidates', []):
        candidate['locator'] = locator
        for segment in candidate.get('segments', []):
            segment['locator'] = locator
    return result


def locate_on_page(quote, locator, blocks, *, definitions=None, names=()):
    if not isinstance(quote, str) or not OMISSION.search(quote):
        result = previous.locate_on_page(quote, locator, blocks, definitions=definitions, names=names)
        return dict(result, policy=POLICY)
    originals = {b['id']: b['text'] for b in blocks}
    result = _locator(locate_quote(quote, originals.get(locator), definitions=definitions, names=names), locator)
    result.update(locator=locator, search_scope='referenced_block')
    if result['located']:
        return result
    alternatives = []
    for block_id, text in originals.items():
        if block_id == locator:
            continue
        other = _locator(locate_quote(quote, text, definitions=definitions, names=names), block_id)
        if other['accepted']:
            alternatives.extend([dict(other, locator=block_id)] if other['position_resolved'] else other['candidates'])
    if not alternatives:
        return result
    if len(alternatives) == 1:
        result = deepcopy(alternatives[0])
        result.update(locator=locator, resolved_locator=alternatives[0]['locator'], position_resolved=True)
    else:
        result.update(method='ambiguous', candidates=alternatives, candidate_count=len(alternatives),
                      position_resolved=False, resolved_locator=None,
                      reason='Multiple source positions on the current page')
    result.update(accepted=True, located=True, search_scope='current_page')
    result['diagnostics'] = list(dict.fromkeys(result.get('diagnostics', []) + ['locator_mismatch']))
    return result
