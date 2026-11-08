"""Resolve explicit PL corpus tome/column atoms using a readonly header cache.

The cache only enumerates candidate files. Current OCR header text is checked
lazily for requested numbers; changed file/image hashes never block a search.
No source scan, cache refresh, inference, or database write occurs here.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'pl-corpus-author-cached-header-targets-v3'
NAMESPACE = 'pl_corpus_tome_column'
NEIGHBOR_COLUMNS = 12
MAX_EDGE_FILES = 2
# Internal punctuation may split one corrupted printed number (9:9 = damaged
# 909). Never treat either fragment as a separate literal column 9.
NUMBER = re.compile(r'(?<![\w:.,;/])\d{1,4}(?!\w|[:.,;/]\d)')


def eligible(entry):
    return (entry.get('section_id') == 'PL218:index-iii-alphabetic-authors'
            and entry.get('reference_namespace') == NAMESPACE
            and entry.get('grammar') == 'pl_corpus_author_date_tome_column')


def locator_keys(entry):
    return [(i, atom, (atom['target_volume'], int(atom['column_value'])))
            for i, atom in enumerate(entry.get('locators', []))
            if atom.get('target_volume') and atom.get('column_value') is not None and not atom.get('uncertainty')]


def build_map(root, entries=None, *, cache_path=None):
    root = Path(root).resolve()
    cache = Path(cache_path) if cache_path else root/'data/editorial_page_estimator.db'
    if not cache.is_file():
        raise FileNotFoundError(f'Editorial header cache missing: {cache}; regenerate with scripts/estimate_editorial_pages.py')
    wanted = None if entries is None else {key for entry in entries if eligible(entry) for _, _, key in locator_keys(entry)}
    neighborhood = None if wanted is None else {(v, m) for v, n in wanted
        for m in range(max(1, n-NEIGHBOR_COLUMNS), n+NEIGHBOR_COLUMNS+1)}
    index = defaultdict(list)
    nearby, pages = defaultdict(list), defaultdict(lambda: defaultdict(list))
    rows, selected = 0, 0
    # Do not reuse the estimator's _connect_db: it creates tables and enables WAL.
    with closing(sqlite3.connect(f'{cache.resolve().as_uri()}?mode=ro', uri=True)) as db:
        for volume, path, payload in db.execute("SELECT volume_id,file_path,observation_json FROM file_observation_cache WHERE collection='PL'"):
            rows += 1
            observation = json.loads(payload)
            pairs = observation.get('header_pairs', [])
            # Old cached singles/pairs can themselves contain numeric fragments.
            # Enumeration therefore also requires an intact token in cache text.
            numbers = {int(m[0]) for m in NUMBER.finditer(observation.get('header_text', ''))}
            current = Path(path)
            parts = current.parts
            if 'teste' in parts:
                current = root.joinpath(*parts[parts.index('teste'):])
            physical = observation.get('file_seq')
            if isinstance(physical, int):
                pages[volume][str(physical)].append(str(current))
            keys = [(volume, n) for n in numbers if neighborhood is None or (volume, n) in neighborhood]
            if not keys:
                continue
            selected += 1
            pair_numbers = {int(n) for pair in pairs if len(pair) == 2
                            and int(pair[0]) % 2 == 1 and int(pair[1]) == int(pair[0]) + 1
                            for n in pair}
            for _, number in keys:
                item = {'file': str(current), 'physical': physical, 'cache_header_pair': number in pair_numbers}
                nearby[f'{volume}:{number}'].append(item)
                if wanted is None or (volume, number) in wanted:
                    index[f'{volume}:{number}'].append(item)
    # Retain only physical windows that can participate in a requested lookup.
    needed = defaultdict(set)
    for key, candidates in nearby.items():
        volume = key.split(':')[0]
        for candidate in candidates:
            if isinstance(candidate['physical'], int):
                needed[volume].update(range(max(1, candidate['physical']-7), candidate['physical']+8))
    mapping = {'version': VERSION, 'namespace': NAMESPACE, 'cache': str(cache.resolve()),
            'source_table': 'file_observation_cache', 'cache_rows_examined': rows,
            'cache_rows_selected': selected, 'wanted_keys': None if wanted is None else len(wanted),
            'numbers': {key: sorted(value, key=lambda a: a['file']) for key, value in sorted(index.items())},
            'neighbor_numbers': {key: sorted(value, key=lambda a: a['file']) for key, value in sorted(nearby.items())},
            'pages': {v: {str(n): sorted(pages[v][str(n)]) for n in sorted(ns) if str(n) in pages[v]}
                      for v, ns in sorted(needed.items())},
            'parameters': {'neighbor_columns': NEIGHBOR_COLUMNS, 'max_edge_files': MAX_EDGE_FILES},
            'canonical_updates': 0, 'cache_updates': 0}
    return mapping


@lru_cache(maxsize=8192)
def _current_header(path, mtime_ns, size):
    # File metadata only invalidates in-memory reads; it never gates acceptance.
    raw = Path(path).read_text(encoding='utf-8')
    result = defaultdict(list)
    for block in blocks(raw):
        if block['kind'] != 'cabecalho':
            continue
        start, end = block['body_start'], block['body_end']
        for match in NUMBER.finditer(raw[start:end]):
            a, b = start+match.start(), start+match.end()
            result[int(match[0])].append({'source': path, 'span': [a, b], 'raw': raw[a:b],
                'header_span': [start, end], 'header_raw': raw[start:end]})
    return dict(result)


def _literal_candidates(key, mapping):
    volume, number = key
    found = []
    for cached in mapping['numbers'].get(f'{volume}:{number}', []):
        path = Path(cached['file'])
        try:
            stat = path.stat()
            current = _current_header(str(path), stat.st_mtime_ns, stat.st_size)
        except (OSError, UnicodeError):
            continue
        proof = current.get(number, [])
        if not proof:
            continue
        companion = number + 1 if number % 2 else number - 1
        pair_proof = current.get(companion, [])
        found.append({**cached, 'header_evidence': proof,
                      'current_consecutive_pair': bool(pair_proof),
                      'companion_header_evidence': pair_proof})
    return found


def _header(path):
    try:
        stat = Path(path).stat()
        return _current_header(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, UnicodeError):
        return {}


@lru_cache(maxsize=8192)
def _has_body(path, mtime_ns, size):
    return any(b['kind'] == 'texto_principal' and len(re.findall(r'[^\W\d_]', b['body'])) >= 20
               for b in blocks(Path(path).read_text()))


def _sequence_candidates(key, mapping):
    """Infer a paired column only from nearby, freshly read header evidence.

    Physical positions are never global offsets: each hypothesis needs two
    distinct neighboring pages with matching local slope (two columns/file).
    An explicit contradictory pair on the target prevents propagation through
    a numbering restart. A missing header remains uncertainty, not invented OCR.
    """
    volume, number = key
    unit = (number+1)//2
    anchors = {}
    for n in range(max(1, number-NEIGHBOR_COLUMNS), number+NEIGHBOR_COLUMNS+1):
        for cached in mapping.get('neighbor_numbers', {}).get(f'{volume}:{n}', []):
            physical = cached.get('physical')
            if not isinstance(physical, int):
                continue
            current = _header(cached['file'])
            if n not in current:
                continue
            companion = n+1 if n%2 else n-1
            aunit = (n+1)//2
            pair = bool(current.get(companion))
            proofs = current[n] + current.get(companion, [])
            anchors[(cached['file'], aunit)] = dict(file=cached['file'], physical=physical,
                unit=aunit, pair=pair, evidence=proofs)
    ordered = sorted(anchors.values(), key=lambda a: (a['physical'], a['unit'], a['file']))
    found = {}
    for i, left in enumerate(ordered):
        for right in ordered[i+1:]:
            distance = right['physical']-left['physical']
            if not 1 <= distance <= 6 or right['unit']-left['unit'] != distance:
                continue
            if not (left['pair'] or right['pair']):
                continue
            physical = left['physical']+unit-left['unit']
            inside = left['unit'] <= unit <= right['unit']
            edge = min(abs(physical-left['physical']), abs(physical-right['physical']))
            if not inside and (edge > MAX_EDGE_FILES or not (left['pair'] and right['pair'])):
                continue
            for file in mapping.get('pages', {}).get(volume, {}).get(str(physical), []):
                path = Path(file)
                try:
                    stat = path.stat()
                    if not _has_body(file, stat.st_mtime_ns, stat.st_size):
                        continue
                except (OSError, UnicodeError):
                    continue
                current = _header(file)
                strong = {(n+1)//2 for n in current if n%2 and n+1 in current}
                if strong and unit not in strong:
                    continue
                # A competing observed pair anywhere inside the short bridge
                # marks a restart/conflict; do not carry the old sequence over it.
                conflict = False
                for mid in range(min(physical, left['physical']), max(physical, right['physical'])+1):
                    expected = left['unit']+mid-left['physical']
                    for other in mapping.get('pages', {}).get(volume, {}).get(str(mid), []):
                        hdr = _header(other)
                        pairs = {(n+1)//2 for n in hdr if n%2 and n+1 in hdr}
                        if pairs and expected not in pairs:
                            conflict = True
                if conflict:
                    continue
                kind = 'paired_column_between_agreeing_headers' if inside else 'short_paired_column_extrapolation'
                score = .82 if inside else .64
                proof = dict(kind=kind, requested_column=number, target_physical=physical,
                             left=left, right=right)
                candidate = found.setdefault(file, dict(file=file, physical=physical,
                    cache_header_pair=False, current_consecutive_pair=False,
                    header_evidence=current.get(number, []), companion_header_evidence=[],
                    sequence_evidence=[], inferred_score=score))
                candidate['inferred_score'] = max(candidate['inferred_score'], score)
                candidate['sequence_evidence'].append(proof)
    # Two nearest witnesses suffice per file. Keep distinct physical alternatives.
    for candidate in found.values():
        candidate['sequence_evidence'] = sorted(candidate['sequence_evidence'], key=lambda p: (
            p['kind'] != 'paired_column_between_agreeing_headers',
            abs(p['left']['physical']-candidate['physical']) + abs(p['right']['physical']-candidate['physical']),
            p['left']['file'], p['right']['file']))[:2]
    return sorted(found.values(), key=lambda c: c['file'])


def _atom_candidates(key, mapping):
    direct = _literal_candidates(key, mapping)
    if any(c['current_consecutive_pair'] for c in direct):
        return direct
    return direct + _sequence_candidates(key, mapping)


def locate(entry, mapping):
    if not eligible(entry):
        return []
    candidates = {}
    for index, atom, key in locator_keys(entry):
        for target in _atom_candidates(key, mapping):
            file = target['file']
            candidate = candidates.setdefault(file, {'file': file, 'volume': key[0],
                'physical': target['physical'], 'score': 0.0, 'method': VERSION,
                'namespace': NAMESPACE, 'evidence': [], 'uncertainties': ['candidate_not_semantically_verified',
                    'cached_candidate_enumeration_may_miss_new_or_changed_headers']})
            inferred = bool(target.get('sequence_evidence'))
            candidate['score'] = max(candidate['score'], target.get('inferred_score', 0.90 if target['current_consecutive_pair'] else 0.76))
            if inferred:
                candidate['uncertainties'].append('requested_column_inferred_from_local_header_sequence')
            candidate['evidence'].append({'kind': 'local_pl_header_sequence' if inferred else 'current_literal_pl_header_column', 'locator_index': index,
                'target_volume': key[0], 'column': key[1], 'index_reference': atom,
                'current_header_evidence': target['header_evidence'], 'cache': mapping['cache'],
                'cache_pair_support': target['cache_header_pair'],
                'current_consecutive_pair': target['current_consecutive_pair'],
                'companion_header_evidence': target['companion_header_evidence'],
                'sequence_evidence': target.get('sequence_evidence', [])})
    if len(candidates) > 1:
        for candidate in candidates.values():
            candidate['uncertainties'].append('multiple_files_or_multiple_reference_destinations_preserved')
    return sorted(candidates.values(), key=lambda c: (-c['score'], c['file']))


def diagnose(entry, mapping):
    if not eligible(entry):
        return {'reason': 'outside_pl_corpus_author_grammar', 'locator_count': 0, 'located_locator_count': 0, 'unmatched': []}
    keys = {i: key for i, _, key in locator_keys(entry)}
    unmatched = []
    located = 0
    for i, atom in enumerate(entry.get('locators', [])):
        if i not in keys:
            reason = atom.get('uncertainty') or atom.get('relation') or 'no_explicit_tome_column'
        elif _atom_candidates(keys[i], mapping):
            located += 1
            continue
        elif f'{keys[i][0]}:{keys[i][1]}' in mapping['numbers']:
            reason = 'cached_file_missing_or_current_header_not_literal'
        else:
            reason = 'column_absent_from_cached_headers'
        unmatched.append({'locator_index': i, 'raw': atom.get('raw'), 'reason': reason})
    total = len(entry.get('locators', []))
    if entry.get('textual_cross_reference'):
        total += 1
        unmatched.append({'raw': entry['textual_cross_reference']['raw'], 'reason': 'textual_cross_reference_requires_index_join'})
    return {'locator_count': total, 'located_locator_count': located, 'unmatched': unmatched,
            'namespace': NAMESPACE, 'reason': 'header_and_local_sequence_candidates'}
