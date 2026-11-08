"""Cenni's two independently numbered Codex Carolinus tomes inside PL098.

The index notices refer to large numbers inserted in the text. Consecutive
numeric chains supply page candidates where OCR has flattened font sizes.
Never compare these numbers to PL column headings, or combine the two tomes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.indexing.cedrenum_internal_targets import (
    numeric_candidates, numeric_occurrences, sequence_anchors,
)

VERSION = 'pl098-cenni-internal-targets-v1'
SECTIONS = {
    '259e710d3f1ab9578674a55e': ('I', 223, 229),
    '239c5e711775e66e16e76303': ('II', 441, 450),
}
SCOPES = (
    ('I', 31, 223, 'ADMONITIO.',
     'Numeri hujus indicis respondent numeris crassiioribus in textu insertis.'),
    ('II', 248, 441, 'DISSERTATIO.',
     'Numeri hujus indicis respondent numeris crassioribus in textu insertis.'),
)


def eligible(entry):
    section = SECTIONS.get(entry.get('section_id'))
    if not section or entry.get('volume') != 'PL098':
        return False
    try:
        physical = int(Path(entry['source']).stem.rsplit('-', 1)[1])
    except (KeyError, ValueError):
        return False
    return (section[1] <= physical <= section[2]
            and entry.get('reference_namespace') == 'internal_text_number')


def quote_evidence(path, raw, quote):
    if raw.count(quote) != 1:
        raise ValueError(f'PL098 reviewed boundary/notice changed: {path}: {quote}')
    start = raw.index(quote)
    return dict(file=str(path), raw=quote, span=[start, start + len(quote)])


def build_map(root: Path):
    root = Path(root).resolve()
    files = {}
    for path in sorted((root / 'teste/PL098/text').glob('*.txt')):
        physical = int(path.stem.rsplit('-', 1)[1])
        if physical in files:
            raise ValueError(f'Ambiguous PL098 physical identity: {physical}')
        files[physical] = path
    result = dict(version=VERSION, namespace='cenni_tome_internal_number',
                  partitions={}, notices={}, boundaries=[])
    for tome, first, last, opening, notice in SCOPES:
        occurrences, pages = [], []
        for physical in range(first, last + 1):
            path = files[physical]
            raw = path.read_text(encoding='utf-8')
            start, end = 0, len(raw)
            if physical == first:
                proof = quote_evidence(path, raw, opening)
                result['boundaries'].append(proof)
                start = proof['span'][1]
            if physical == last:
                # A mixed page contains the final body and the opening index.
                # Cut at the actual INDEX heading, not at its running header.
                headings = list(re.finditer(r'(?m)^\s*INDEX\s*$', raw))
                if len(headings) != 1:
                    raise ValueError(f'PL098 index heading boundary changed: {path}')
                end = headings[0].start()
                result['notices'][tome] = quote_evidence(path, raw, notice)
            occurrences.extend(a for a in numeric_occurrences(
                raw, file=path, physical=physical, volume='PL098', cutoff=end)
                if a['span'][0] >= start)
            pages.append(dict(file=str(path), physical=physical, volume='PL098',
                              sha256=hashlib.sha256(raw.encode()).hexdigest()))
        anchors = sequence_anchors(occurrences)
        numbers = {}
        for anchor in anchors:
            numbers.setdefault(str(anchor['number']), []).append(anchor)
        result['partitions'][tome] = dict(pages=pages, anchors=anchors, numbers=numbers,
                                         numeric_occurrences=len(occurrences))
    return result


def reference_locators(entry):
    raw = entry.get('reference_raw', entry.get('ref_raw', ''))
    numbers = list(dict.fromkeys(int(n) for n in re.findall(r'\b\d+\b', raw)))
    warnings = []
    if re.search(r'\b(?:seq|sqq)', raw, re.I):
        warnings.append('open_ended_sequence_only_initial_locator_bounded')
    if re.search(r'\bibid\b', raw, re.I):
        warnings.append('ibid_inheritance_unresolved')
    return numbers, warnings


def locate(entry, mapping):
    if not eligible(entry):
        return []
    tome = SECTIONS[entry['section_id']][0]
    numbers, warnings = reference_locators(entry)
    found = {}
    for number in numbers:
        for candidate in numeric_candidates(tome, number, mapping):
            candidate.update(method=VERSION, namespace=mapping['namespace'])
            candidate['uncertainties'] += warnings + ['font_size_replaced_by_sequence_evidence',
                                                       'candidate_not_semantically_verified']
            candidate['evidence'].append(dict(kind='cenni_index_locator', tome=tome,
                number=number, source=entry.get('source'), raw=entry.get('reference_raw'),
                span=entry.get('reference_span'), notice=mapping['notices'][tome]))
            existing = found.get(candidate['file'])
            if existing:
                existing['score'] = max(existing['score'], candidate['score'])
                existing['evidence'].extend(candidate['evidence'])
                existing['uncertainties'] = sorted(set(existing['uncertainties'] + candidate['uncertainties']))
            else:
                found[candidate['file']] = candidate
    return sorted(found.values(), key=lambda c: (-c['score'], c['file']))


def diagnose(entry, mapping):
    numbers, warnings = reference_locators(entry)
    if not eligible(entry):
        return dict(reason='outside_reviewed_cenni_sections', locator_count=0,
                    located_locator_count=0, unmatched=[])
    tome = SECTIONS[entry['section_id']][0]
    unmatched = [n for n in numbers if not numeric_candidates(tome, n, mapping)]
    # An ibid. and an open-ended seq. cannot become fully covered merely because
    # their visible starting number has a page candidate.
    unknown = int('ibid_inheritance_unresolved' in warnings) + int(
        'open_ended_sequence_only_initial_locator_bounded' in warnings)
    return dict(locator_count=len(numbers) + unknown,
                located_locator_count=len(numbers)-len(unmatched),
                unmatched=unmatched, uncertainties=warnings, tome=tome)
