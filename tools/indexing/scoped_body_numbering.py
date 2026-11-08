"""Recover OCR page candidates from explicitly scoped internal printed numbering.

The notice and anchors are local evidence, not source-file hash gates. Outputs
are experimental page links; occurrence spans only explain numeric evidence.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'scoped-body-numbering-v2'
NUMBER = re.compile(r'(?<![\w])\d+(?![\w])')
SIMPLE = re.compile(r'\s*\d+(?:\s*[,;]\s*\d+)*\s*\.?\s*')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def body_occurrences(raw, maximum, mode='body'):
    found = defaultdict(list)
    for block in blocks(raw):
        if mode == 'fascicle_header':
            if block['kind'] != 'cabecalho' and not (
                    block['kind'] == 'nota_marginal' and
                    re.fullmatch(r'\s*\[\d+\]?\s*', block['body'])):
                continue
        else:
            if block['kind'] != 'texto_principal':
                continue
            if mode == 'greek_body':
                letters = [c for c in block['body'] if c.isalpha()]
                greek = sum('\u0370' <= c <= '\u03ff' or '\u1f00' <= c <= '\u1fff' for c in letters)
                if not letters or greek / len(letters) < 0.6:
                    continue
        for match in NUMBER.finditer(block['body']):
            number = int(match[0])
            if 0 < number <= maximum:
                start = block['body_start'] + match.start()
                end = block['body_start'] + match.end()
                found[number].append({'span': [start, end],
                    'quote': raw[max(block['body_start'], start - 70):
                                 min(block['body_end'], end + 100)]})
    return dict(found)


def anchor_allows(number, physical, anchors):
    return all(physical <= a['physical'] if number < a['number'] else
               physical >= a['physical'] if number > a['number'] else
               physical == a['physical'] for a in anchors)


def load_series(profile, root):
    notice = profile['notice']
    notice_path = root / notice['source']
    notice_raw = notice_path.read_text()
    if notice_raw.count(notice['quote']) != 1:
        raise ValueError(f"Notice changed or ambiguous: {notice_path}")
    pages, nodes = {}, defaultdict(set)
    start, end = profile['body_pages']
    for path in discover_preferred_pages(root / 'teste' / profile['volume'] / 'text',
                                         volume_id=profile['volume']):
        physical = page_number(path)
        if not start <= physical <= end:
            continue
        if physical in pages:
            raise ValueError(f'Duplicate physical page: {profile["volume"]}:{physical}')
        data = path.read_bytes()
        raw = data.decode('utf-8')
        occurrences = body_occurrences(raw, profile['maximum_number'], profile.get('mode', 'body'))
        pages[physical] = {'file': str(path.resolve()), 'sha256': checksum(data),
                           'occurrences': occurrences, 'raw': raw}
        for number in occurrences:
            if anchor_allows(number, physical, profile['anchors']):
                nodes[number].add(physical)
    for anchor in profile['anchors']:
        page = pages.get(anchor['physical'])
        if not page or Path(page['file']) != (root / anchor['source']).resolve():
            raise ValueError('Anchor source absent or replaced; review local evidence')
        quote = anchor['quote']
        offset = page['raw'].find(quote)
        matches = page['occurrences'].get(anchor['number'], [])
        if page['raw'].count(quote) != 1 or not any(
                offset <= m['span'][0] < offset + len(quote) for m in matches):
            raise ValueError('Anchor quote or body number changed; review local evidence')
    supported = supported_sequence_nodes(nodes, **profile['sequence'])
    for anchor in profile['anchors']:
        supported[anchor['number'], anchor['physical']] = {'basis': 'profile_anchor',
            'review': anchor.get('review', 'visual_anchor')}
    inventory = [{'file': p['file'], 'sha256': p['sha256'], 'physical': n}
                 for n, p in sorted(pages.items())]
    return pages, supported, {'notice_sha256': checksum(notice_raw.encode()),
        'sources': inventory, 'missing_physical_pages': sorted(set(range(start, end + 1)) - pages.keys()),
        'supported_number_page_pairs': len(supported),
        'supported_markers': [dict(number=n, physical=p, file=pages[p]['file'],
            occurrences=pages[p]['occurrences'][n], support=s)
            for (n,p),s in sorted(supported.items())]}


def reference_numbers(item, metadata, raw, notation=None):
    span = metadata.get('reference_span')
    literal = metadata.get('reference_raw')
    if not isinstance(span, list) or len(span) != 2 or not isinstance(literal, str):
        return [], 'no_reference_span'
    start, end = span
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(raw)):
        return [], 'invalid_reference_span'
    if raw[start:end] != literal:
        return [], 'reference_span_changed'
    owner_span = metadata.get('owner_span')
    owner = metadata.get('owner_raw')
    if (not isinstance(owner_span, list) or len(owner_span) != 2 or not owner
            or not all(isinstance(x, int) for x in owner_span)
            or not 0 <= owner_span[0] < owner_span[1] <= len(raw)
            or raw[owner_span[0]:owner_span[1]] != owner):
        return [], 'owner_span_changed_or_missing'
    if notation:
        from tools.indexing.po_locator_candidates import parse_po_locators
        if notation == 'page_list':
            parsed = page_list(literal, start)
        else:
            parsed = parse_po_locators(literal, notation, base=start)
        metadata['_parsed_locators'] = parsed
        return list(dict.fromkeys(x['page'] for x in parsed['locators'])), None
    if not SIMPLE.fullmatch(literal):
        return [], 'notation_requires_other_grammar'
    return list(dict.fromkeys(map(int, re.findall(r'\d+', literal)))), None


def page_list(raw, base=0):
    """Explicit page-list namespace; never apply this to page/line notation."""
    parsed = {'notation': 'page_list', 'locators': [], 'residuals': []}
    pattern = re.compile(r'([0-9]+)(?:\s*(?:[-–]|à)\s*([0-9]+))?')
    pos = 0
    while pos < len(raw):
        if raw[pos].isspace() or raw[pos] in ',;.':
            pos += 1
            continue
        match = pattern.match(raw, pos)
        if not match:
            parsed['residuals'].append({'raw': raw[pos:], 'char_start': base+pos,
                'char_end': base+len(raw), 'reason': 'unparsed_page_list_tail'})
            break
        first, last = int(match[1]), int(match[2] or match[1])
        if 0 < first <= last <= first + 30:
            for number in range(first, last+1):
                parsed['locators'].append({'page': number, 'raw': match[0],
                    'char_start': base+pos, 'char_end': base+match.end(),
                    'basis': 'printed_range' if match[2] else 'printed_number'})
        else:
            parsed['residuals'].append({'raw': match[0], 'char_start': base+pos,
                'char_end': base+match.end(), 'reason': 'invalid_or_large_range'})
        pos = match.end()
    return parsed


def locate(numbers, pages, supported, profile):
    candidates, missing = {}, {}
    for number in numbers:
        if 'maximum_number' in profile and not 0 < number <= profile['maximum_number']:
            missing[str(number)] = 'number_outside_scoped_series'
            continue
        physicals = sorted(p for n, p in supported if n == number)
        bounded = None
        if not physicals and profile.get('gap_recovery'):
            bounded = bounded_gap(number, supported, profile)
            if bounded:
                physicals = bounded['pages']
        if not physicals:
            missing[str(number)] = 'no_supported_body_marker'
            continue
        if not bounded and len(physicals) > profile['max_marker_files']:
            missing[str(number)] = 'too_many_numeric_alternatives'
            continue
        for physical in physicals:
            # Include pages until the next printed marker, when tightly bounded.
            # This is file-level recall, not a claimed paragraph position.
            following = sorted(p for n, p in supported if n == number + 1 and p >= physical)
            last = physical
            if (not bounded and profile.get('mode') != 'fascicle_header' and
                    len(following) == 1 and following[0] - physical < profile['max_window_pages']):
                last = following[0]
            first = max(profile['body_pages'][0], physical - profile.get('previous_page_context', 0))
            for seq in range(first, last + 1):
                if seq not in pages:
                    continue
                page = pages[seq]
                evidence = {'number': number, 'marker_file': pages[physical]['file'],
                    'marker_sha256': pages[physical]['sha256'],
                    'marker_occurrences': pages[physical]['occurrences'].get(number, []) if not bounded else [],
                    'sequence': supported.get((number, physical)), 'alternative_marker_files': len(physicals),
                    'role': 'marker_page' if seq == physical else 'bounded_page_context'}
                if bounded:
                    evidence.update(role='bounded_missing_marker', bounds=bounded)
                    evidence['marker_file'] = None
                    evidence['marker_sha256'] = None
                    evidence['bounding_markers'] = [dict(number=n, physical=p,
                        file=pages[p]['file'], sha256=pages[p]['sha256'],
                        occurrences=pages[p]['occurrences'][n])
                        for n, p in (bounded['lower'], bounded['upper'])]
                candidate = candidates.setdefault(page['file'], {'file': page['file'],
                    'physical': seq, 'source_sha256': page['sha256'], 'score': 0,
                    'navigable': True, 'certified': False, 'tier': 'scoped_fascicle_page' if
                    profile.get('mode') == 'fascicle_header' else 'scoped_internal_number',
                    'localization_level': 'ocr_page_file', 'passage_verified': False,
                    'evidence': [], 'uncertainties': ['numeric_candidate_not_semantic_confirmation']})
                candidate['score'] = max(candidate['score'], 0.52 if bounded else 0.72 if seq == physical else 0.52)
                candidate['evidence'].append(evidence)
                if len(physicals) > 1 and 'multiple_numeric_markers' not in candidate['uncertainties']:
                    candidate['uncertainties'].append('multiple_numeric_markers')
    ordered = sorted(candidates.values(), key=lambda c: (-c['score'], c['physical'], c['file']))
    for rank, candidate in enumerate(ordered, 1):
        candidate['rank'] = rank
        candidate['unlocated_reference_numbers'] = missing
    return ordered, missing


def bounded_gap(number, supported, profile):
    """Two-sided bounds only: interpolate page sequences, bracket internal markers."""
    lower = [n for n, p in supported if n < number]
    upper = [n for n, p in supported if n > number]
    if not lower or not upper:
        return None
    low, high = max(lower), min(upper)
    left = sorted(p for n, p in supported if n == low)
    right = sorted(p for n, p in supported if n == high)
    if len(left) != 1 or len(right) != 1:
        return None
    a, b = left[0], right[0]
    if not 0 <= b - a < profile['max_window_pages']:
        return None
    if profile.get('mode') == 'fascicle_header':
        if b - a != high - low:
            return None
        candidates = [a + number - low]
    else:
        candidates = list(range(a, b + 1))
    return {'pages': candidates, 'lower': [low, a], 'upper': [high, b],
            'lower_sequence': supported[low, a], 'upper_sequence': supported[high, b]}


def run(catalog, plan, out, root):
    config = json.loads(plan.read_text())
    out.mkdir(parents=True, exist_ok=False)
    summary = {'version': VERSION, 'catalog': str(catalog.resolve()),
        'plan': str(plan.resolve()), 'plan_sha256': checksum(plan.read_bytes()),
        'implementation_sha256': checksum(Path(__file__).read_bytes()),
        'canonical_updates': 0, 'inference_calls': 0, 'profiles': [],
        'quality': 'Page candidates, not certified passages or a precision measurement.'}
    summary['dependencies_sha256'] = {
        name: checksum((Path(__file__).resolve().parents[2] / name).read_bytes())
        for name in ('scripts/audit_alphabetical_numbering.py',
                     'tools/indexing/editorial_ocr_blocks.py', 'tools/corpus_utils.py',
                     'tools/indexing/po_locator_candidates.py', 'tools/indexing/entry_parser_po.py')}
    (out / 'plan.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    with closing(readonly(catalog)) as src, closing(sqlite3.connect(out / 'relations.sqlite')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,
            score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        with (out / 'results.jsonl').open('w') as output:
            for profile in config['profiles']:
                pages, supported, inventory = load_series(profile, root)
                (out / (profile['id'] + '_sources.json')).write_text(encoded(inventory) + '\n')
                counters, reasons, source_cache = Counter(), Counter(), {}
                for row in src.execute('SELECT * FROM items WHERE volume=? ORDER BY id', (profile['volume'],)):
                    item = dict(row)
                    meta = json.loads(item['metadata_json'])
                    selected = meta.get('section_id') in profile.get('section_ids', [])
                    if 'index_pages' in profile:
                        selected = (profile['index_pages'][0] <= meta.get('physical', -1) <= profile['index_pages'][1]
                            and meta.get('reference_namespace') in profile['reference_namespaces'])
                    if not selected:
                        continue
                    counters['selected_items'] += 1
                    if item['state'] == 'source_only':
                        counters['previously_unlinked'] += 1
                    source = Path(item['source'])
                    if source not in source_cache:
                        source_cache[source] = source.read_text() if source.is_file() else None
                    raw = source_cache[source]
                    notation = profile.get('notation')
                    for override in profile.get('notation_by_index_pages', []):
                        if override['pages'][0] <= meta.get('physical', -1) <= override['pages'][1]:
                            notation = override['notation']
                    numbers, reason = reference_numbers(item, meta, raw, notation) if raw is not None else ([], 'source_missing')
                    if reason:
                        candidates, missing = [], {}
                    else:
                        candidates, missing = locate(numbers, pages, supported, profile)
                    status = reason or ('all_numbers_with_candidates' if candidates and not missing else
                                        'some_numbers_with_candidates' if candidates else 'no_candidates')
                    residuals = meta.get('_parsed_locators', {}).get('residuals', [])
                    notation_warnings = [warning for locator in meta.get('_parsed_locators', {}).get('locators', [])
                                         for warning in locator.get('warnings', [])]
                    if residuals and candidates:
                        status = 'candidates_with_unparsed_reference_material'
                    elif notation_warnings and candidates:
                        status = 'candidates_with_notation_uncertainty'
                    counters['items_with_reference_residuals'] += bool(residuals)
                    counters['items_with_notation_warnings'] += bool(notation_warnings)
                    reasons[status] += 1
                    for cause in missing.values():
                        counters[cause] += 1
                    counters['candidate_links'] += len(candidates)
                    counters['reference_numbers'] += len(numbers)
                    counters['reference_numbers_with_candidates'] += len(numbers) - len(missing) if not reason else 0
                    counters['items_with_candidates'] += bool(candidates)
                    counters['newly_linkable_items'] += bool(candidates) and item['state'] == 'source_only'
                    evidence = {'item_id': item['id'], 'profile': profile['id'], 'label': item['label'],
                        'reference_raw': meta.get('reference_raw'), 'source': str(source),
                        'owner_raw': meta.get('owner_raw'), 'owner_span': meta.get('owner_span'),
                        'source_sha256': checksum(raw.encode()) if raw is not None else None,
                        'reference_span': meta.get('reference_span'), 'status': status,
                        'numbers': numbers, 'unlocated_numbers': missing,
                        'parsed_locators': meta.get('_parsed_locators'), 'candidates': candidates}
                    for c in candidates:
                        c['profile'] = profile['id']
                        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                                   (item['id'], c['file'], c['rank'], c['score'], 1, encoded(c)))
                    db.execute('INSERT INTO relations VALUES(?,?,?,?,?)',
                               (item['id'], item['group_id'], item['volume'], status, encoded(evidence)))
                    output.write(encoded(evidence) + '\n')
                counts = dict(profile=profile['id'], **counters, statuses=dict(reasons))
                summary['profiles'].append(counts)
                print(encoded(counts), flush=True)
        summary['logical_sha256'] = checksum('\n'.join(r[0] for r in db.execute(
            'SELECT evidence_json FROM relations ORDER BY item_id')).encode())
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    with (out / 'results.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['item_id','profile','label','reference_raw','status','candidate_files','unlocated_numbers'])
        for line in (out / 'results.jsonl').open():
            row = json.loads(line)
            writer.writerow([row[k] for k in ('item_id','profile','label','reference_raw','status')] +
                [encoded([c['file'] for c in row['candidates']]),encoded(row['unlocated_numbers'])])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=Path('docs/fixtures/scoped_body_numbering.json'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.catalog, args.plan, args.out, args.root.resolve())
