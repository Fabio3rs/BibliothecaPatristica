"""Recover Cedrenus physical page candidates from long inline numeric sequences.

The reviewed index points to large numbers inserted in the text, not PG column
headers. Typography is flattened in OCR: consecutive sequences identify likely
markers mechanically, while uncertainties remain attached to every candidate.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'cedrenum-internal-targets-v1'
NUMBER = re.compile(r'(?<![\w])\d{1,4}(?![\w])')
SCOPES = (
    ('I', 'PG121', 18, 589, 'Multi ante nos religiosi', None),
    ('II', 'PG122', 12, 189, 'CEDRENI HISTORIARUM', 'MONITUM EDITORUM.'),
)


def numeric_occurrences(raw, *, file, physical, volume, cutoff=None):
    """Only main-text Arabic numbers; exclude bracket pagination and note calls."""
    result = []
    for block in blocks(raw):
        if block['kind'] != 'texto_principal':
            continue
        a, b = block['body_start'], min(block['body_end'], len(raw) if cutoff is None else cutoff)
        if a >= b:
            continue
        masked = [(m.start()+a, m.end()+a) for m in
                  re.finditer(r'\[[^\]]*\]|\([^)]*\)', raw[a:b])]
        for match in NUMBER.finditer(raw, a, b):
            if any(x <= match.start() < y for x, y in masked):
                continue
            local = raw[max(a, match.start()-65):min(b, match.end()+65)]
            letters = [c for c in local if c.isalpha()]
            greek = sum('\u0370' <= c <= '\u03ff' or '\u1f00' <= c <= '\u1fff' for c in letters)
            if not letters or greek / len(letters) > .35:
                continue
            result.append(dict(number=int(match.group()), file=str(file), physical=physical,
                               volume=volume, span=list(match.span()), raw=match.group(),
                               snippet=local, sha256=hashlib.sha256(raw.encode()).hexdigest()))
    return result


def sequence_anchors(occurrences, *, min_chain=15):
    """Longest supported chain in the reviewed body, not a page/number offset.

    A jump may cross at most fourteen missing/visible numbers and eight physical
    files. Disconnected short chains (dates, incidental counts) are rejected.
    Same-number occurrences near selected anchors remain explicit alternatives.
    """
    ordered = sorted(occurrences, key=lambda x: (x['physical'], x['span'][0]))
    prior, lengths, parents = {}, [], []
    for i, anchor in enumerate(ordered):
        options = [(1, -1)]
        for n in range(max(0, anchor['number']-14), anchor['number']):
            for j in prior.get(n, []):
                if 0 <= anchor['physical']-ordered[j]['physical'] <= 8:
                    options.append((lengths[j]+1, j))
        length, parent = max(options, key=lambda v: (v[0], -v[1]))
        lengths.append(length)
        parents.append(parent)
        prior.setdefault(anchor['number'], []).append(i)
    selected = set()
    # Keep the dominant sequence only. Secondary long chains may be dates or
    # counts recurring in the narrative and must not become competing markers.
    if lengths:
        i = max(range(len(ordered)), key=lambda i: (lengths[i], -i))
        if lengths[i] >= min_chain:
            while i >= 0:
                selected.add(i)
                i = parents[i]
    selected_by_number = {}
    for i in selected:
        selected_by_number.setdefault(ordered[i]['number'], []).append(ordered[i])
    return [dict(a, sequence_support=True) for a in ordered
            if any(abs(a['physical']-b['physical']) <= 1
                   for b in selected_by_number.get(a['number'], []))]


def build_map(root: Path) -> dict:
    root = Path(root).resolve()
    result = dict(version=VERSION, namespace='internal_text_number_with_roman_namespace',
                  partitions={}, boundaries=[], limitations=[
                      'roman_partition_is_editorial_candidate_not_certified',
                      'II_below_278_not_extrapolated',
                      'OCR_has_no_reliable_font_size'])
    notice = next((root/'teste/PG122/text').glob('*-767.txt'))
    text = notice.read_text()
    quote = 'Revocatur Lector ad numeros grandiores textui insertos.'
    if text.count(quote) != 1:
        raise ValueError('Cedrenus index numbering notice must be uniquely present')
    result['notice'] = dict(file=str(notice), raw=quote, span=[text.index(quote), text.index(quote)+len(quote)])
    for roman, volume, first, last, opening, closing in SCOPES:
        pages, occurrences = [], []
        files = sorted((root/'teste'/volume/'text').glob('*.txt'), key=lambda p: (int(p.stem.rsplit('-', 1)[1]), p.name))
        selected = [p for p in files if first <= int(p.stem.rsplit('-', 1)[1]) <= last]
        for path in selected:
            physical = int(path.stem.rsplit('-', 1)[1])
            raw = path.read_text()
            cutoff = len(raw)
            if physical == first and raw.count(opening) != 1:
                raise ValueError(f'{volume} body opening changed; review section boundary')
            if physical == last and closing:
                if raw.count(closing) != 1:
                    raise ValueError(f'{volume} body closing changed; review section boundary')
                cutoff = raw.index(closing)
            if physical in (first, last):
                q = opening if physical == first else (closing or '1077')
                if raw.count(q) != 1:
                    raise ValueError(f'{volume} boundary evidence not unique: {q}')
                at = raw.index(q)
                result['boundaries'].append(dict(file=str(path), raw=q, span=[at, at+len(q)]))
            pages.append(dict(file=str(path), physical=physical, volume=volume,
                              sha256=hashlib.sha256(raw.encode()).hexdigest()))
            occurrences.extend(numeric_occurrences(raw, file=path, physical=physical, volume=volume, cutoff=cutoff))
        if not pages or pages[0]['physical'] != first or pages[-1]['physical'] != last:
            raise ValueError(f'{volume} reviewed body boundary missing')
        anchors = sequence_anchors(occurrences)
        numbers = {}
        for anchor in anchors:
            numbers.setdefault(str(anchor['number']), []).append(anchor)
        result['partitions'][roman] = dict(volume=volume, pages=pages, anchors=anchors,
                                           numbers=numbers, numeric_occurrences=len(occurrences))
    return result


def _parse_reference(entry, depth=0):
    """Read only literal locators and explicitly preserved inheritance chains."""
    if depth > 30:
        return [], ['inheritance_depth_exceeded'], [['inheritance_depth_exceeded', None]]
    raw = entry.get('reference_raw', entry.get('raw', ''))
    inherited = entry.get('inherited_reference_evidence')
    ibid = re.match(r'\s*(?:ibid(?:em)?|ib)(?:\.|(?=$|[\s,;]))', raw, re.I)
    if ibid:
        if inherited:
            prior, warnings, unknown = _parse_reference(inherited, depth+1)
            warnings += ['inherited_reference_candidate']
        else:
            prior, warnings, unknown = [], ['ibid_without_prior_reference'], [['ibid_without_prior_reference', None]]
        tail = raw[ibid.end():]
        explicit = re.search(r'\b[IVXLCDM]+\s*,\s*(?=\d)', tail)
        undecided = tail[:explicit.start()] if explicit else tail
        for number in re.findall(r'\d+', undecided):
            unknown.append(['ibid_additional_uninterpreted', int(number)])
            warnings.append('ibid_additional_number_not_interpreted')
        if explicit:
            extra, extra_warnings, extra_unknown = _parse_reference({'raw': tail[explicit.start():]}, depth+1)
            prior = list(dict.fromkeys(prior + extra))
            warnings += extra_warnings
            unknown += extra_unknown
        return prior, sorted(set(warnings)), unknown
    namespace = entry.get('inherited_namespace_evidence', {}).get('raw')
    warnings = ['implicit_roman_namespace_candidate'] if namespace else []
    locators, unknown = [], []
    for match in re.finditer(r'\b(?P<roman>[IVXLCDM]+)\s*,\s*(?=\d)|(?P<number>\d{1,4})', raw):
        if match['roman']:
            namespace = match['roman']
        elif namespace:
            pair = (namespace, int(match['number']))
            if pair not in locators:
                locators.append(pair)
        else:
            warnings.append('arabic_without_roman_context')
            unknown.append(['arabic_without_roman_context', int(match['number'])])
    return locators, sorted(set(warnings)), unknown


def reference_locators(entry, depth=0):
    locators, warnings, _ = _parse_reference(entry, depth)
    return locators, warnings


def numeric_candidates(roman, number, mapping):
    partition = mapping['partitions'].get(roman)
    if not partition:
        return []
    anchors = partition['numbers'].get(str(number), [])
    out = []
    for anchor in anchors:
        out.append(dict(file=anchor['file'], physical=anchor['physical'], volume=anchor['volume'],
                        score=.84, evidence=[dict(kind='consecutive_inline_marker', **anchor)], uncertainties=[]))
        for successor in partition['numbers'].get(str(number+1), []):
            if not 0 < successor['physical']-anchor['physical'] <= 2:
                continue
            for page in partition['pages']:
                if anchor['physical'] < page['physical'] <= successor['physical']:
                    out.append(dict(file=page['file'], physical=page['physical'], volume=page['volume'], score=.66,
                                    evidence=[dict(kind='continuation_before_next_marker', left=anchor, right=successor)],
                                    uncertainties=['passage_may_continue_before_next_marker']))
    if anchors:
        return out
    pairs = []
    for left in partition['anchors']:
        if not 0 < number-left['number'] <= 7:
            continue
        for right_number in range(number+1, left['number']+9):
            for right in partition['numbers'].get(str(right_number), []):
                if not 0 <= right['physical']-left['physical'] <= 4:
                    continue
                if left['physical'] == right['physical'] and left['span'][0] >= right['span'][0]:
                    continue
                pairs.append((right['number']-left['number'], right['physical']-left['physical'], left, right))
    if pairs:
        best = min((x[0], x[1]) for x in pairs)
        for gap, distance, left, right in pairs:
            if (gap, distance) != best:
                continue
            for page in partition['pages']:
                if left['physical'] <= page['physical'] <= right['physical']:
                    out.append(dict(file=page['file'], physical=page['physical'], volume=page['volume'], score=.52,
                                    evidence=[dict(kind='bounded_missing_inline_marker', left=left, right=right)],
                                    uncertainties=['marker_missing_or_corrupt', 'bounded_page_interval']))
    return out


def locate(entry, mapping):
    if entry.get('section_id') != 'PG122:index-in-georgium-cedrenum':
        return []
    locators, warnings = reference_locators(entry)
    found = {}
    for roman, number in locators:
        for candidate in numeric_candidates(roman, number, mapping):
            candidate.update(method=VERSION, namespace=mapping['namespace'])
            candidate['uncertainties'] += warnings + [
                'roman_partition_is_editorial_candidate_not_certified', 'font_size_replaced_by_sequence_evidence']
            candidate['evidence'] += [dict(kind='cedrenus_index_locator', roman=roman, number=number,
                source=entry.get('source'), span=entry.get('reference_span'), raw=entry.get('reference_raw'),
                inherited=entry.get('inherited_reference_evidence') or entry.get('inherited_namespace_evidence'),
                notice=mapping.get('notice'))]
            old = found.get(candidate['file'])
            if old:
                old['score'] = max(old['score'], candidate['score'])
                old['evidence'] += candidate['evidence']
                old['uncertainties'] = sorted(set(old['uncertainties'] + candidate['uncertainties']))
            else:
                found[candidate['file']] = candidate
    return sorted(found.values(), key=lambda c: (-c['score'], c['file']))


def diagnose(entry, mapping):
    locators, reasons, unknown = _parse_reference(entry)
    unmatched = [[roman, n] for roman, n in locators if not numeric_candidates(roman, n, mapping)]
    if unmatched:
        reasons += ['number_without_literal_or_bounded_sequence_anchor']
    return dict(locator_count=len(locators)+len(unknown), located_locator_count=len(locators)-len(unmatched),
                unmatched_locators=unmatched+unknown, reasons=sorted(set(reasons)),
                completeness='all_locators_have_candidates' if locators and not unmatched and not unknown else 'partial_or_missing',
                certified=False, version=VERSION)
