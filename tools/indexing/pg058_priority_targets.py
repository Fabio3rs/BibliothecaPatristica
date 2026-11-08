"""PG058 appendix references to bracket pagination spanning PG057 and PG058.

The opening notice, not the PG volume's printed column headers, defines this
namespace. Scores rank literal/sequential evidence; they are not certification.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'pg058-bracket-priority-targets-v1'
NAMESPACE = 'chrysostom_matthew_bracket_page'
MARKER = re.compile(r'\[\s*(\d{1,4})\s*\]')
NOTICE = 'Notarum quæ in sequentibus indicibus usurpantur'
BODY_SCOPES = (('PG057', 25, 395), ('PG058', 9, 328))
CLOSING = 'Quæ sequuntur ex Montef. excerpta sunt.'


def _physical(path):
    return int(Path(path).stem.rsplit('-', 1)[1])


def eligible(entry):
    """Apply only inside the two indexes governed by the inspected notice."""
    if entry.get('volume') != 'PG058':
        return False
    try:
        physical = int(entry.get('physical') or _physical(entry.get('source', '')))
    except (ValueError, TypeError):
        return False
    return 394 <= physical <= 422 and entry.get('entry_kind', 'index_entry') == 'index_entry'


def supported_sequence(occurrences, *, max_number_gap=18, max_file_gap=16):
    """Dominant increasing chain excludes OCR number corruption and citations.

    Retain nearby repeated markers as alternatives: a printed page may continue
    across files and OCR can repeat the marker. Never silently correct digits.
    """
    ordered = sorted(occurrences, key=lambda a: (a['order'], a['span'][0]))
    lengths, parents, previous = [], [], {}
    for i, anchor in enumerate(ordered):
        options = [(1, -1)]
        for number in range(max(1, anchor['number']-max_number_gap), anchor['number']):
            for j in previous.get(number, []):
                if 0 <= anchor['order']-ordered[j]['order'] <= max_file_gap:
                    options.append((lengths[j]+1, j))
        length, parent = max(options, key=lambda pair: (pair[0], -pair[1]))
        lengths.append(length)
        parents.append(parent)
        previous.setdefault(anchor['number'], []).append(i)
    selected = set()
    if lengths:
        i = max(range(len(ordered)), key=lambda j: (lengths[j], -j))
        while i >= 0:
            selected.add(i)
            i = parents[i]
    chosen = {}
    for i in selected:
        chosen.setdefault(ordered[i]['number'], []).append(ordered[i])
    accepted, rejected = [], []
    for i, anchor in enumerate(ordered):
        supported = any(abs(anchor['order']-other['order']) <= 4
                        for other in chosen.get(anchor['number'], []))
        (accepted if supported else rejected).append(dict(anchor, in_dominant_chain=i in selected))
    return accepted, rejected


def build_map(root: Path):
    root = Path(root).resolve()
    notice_file, = (root/'teste/PG058/text').glob('*-394.txt')
    notice_raw = notice_file.read_text()
    notice_blocks = [b for b in blocks(notice_raw) if NOTICE in b['body']]
    if len(notice_blocks) != 1:
        raise ValueError('PG058 numbering notice not uniquely identified')
    b = notice_blocks[0]
    notice = dict(file=str(notice_file), raw=b['body'], span=[b['body_start'], b['body_end']],
                  sha256=hashlib.sha256(notice_raw.encode()).hexdigest())
    result = dict(version=VERSION, namespace=NAMESPACE, notice=notice, pages=[], boundaries=[],
                  parameters=dict(max_number_gap=18, max_file_gap=16, max_candidate_interval_files=12))
    occurrences = []
    for volume, first, last in BODY_SCOPES:
        files = sorted((root/'teste'/volume/'text').glob('*.txt'), key=lambda p: (_physical(p), p.name))
        files = [p for p in files if first <= _physical(p) <= last]
        if not files or _physical(files[0]) != first or _physical(files[-1]) != last:
            raise ValueError(f'{volume}: body boundary missing')
        if len({_physical(p) for p in files}) != len(files):
            raise ValueError(f'{volume}: ambiguous physical source')
        for path in files:
            physical, raw = _physical(path), path.read_text()
            cutoff = len(raw)
            if volume == 'PG058' and physical == last:
                if raw.count(CLOSING) != 1:
                    raise ValueError('PG058 final homily/addendum boundary changed')
                cutoff = raw.index(CLOSING)
                result['boundaries'].append(dict(file=str(path), raw=CLOSING,
                    span=[cutoff, cutoff+len(CLOSING)]))
            page = dict(file=str(path), volume=volume, physical=physical,
                        order=len(result['pages']), sha256=hashlib.sha256(raw.encode()).hexdigest())
            main = [b for b in blocks(raw) if b['kind'] == 'texto_principal']
            letters = ''.join(c for b in main for c in b['body'] if c.isalpha())
            greek = sum('\u0370' <= c <= '\u03ff' or '\u1f00' <= c <= '\u1fff' for c in letters)
            page['greek_main_text'] = bool(letters and greek / len(letters) > .35)
            result['pages'].append(page)
            for block in blocks(raw):
                if block['kind'] not in {'texto_principal', 'nota_marginal'}:
                    continue
                a, z = block['body_start'], min(block['body_end'], cutoff)
                for match in MARKER.finditer(raw, a, max(a, z)):
                    occurrences.append(dict(page, number=int(match[1]), raw=match.group(),
                        span=list(match.span()), block_kind=block['kind'],
                        snippet=raw[max(a, match.start()-60):min(z, match.end()+100)]))
    anchors, rejected = supported_sequence(occurrences)
    if len(anchors) < 100 or not any(a['number'] == 1 for a in anchors) or not any(a['number'] == 845 for a in anchors):
        raise ValueError('PG057/058 bracket sequence insufficient; review source changes')
    result.update(anchors=anchors, rejected_markers=rejected, numbers={})
    for anchor in anchors:
        result['numbers'].setdefault(str(anchor['number']), []).append(anchor)
    result['numeric_candidates'] = {str(n): _candidates(n, result) for n in range(1, 846)}
    return result


def _candidates(number, mapping):
    """Literal pages plus bounded continuations; missing markers use both sides.

    Greek-body files rank above intervening Latin translation files, which remain
    lower-ranked candidates. Positional abbreviations are preserved, not treated
    as reliable OCR coordinates or applied to an entire physical scan.
    """
    direct = mapping['numbers'].get(str(number), [])
    found = []
    for anchor in direct:
        found.append(dict(file=anchor['file'], physical=anchor['physical'], volume=anchor['volume'],
            score=.91, evidence=[dict(kind='sequence_supported_bracket', **anchor)],
            uncertainties=['repeated_marker_alternatives'] if len(direct) > 1 else []))
    lefts = direct or [a for a in mapping['anchors'] if 0 < number-a['number'] <= 9]
    rights = [a for a in mapping['anchors'] if 0 < a['number']-number <= 9]
    pairs = [(r['number']-l['number'], r['order']-l['order'], l, r)
             for l in lefts for r in rights
             if 0 <= r['order']-l['order'] <= 12
             and (r['order'] > l['order'] or r['span'][0] > l['span'][0])]
    if pairs:
        best = min((gap, distance) for gap, distance, _, _ in pairs)
        for gap, distance, left, right in pairs:
            if (gap, distance) != best:
                continue
            for page in mapping['pages'][left['order']:right['order']+1]:
                uncertain = ['physical_page_interval_not_passage_certification']
                if not direct:
                    uncertain.append('bracket_marker_missing_or_corrupt')
                if not page['greek_main_text']:
                    uncertain.append('possible_parallel_latin_translation')
                found.append(dict(file=page['file'], physical=page['physical'], volume=page['volume'],
                    score=(.72 if direct else .59) if page['greek_main_text'] else .38,
                    evidence=[dict(kind='bracket_page_extent' if direct else 'bounded_missing_bracket',
                                   number=number, left=left, right=right)], uncertainties=uncertain))
    # The final printed page continues onto the bounded homily-closing file.
    if number == 845 and direct:
        for anchor in direct:
            for page in mapping['pages'][anchor['order']+1:]:
                if page['order']-anchor['order'] <= 2 and page['greek_main_text']:
                    found.append(dict(file=page['file'], physical=page['physical'], volume=page['volume'],
                        score=.72, evidence=[dict(kind='final_page_to_reviewed_body_closing',
                            number=number, left=anchor, boundaries=mapping.get('boundaries', []))],
                        uncertainties=['physical_page_interval_not_passage_certification']))
    return found


def reference_locators(entry):
    raw = entry.get('reference_raw', entry.get('ref_raw', ''))
    numbers = sorted({int(n) for n in re.findall(r'(?<!\w)\d+(?!\w)', raw)})
    warnings = []
    if re.search(r'\bib(?:id(?:em)?)?\.', raw, re.I):
        warnings.append('ibid_context_not_reconstructed')
    if re.search(r'\b(?:seq|sqq)\.?', raw, re.I):
        warnings.append('following_extent_not_fully_determined')
    if not numbers:
        warnings.append('no_explicit_numeric_locator')
    return numbers, warnings


def locate(entry, mapping):
    if not eligible(entry):
        return []
    numbers, warnings = reference_locators(entry)
    found = {}
    for number in numbers:
        for candidate in mapping['numeric_candidates'].get(str(number), []):
            target = found.setdefault(candidate['file'], dict(file=candidate['file'],
                physical=candidate['physical'], volume=candidate['volume'], score=0,
                method=VERSION, namespace=NAMESPACE, evidence=[], uncertainties=[]))
            target['score'] = max(target['score'], candidate['score'])
            reference = dict(kind='index_reference', number=number, source=entry.get('source'),
                raw=entry.get('reference_raw', entry.get('ref_raw', '')),
                span=entry.get('reference_span'), notice=mapping['notice'])
            for evidence in candidate['evidence'] + [reference]:
                if evidence not in target['evidence']:
                    target['evidence'].append(evidence)
            target['uncertainties'] = sorted(set(target['uncertainties'] + candidate['uncertainties'] + warnings
                + ['index_owner_attribution_not_revalidated', 'editorial_position_qualifier_not_resolved']))
    return sorted(found.values(), key=lambda c: (-c['score'], c['volume'], c['physical'], c['file']))


def diagnose(entry, mapping):
    numbers, reasons = reference_locators(entry)
    missing = [n for n in numbers if not mapping['numeric_candidates'].get(str(n))]
    if missing:
        reasons.append('outside_work_or_no_bounded_marker')
    if not eligible(entry):
        reasons.append('outside_reviewed_index_scope')
    candidates = locate(entry, mapping)
    return dict(version=VERSION, status='candidate' if candidates else 'pending',
        locator_count=len(numbers), located_locator_count=len(numbers)-len(missing) if eligible(entry) else 0,
        numbers_without_anchor=missing, candidate_files=len(candidates), reasons=reasons)
