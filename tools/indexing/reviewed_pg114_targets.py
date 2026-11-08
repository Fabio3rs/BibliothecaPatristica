"""Locate Allatian index references in literal bracket-numbered source regions.

No global PG pagination is applied. Ambiguous secondary numbers and gaps retain
alternatives; scores rank evidence, not probabilities or certification.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'reviewed-allatian-targets-v1'
MARKER = re.compile(r'\[\s*(\d{1,4})\s*\]')
OPENING = 'SCRIPTORUM SYMEONIS METAPHRASTÆ'
CLOSING = 'CERTI AUCTORES GRÆCI,'


def build_map(root: Path) -> dict:
    """Re-read the bounded work, including corrected OCR without a hash gate."""
    pages = sorted(Path(root, 'teste/PG114/text').glob('*.txt'),
                   key=lambda p: (int(p.stem.rsplit('-', 1)[-1]), p.name))
    loaded = [(p, p.read_text()) for p in pages]
    start = [i for i, (_, text) in enumerate(loaded) if OPENING in text]
    end = [i for i, (_, text) in enumerate(loaded) if CLOSING in text]
    if (sum(text.count(OPENING) for _, text in loaded) != 1
            or sum(text.count(CLOSING) for _, text in loaded) != 1
            or len(start) != 1 or len(end) != 1 or start[0] >= end[0]):
        raise ValueError('Allatian work opening/index boundary must be uniquely identified')
    result = dict(version=VERSION, volume='PG114', namespace='bracketed_text_number',
                  anchors=[], pages=[], boundaries=[])
    for i, quote in [(start[0], OPENING), (end[0], CLOSING)]:
        path, text = loaded[i]
        at = text.index(quote)
        result['boundaries'].append(dict(file=str(path.resolve()), span=[at, at + len(quote)], raw=quote))
    # The final page begins a letter before the index. It belongs to the work,
    # but only content preceding the explicit index opening can supply anchors.
    for path, text in loaded[start[0]:end[0] + 1]:
        physical = int(path.stem.rsplit('-', 1)[-1])
        cutoff = text.index(CLOSING) if CLOSING in text else len(text)
        digest = hashlib.sha256(text.encode()).hexdigest()
        result['pages'].append(dict(file=str(path.resolve()), physical=physical, sha256=digest))
        seen = set()
        for block in blocks(text):
            if block['kind'] != 'texto_principal':
                continue
            a, b = block['body_start'], min(block['body_end'], cutoff)
            for match in MARKER.finditer(text, a, b):
                number = int(match.group(1))
                # Repeated OCR columns are retained as additional evidence,
                # but do not create duplicated anchors for sequencing.
                if number in seen:
                    continue
                seen.add(number)
                result['anchors'].append(dict(number=number, file=str(path.resolve()),
                    physical=physical, span=list(match.span()), raw=match.group(), sha256=digest,
                    snippet=text[max(a, match.start()-60):min(b, match.end()+180)]))
    result['numbers'] = {}
    for anchor in result['anchors']:
        result['numbers'].setdefault(str(anchor['number']), []).append(anchor)
    return result


def _numeric_candidates(number, mapping):
    direct = mapping['numbers'].get(str(number), [])
    out = []
    for anchor in direct:
        out.append(dict(file=anchor['file'], physical=anchor['physical'], score=.92,
                        evidence=[dict(kind='literal_bracket_marker', **anchor)], uncertainties=[]))
    if direct:
        # A numbered passage can continue onto the next physical file before
        # its successor marker. Both files are useful navigation candidates.
        for anchor in direct:
            for successor in mapping['numbers'].get(str(number + 1), []):
                if not 0 < successor['physical'] - anchor['physical'] <= 2:
                    continue
                for page in mapping['pages']:
                    if anchor['physical'] < page['physical'] <= successor['physical']:
                        out.append(dict(file=page['file'], physical=page['physical'], score=.72,
                            evidence=[dict(kind='passage_continuation_before_next_marker',
                                           number=number, left=anchor, right=successor)],
                            uncertainties=['passage_may_continue_before_next_marker']))
        return out
    # Missing markers: bounded interval search, never a global offset or an
    # extrapolation. Prefer the closest numeric anchors, preserving tied spans.
    pairs = []
    for left in mapping['anchors']:
        if not 0 < number-left['number'] <= 7:
            continue
        for right in mapping['anchors']:
            if not left['number'] < number < right['number'] <= left['number']+8:
                continue
            if not 0 <= right['physical']-left['physical'] <= 3:
                continue
            if left['physical'] == right['physical'] and left['span'][0] >= right['span'][0]:
                continue
            pairs.append((right['number']-left['number'], right['physical']-left['physical'], left, right))
    if pairs:
        best = min((p[0], p[1]) for p in pairs)
        for gap, distance, left, right in pairs:
            if (gap, distance) != best:
                continue
            for page in mapping['pages']:
                if left['physical'] <= page['physical'] <= right['physical']:
                    out.append(dict(file=page['file'], physical=page['physical'], score=.55,
                        evidence=[dict(kind='bounded_missing_bracket_interval', number=number,
                                       left=left, right=right)],
                        uncertainties=['marker_missing_or_corrupt', 'page_within_bounded_interval']))
    return out


def locate(entry: dict, mapping: dict) -> list[dict]:
    """Return physical files; preserve uncertain primary/secondary roles."""
    if entry.get('volume') != 'PG114' or not entry.get('reference_namespace', '').startswith('bracketed_text_number'):
        return []
    raw = entry.get('reference_raw', '')
    inherited = entry.get('inherited_reference_evidence') if re.search(r'\bib(?:id(?:em)?)?(?:\.|(?=$|[\s,;]))', raw, re.I) else None
    effective = (re.sub(r'\bib(?:id(?:em)?)?\.?', lambda _: inherited['raw'], raw, count=1, flags=re.I)
                 if inherited and inherited.get('raw') else raw)
    tokens = list(re.finditer(r'\d+', effective))
    saints = entry.get('reference_namespace') == 'bracketed_text_number_with_secondary_locator'
    found = {}
    for index, token in enumerate(tokens):
        number = int(token.group())
        role = 'primary_or_secondary_uncertain' if saints else 'primary_bracket_number'
        # Primary locators in the catalog usually lie at 81–116. Typography
        # was flattened by OCR, so smaller trailing integers remain alternatives.
        secondary = saints and index > 0 and number < 80
        for candidate in _numeric_candidates(number, mapping):
            score = candidate['score'] * (.45 if secondary else 1)
            evidence = dict(kind='index_reference', raw=raw, effective_raw=effective,
                            number=number, role=role, source=entry.get('source'),
                            span=entry.get('reference_span'), inherited=inherited)
            uncertainty = list(candidate['uncertainties'])
            if saints:
                uncertainty.append('primary_secondary_typography_missing')
            if secondary:
                uncertainty.append('possible_secondary_locator_alternative')
            if re.search(r'\bseq', raw):
                uncertainty.append('following_extent_not_fully_determined')
            if candidate['file'] not in found:
                found[candidate['file']] = dict(file=candidate['file'], physical=candidate['physical'],
                    volume='PG114', score=score, namespace='bracketed_text_number',
                    method=VERSION, evidence=[], uncertainties=[])
            item = found[candidate['file']]
            item['score'] = max(item['score'], score)
            for ev in candidate['evidence'] + [evidence]:
                if ev not in item['evidence']:
                    item['evidence'].append(ev)
            item['uncertainties'] = sorted(set(item['uncertainties'] + uncertainty))
    return sorted(found.values(), key=lambda c: (-c['score'], c['physical'], c['file']))


def diagnose(entry: dict, mapping: dict) -> dict:
    """Describe unresolved locators without blocking useful page candidates."""
    raw = entry.get('reference_raw', '')
    ibid = bool(re.search(r'\bib(?:id(?:em)?)?(?:\.|(?=$|[\s,;]))', raw, re.I))
    inherited = entry.get('inherited_reference_evidence') if ibid else None
    effective = (re.sub(r'\bib(?:id(?:em)?)?\.?', lambda _: inherited['raw'], raw, count=1, flags=re.I)
                 if inherited and inherited.get('raw') else raw)
    numbers = sorted(set(int(n) for n in re.findall(r'\d+', effective)))
    candidates = locate(entry, mapping)
    missing = [n for n in numbers if not _numeric_candidates(n, mapping)]
    reasons = []
    if ibid and not inherited:
        reasons.append('ibid_without_prior_reference_evidence')
    if missing:
        reasons.append('number_without_literal_or_bounded_anchor')
    if re.search(r'\bseq', raw):
        reasons.append('following_extent_not_fully_determined')
    if entry.get('reference_namespace') == 'bracketed_text_number_with_secondary_locator':
        reasons.append('secondary_locator_numbers_remain_ambiguous')
    return dict(status='candidate' if candidates else 'pending',
                candidate_files=len(candidates), effective_reference=effective,
                numbers_without_anchor=missing, reasons=reasons,
                version=VERSION)
