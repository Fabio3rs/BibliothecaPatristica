"""Physical candidates for Gudian folios, from literal markers in the Ambigua.

The folio-side ordinal is only used for bounded intervals. It is never an
arithmetic offset into physical filenames; repeated scans and corrupt numerals
remain visible as alternatives. No database or source text is modified.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'gudian-folio-targets-v1'
OPENING = 'MAXIMI DE VARIIS DIFFICILIBUS LOCIS'
CLOSING = 'S. MAXIMI CONFESSORIS HYMNI.'
NOTICE = 'Numeri respondent foliis libri Gudiani typis grandioribus expressis.'
MARKER = re.compile(r'[\[(]\s*(?P<number>\d{1,4})\s*(?P<side>[abαβ])(?:\s*[.ᵇ*]+)?\s*[\])]')
MAX_PAGES = 4
MAX_SIDES = 8


def ordinal(number, side):
    return int(number) * 2 + (side == 'b')


def build_map(root: Path) -> dict:
    files = sorted((root / 'teste/PG091/text').glob('*.txt'),
                   key=lambda p: (int(p.stem.rsplit('-', 1)[-1]), p.name))
    loaded = [(p, p.read_text()) for p in files]
    starts = [i for i, (_, text) in enumerate(loaded) if OPENING in text]
    ends = [i for i, (_, text) in enumerate(loaded) if CLOSING in text]
    notices = [(p, text.index(NOTICE)) for p, text in loaded if NOTICE in text]
    if not starts or len(ends) != 1 or ends[0] <= max(starts) or len(notices) != 1:
        raise ValueError('Gudian work boundaries and index notice must be identified')
    mapping = dict(version=VERSION, volume='PG091', namespace='gudian_folio_side',
                   pages=[], anchors=[], boundaries=[], max_pages=MAX_PAGES,
                   max_sides=MAX_SIDES, notice=dict(file=str(notices[0][0].resolve()),
                   raw=NOTICE, span=[notices[0][1], notices[0][1] + len(NOTICE)]))
    # Multiple literal openings delimit independent scan runs, not unique works.
    # This prevents an interval crossing a restart in the physical sequence.
    for run, start in enumerate(starts):
        stop = starts[run + 1] if run + 1 < len(starts) else ends[0] + 1
        p, text = loaded[start]
        mapping['boundaries'].append(dict(file=str(p.resolve()), raw=OPENING,
            span=[text.index(OPENING), text.index(OPENING) + len(OPENING)], run=run))
        for p, text in loaded[start:stop]:
            physical = int(p.stem.rsplit('-', 1)[-1])
            cutoff = text.index(CLOSING) if CLOSING in text else len(text)
            lower = text.index(OPENING) if OPENING in text else 0
            sha = hashlib.sha256(text.encode()).hexdigest()
            mapping['pages'].append(dict(file=str(p.resolve()), physical=physical,
                sha256=sha, run=run, body_bounds=[lower, cutoff]))
            for block in blocks(text):
                if block['kind'] != 'texto_principal':
                    continue
                a, b = max(lower, block['body_start']), min(cutoff, block['body_end'])
                for match in MARKER.finditer(text, a, b):
                    number, side = int(match['number']), {'α': 'a', 'β': 'b'}.get(match['side'], match['side'])
                    mapping['anchors'].append(dict(file=str(p.resolve()), physical=physical,
                        run=run, number=number, side=side, ordinal=ordinal(number, side),
                        side_raw=match['side'], side_normalized=match['side'] != side,
                        raw=match.group(), span=list(match.span()), sha256=sha,
                        snippet=text[max(a, match.start()-60):min(b, match.end()+120)]))
    p, text = loaded[ends[0]]
    mapping['boundaries'].append(dict(file=str(p.resolve()), raw=CLOSING,
        span=[text.index(CLOSING), text.index(CLOSING) + len(CLOSING)]))
    anchors = mapping['anchors']
    for anchor in anchors:
        compatible = [other for other in anchors if other['run'] == anchor['run']
            and 0 < abs(other['ordinal'] - anchor['ordinal']) <= MAX_SIDES
            and 0 <= abs(other['physical'] - anchor['physical']) <= MAX_PAGES
            and ((other['physical'], other['span'][0]) > (anchor['physical'], anchor['span'][0]))
                == (other['ordinal'] > anchor['ordinal'])]
        anchor['sequence_supported'] = bool(compatible)
    mapping['numbers'] = {}
    for anchor in anchors:
        mapping['numbers'].setdefault(f"{anchor['number']}{anchor['side']}", []).append(anchor)
    return mapping


def _candidates(number, side, mapping):
    target = ordinal(number, side)
    anchors = mapping['anchors']
    pages = mapping['pages']
    result = []
    direct = mapping['numbers'].get(f'{number}{side}', [])
    for anchor in direct:
        supported = anchor['sequence_supported']
        result.append(dict(file=anchor['file'], physical=anchor['physical'],
            score=.92 if supported else .35,
            evidence=[dict(kind='literal_gudian_folio_marker', **anchor)],
            uncertainties=[] if supported else ['numeral_outside_local_sequence']))
        if not supported:
            continue
        successors = [a for a in anchors if a['sequence_supported']
            and a['run'] == anchor['run'] and 0 < a['ordinal']-target <= MAX_SIDES
            and 0 <= a['physical']-anchor['physical'] <= MAX_PAGES
            and (a['physical'], a['span'][0]) > (anchor['physical'], anchor['span'][0])]
        if successors:
            # Nearest physical successor bounds the passage even if its numeral
            # skips missing OCR markers. Include its page: text before the new
            # marker may still belong to the requested folio.
            right = min(successors, key=lambda a: (a['physical'], a['span'][0]))
            for page in pages:
                if page['run'] == anchor['run'] and anchor['physical'] < page['physical'] <= right['physical']:
                    result.append(dict(file=page['file'], physical=page['physical'], score=.72,
                        evidence=[dict(kind='gudian_passage_before_next_marker', left=anchor, right=right)],
                        uncertainties=['passage_continuation_candidate']))
        else:
            tail = [p for p in pages if p['run'] == anchor['run'] and p['physical'] > anchor['physical']]
            if (anchor['run'] == max(p['run'] for p in pages) and tail
                    and max(p['physical'] for p in tail)-anchor['physical'] <= 2):
                for page in tail:
                    result.append(dict(file=page['file'], physical=page['physical'], score=.60,
                        evidence=[dict(kind='gudian_final_passage_to_work_end', left=anchor,
                                       boundary=mapping['boundaries'][-1])],
                        uncertainties=['final_passage_extent_candidate']))
    # A missing marker (or only an anomalous reading) still gets a small
    # interval supported by both sides; no arbitrary nearest-page extrapolation.
    if not any(a['sequence_supported'] for a in direct):
        pairs = []
        for left in anchors:
            if not left['sequence_supported'] or not 0 < target-left['ordinal'] < MAX_SIDES:
                continue
            for right in anchors:
                if not right['sequence_supported'] or right['run'] != left['run']:
                    continue
                if not target < right['ordinal'] <= left['ordinal']+MAX_SIDES:
                    continue
                if not 0 <= right['physical']-left['physical'] <= MAX_PAGES:
                    continue
                if (left['physical'], left['span'][0]) >= (right['physical'], right['span'][0]):
                    continue
                pairs.append((right['ordinal']-left['ordinal'], right['physical']-left['physical'], left, right))
        # Preserve independent repeated scans, choosing tight bounds per run.
        for run in sorted({p[2]['run'] for p in pairs}):
            group = [p for p in pairs if p[2]['run'] == run]
            best = min((p[0], p[1]) for p in group)
            for gap, distance, left, right in group:
                if (gap, distance) != best:
                    continue
                for page in pages:
                    if page['run'] == run and left['physical'] <= page['physical'] <= right['physical']:
                        result.append(dict(file=page['file'], physical=page['physical'], score=.55,
                            evidence=[dict(kind='bounded_missing_gudian_folio', requested=[number, side],
                                           left=left, right=right)],
                            uncertainties=['folio_marker_missing_or_corrupt', 'bounded_page_interval']))
    return result


def locate(entry: dict, mapping: dict) -> list[dict]:
    if entry.get('volume') != 'PG091' or entry.get('reference_namespace') != 'gudian_folio_side':
        return []
    grouped = {}
    for atom in entry.get('locator_atoms', []):
        if atom.get('side') not in {'a', 'b'} or not isinstance(atom.get('number'), int):
            continue
        for item in _candidates(atom['number'], atom['side'], mapping):
            evidence = dict(kind='gudian_index_reference', source=entry.get('source'),
                span=atom.get('span'), raw=atom.get('raw'), number=atom['number'], side=atom['side'])
            row = grouped.setdefault(item['file'], dict(file=item['file'], physical=item['physical'],
                volume='PG091', score=0, method=VERSION, namespace='gudian_folio_side',
                evidence=[], uncertainties=[]))
            row['score'] = max(row['score'], item['score'])
            for ev in item['evidence'] + [evidence]:
                if ev not in row['evidence']:
                    row['evidence'].append(ev)
            row['uncertainties'] = sorted(set(row['uncertainties'] + item['uncertainties']))
    return sorted(grouped.values(), key=lambda x: (-x['score'], x['physical'], x['file']))


def diagnose(entry: dict, mapping: dict) -> dict:
    atoms = entry.get('locator_atoms', [])
    missing = [a for a in atoms if not _candidates(a['number'], a['side'], mapping)]
    anomalous = [a for a in atoms if mapping['numbers'].get(f"{a['number']}{a['side']}")
        and not any(x['sequence_supported'] for x in mapping['numbers'][f"{a['number']}{a['side']}"])]
    return dict(locator_count=len(atoms), located_locator_count=len(atoms)-len(missing),
        unmatched_locators=missing, anomalous_literal_locators=anomalous,
        completeness='all_locators_have_candidates' if not missing else 'partial_or_unlocated',
        reasons=['no_literal_or_bounded_folio_marker'] if missing else [], version=VERSION)
