"""Reconcile versioned OCR proposals into a supplemental, navigable search catalog."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_po015_names_lexical import normalize
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.historical_source_candidates import normal, prepare
from tools.indexing.ocr_variant_audit import tokens

VERSION = 'historical-source-reconciliation-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def reference_key(text):
    # Preserve list/range separators and digits; typography is not a correction.
    value = normalize(text).translate(str.maketrans({'–': '-', '—': '-'}))
    value = re.sub(r'\s+', ' ', value).strip().rstrip('.')
    return re.sub(r'\s*([,;.-])\s*', r'\1', value)


def verified_span(raw, proof):
    a, b = proof['span']
    if not 0 <= a < b <= len(raw) or raw[a:b] != proof['quote'] or digest(raw[a:b].encode()) != proof['sha256']:
        raise ValueError('Invalid snapshot span; regenerate recovery')


def project_reference(current, alternative, reference_span):
    """Only unique equal token runs can project a reference into current text."""
    left, right = tokens(prepare(current)[0]), tokens(prepare(alternative)[0])
    a, b = reference_span
    selected = [i for i,t in enumerate(right) if a <= t['span'][0] < t['span'][1] <= b]
    if not selected:
        return None
    lvals, rvals = [t['token'] for t in left], [t['token'] for t in right]
    for match in SequenceMatcher(None, lvals, rvals, autojunk=False).get_matching_blocks():
        if match.size < 4 or not match.b <= selected[0] <= selected[-1] < match.b+match.size:
            continue
        needle = rvals[match.b:match.b+match.size]
        def occurrences(values):
            return sum(values[i:i+len(needle)] == needle for i in range(len(values)-len(needle)+1))
        if occurrences(lvals) != 1 or occurrences(rvals) != 1:
            continue
        start, end = match.a+selected[0]-match.b, match.a+selected[-1]-match.b
        return [left[start]['span'][0], left[end]['span'][1]]
    return None


def compare_catalog(label, reference, rows, current, projected=()):
    """Same physical source only. Fuzzy similarity never transfers destinations."""
    label_norm, ref_norm = normal(label), reference_key(reference)
    matches = []
    for row in rows:
        meta = json.loads(row['metadata_json'])
        interval = meta.get('group_source_span') or meta.get('source_span')
        same_snapshot = meta.get('source_sha256') == digest(current.encode())
        stored_ref = row['ref_raw']
        span_valid = False
        if interval and same_snapshot:
            a, b = interval
            if 0 <= a < b <= len(current):
                stored_ref = current[a:b]
                span_valid = True
        candidate_label = normal(row['label'])
        ratio = SequenceMatcher(None, label_norm, candidate_label, autojunk=False).ratio()
        if label_norm == candidate_label:
            kind = 'agreement' if ref_norm == reference_key(stored_ref) else 'reference_variant'
        elif len(label_norm) >= 8 and len(candidate_label) >= 8 and ratio >= .82:
            kind = 'possible_label_variant'
        else:
            continue
        aligned = span_valid and any(max(interval[0], p[0]) < min(interval[1], p[1]) for p in projected)
        if kind == 'agreement' and not aligned:
            kind = 'possible_occurrence_match'
        matches.append({'item_id': row['id'], 'group_id': row['group_id'], 'kind': kind,
                        'label': row['label'], 'reference': stored_ref, 'label_similarity': ratio,
                        'source_span': interval, 'span_valid_in_snapshot': span_valid,
                        'aligned_to_current_reference': bool(aligned),
                        'provenance_basis': 'same_physical_source_and_literal_comparison'})
    return sorted(matches, key=lambda m: (m['kind'] != 'agreement', -m['label_similarity'], m['item_id']))


def reviewed_match(proposal, reviews, reading):
    """Only recorded spans bind a variant to an earlier reviewed occurrence."""
    a, b = proposal['reference']['span']
    matches = []
    for review in reviews:
        for old in review['alternative_readings']:
            if (old['version_id'] == proposal['version_id'] and
                    old['snapshot_sha256'] == reading['computed_sha256'] and
                    old['char_start'] <= a < b <= old['char_end']):
                matches.append(review['occurrence_id'])
                break
    return sorted(set(matches))


def grouping_key(proposal, reading, multiplicity):
    label = normal(proposal['owner']['quote'])
    reference = reference_key(proposal['reference']['quote'])
    a, b = proposal['reference']['span']
    regions = [c['current_span'] for c in reading.get('alignment', {}).get('changes', [])
               if c['bounded_by_text'] and c['alternative_span'][0] <= a < b <= c['alternative_span'][1]+2]
    signature = (proposal['page_key'], label, reference)
    if regions and multiplicity[(proposal['version_id'], label, reference)] == 1:
        return ('aligned_literal', *signature, tuple(regions[0]))
    # Exact snapshot coordinates can also establish identity across duplicate OCR runs.
    return ('snapshot_literal', *signature, reading['computed_sha256'], tuple(proposal['reference']['span']))


def merge_aligned_groups(groups):
    """Merge equal readings only where source windows overlap; keep all variants."""
    result = {}
    for key, group in sorted(groups.items(), key=lambda pair: (not bool(pair[1]['prior_review']), repr(pair[0]))):
        windows = [p['alignment_change']['current_span'] for p in group['proposals']
                   if p.get('alignment_change') and p['alignment_change']['bounded_by_text']]
        window = [max(w[0] for w in windows), min(w[1] for w in windows)] if windows else None
        versions = {p['version_id'] for p in group['proposals']}
        match = None
        if window and window[0] <= window[1]:
            for old in result.values():
                bound = old.get('aligned_occurrence_window')
                if (not bound or (old['prior_review'] and group['prior_review']) or
                    old['source'] != group['source'] or normal(old['label']) != normal(group['label']) or
                    reference_key(old['reference']) != reference_key(group['reference']) or
                    versions & {p['version_id'] for p in old['proposals']}):
                    continue
                if max(bound[0], window[0]) <= min(bound[1], window[1]):
                    match = old
                    break
        if match is None:
            group['aligned_occurrence_window'] = window if window and window[0] <= window[1] else None
            result[key] = group
        else:
            bound = match['aligned_occurrence_window']
            match['aligned_occurrence_window'] = [max(bound[0],window[0]),min(bound[1],window[1])]
            match['proposals'].extend(group['proposals'])
            match.setdefault('merged_group_keys', []).append(key)
            match['grouping_warning'] = 'same_literal_and_overlapping_alignment_candidate_not_certification'
    return result


def run(recovery, catalog, reviewed, survey, out):
    recovery, catalog, survey, out = map(Path, (recovery, catalog, survey, out))
    pages = {p['page_key']: p for p in load_lines(recovery/'pages.jsonl')}
    readings = {r['id']: r for r in load_lines(recovery/'readings.jsonl')}
    proposals = load_lines(recovery/'proposals.jsonl')
    reviews = load_lines(reviewed) if reviewed else []
    by_source = defaultdict(list)
    for review in reviews:
        if review['source'] not in {p['source'] for p in pages.values()}:
            continue
        literal = review['visual_transcription']['literal']
        for ref in review['reference_groups']:
            if literal[ref['char_start']:ref['char_end']] != ref['raw']:
                raise ValueError('Invalid prior visual transcription span')
        for old in review['alternative_readings']:
            raw = Path(old['snapshot']).read_bytes().decode('utf-8')
            if digest(raw.encode()) != old['snapshot_sha256'] or raw[old['char_start']:old['char_end']] != old['literal']:
                raise ValueError('Prior reviewed snapshot changed; regenerate that evidence')
        by_source[review['source']].append(review)
    snapshots = {r['id']: Path(r['snapshot']).read_bytes().decode('utf-8') for r in readings.values()}
    for ident, raw in snapshots.items():
        if digest(raw.encode()) != readings[ident]['computed_sha256']:
            raise ValueError('Recovered snapshot changed; regenerate recovery')
    current = {key: Path(p['current_snapshot']).read_bytes().decode('utf-8') for key, p in pages.items()}
    for key, raw in current.items():
        if digest(raw.encode()) != pages[key]['source_sha256']:
            raise ValueError('Current recovery snapshot changed')
    entries = [p for p in proposals if p['kind'] == 'entry_fragment_candidate']
    multiplicity = Counter((p['version_id'], normal(p['owner']['quote']), reference_key(p['reference']['quote'])) for p in entries)
    groups, duplicate_groups = {}, {}
    review_by_id = {r['occurrence_id']: r for rs in by_source.values() for r in rs}
    for review in review_by_id.values():
        if len(review['reference_groups']) != 1:
            raise ValueError('Prior multi-reference review needs explicit occurrence decomposition')
        ref = review['reference_groups'][0]
        groups[('review', review['occurrence_id'])] = {
            'source': review['source'], 'volume': review['volume'], 'physical': review['physical'],
            'label': ref['owner_text_raw'], 'reference': ref['raw'], 'proposals': [],
            'prior_review': review, 'grouping_basis': 'previous_reviewed_occurrence',
            'quality': review['quality'], 'coordinate_space': 'visual_transcription'}
    for p in entries:
        page = pages[p['page_key']]
        for key in ('entry', 'owner', 'reference'):
            verified_span(snapshots[p['version_id']], p[key])
        p['projected_current_reference_span'] = project_reference(
            current[p['page_key']], snapshots[p['version_id']], p['reference']['span'])
        matches = reviewed_match(p, by_source[page['source']], readings[p['version_id']])
        key = ('review', matches[0]) if len(matches) == 1 else grouping_key(p, readings[p['version_id']], multiplicity)
        if key not in groups:
            groups[key] = {'source': page['source'], 'volume': p['page_key'].split(':')[0],
                'physical': int(p['page_key'].split(':')[1]), 'label': p['owner']['quote'],
                'reference': p['reference']['quote'], 'proposals': [], 'prior_review': None,
                'grouping_basis': key[0], 'quality': p['quality'], 'coordinate_space': 'historical_snapshot'}
        groups[key]['proposals'].append(p)
    groups = merge_aligned_groups(groups)
    for p in proposals:
        if p['kind'] != 'duplication_candidate':
            continue
        for proof in p['current_occurrences']:
            verified_span(current[p['page_key']], proof)
        for proof in p['alternative_occurrences']:
            verified_span(snapshots[p['version_id']], proof)
        key = (p['page_key'], tuple(normal(a) for a in p['anchors']),
               tuple(tuple(h['span']) for h in p['current_occurrences']))
        duplicate_groups.setdefault(key, []).append(p)

    out.mkdir(parents=True, exist_ok=False)
    counts, decisions, annotations = Counter(), [], defaultdict(list)
    with closing(readonly(catalog)) as base, sqlite3.connect(out/'search.sqlite') as dest:
        base.execute('BEGIN')
        scoped_rows = {key: [dict(r) for r in base.execute('SELECT * FROM items WHERE volume=? AND source=?',
                        (key.split(':')[0], page['source']))] for key, page in pages.items()}
        dest.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,
                ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,
                navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            CREATE TABLE reconciliation(id TEXT PRIMARY KEY,status TEXT,evidence_json TEXT);
            CREATE TABLE item_annotations(item_id TEXT,evidence_json TEXT);
            CREATE TABLE readings(version_id INTEGER PRIMARY KEY,evidence_json TEXT);''')
        for reading in readings.values():
            dest.execute('INSERT INTO readings VALUES(?,?)', (reading['id'], encoded(reading)))
        for key, members in duplicate_groups.items():
            page_key = key[0]
            matched = [r['id'] for r in scoped_rows[page_key] if normal(r['label']) in key[1]]
            evidence = {'kind': 'possible_duplicate_transcription', 'page_key': page_key,
                        'catalog_item_ids': matched, 'support': members, 'automatically_removed': False}
            for ident in matched:
                annotations[ident].append(evidence)
            counts['duplication_groups'] += 1
            counts['catalog_items_with_duplication_warning'] += len(matched)
        maps = {}
        for volume in sorted({p['page_key'].split(':')[0] for p in pages.values()}):
            path = survey/'volumes'/volume/'profiles.json'
            maps[volume] = {'profiles_path': str(path.resolve()), 'available': path.exists(),
                            'profiles': json.loads(path.read_text()) if path.exists() else []}
        for key, group in sorted(groups.items(), key=lambda pair: repr(pair[0])):
            page_key = f"{group['volume']}:{group['physical']}"
            projections = [p['projected_current_reference_span'] for p in group['proposals']
                           if p['projected_current_reference_span']]
            matches = compare_catalog(group['label'], group['reference'], scoped_rows[page_key], current[page_key], projections)
            exact = [m for m in matches if m['kind'] == 'agreement']
            status = ('catalog_agreement' if len({m['group_id'] for m in exact}) == 1 else
                      'ambiguous_catalog_occurrence' if exact else
                      'catalog_reference_variant' if any(m['kind'] == 'reference_variant' for m in matches) else
                      'possible_catalog_correspondence' if matches else 'absent_from_catalog')
            ident = 'historical:' + digest(encoded(key).encode())[:24]
            decision = {'id': ident, 'status': status, 'catalog_matches': matches, **group}
            decision['map_lookup'] = {'status': 'no_scoped_map_available' if not maps[group['volume']]['profiles']
                                      else 'scoped_profile_requires_explicit_route',
                                      'inventory': maps[group['volume']],
                                      'generic_header_not_substituted_for_editorial_namespace': True}
            decision['source_changed_since_recovery'] = (not Path(group['source']).is_file() or
                digest(Path(group['source']).read_bytes()) != pages[page_key]['source_sha256'])
            counts[status] += 1
            counts['previous_review_occurrences'] += group['prior_review'] is not None
            counts['groups'] += 1
            if status == 'catalog_agreement':
                # Existing entries and every existing destination remain in the base.
                for match in exact:
                    annotations[match['item_id']].append({'kind': 'historical_reading_support', 'reconciliation': decision})
                decision['export'] = 'annotate_existing_not_duplicate'
            else:
                meta = {'status': status, 'canonical_write_allowed': False, 'certified': False,
                        'source_span': None, 'source_sha256': pages[page_key]['source_sha256'],
                        'reference_coordinate_space': group['coordinate_space'], 'reconciliation': decision,
                        'candidate_count': 0, 'catalog': str(catalog.resolve()),
                        'quality': group['quality'], 'source_link_is_index_not_destination': True}
                cursor = dest.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                    (ident, ident, group['volume'], group['label'], group['reference'],
                     group['source'], 'source_only', encoded(meta)))
                aliases = ' '.join(p['owner']['quote'] for p in group['proposals'])
                dest.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',
                    (cursor.lastrowid, normalize(group['label']+' '+aliases), normalize(group['reference']), group['volume']))
                decision['export'] = 'supplemental_source_only'
                counts['supplemental_items'] += 1
            dest.execute('INSERT INTO reconciliation VALUES(?,?,?)', (ident, status, encoded(decision)))
            decisions.append(decision)
        for ident, evidence in sorted(annotations.items()):
            for record in evidence:
                dest.execute('INSERT INTO item_annotations VALUES(?,?)', (ident, encoded(record)))
        summary = {'version': VERSION, 'catalog': str(catalog.resolve()), 'recovery': str(recovery.resolve()),
                   'input_proposals': len(proposals), 'input_fragments': len(entries), 'pages': len(pages),
                   'baseline_items_on_pages': sum(map(len, scoped_rows.values())), 'counts': dict(counts),
                   'new_destination_links': 0, 'canonical_updates': 0, 'ocr_updates': 0,
                   'inference_calls': 0, 'interpretation': 'Groups retain uncertain variants; not a count of unique printed entries.',
                   'map_inventories': maps}
        inputs = [recovery/name for name in ('pages.jsonl','readings.jsonl','proposals.jsonl')]
        if reviewed:
            inputs.append(Path(reviewed))
        inputs += [Path(v['profiles_path']) for v in maps.values() if v['available']]
        manifest = {'version': VERSION, 'inputs': {str(p.resolve()): digest(p.read_bytes()) for p in inputs},
                    'code_sha256': digest(Path(__file__).read_bytes()), 'catalog': str(catalog.resolve()),
                    'baseline_scoped_rows_sha256': digest(encoded(scoped_rows).encode())}
        for name, value in [('summary', summary), ('manifest', manifest)]:
            dest.execute('INSERT INTO metadata VALUES(?,?)', (name, encoded(value)))
            (out/f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        dest.commit()
        assert dest.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    (out/'reconciliation.jsonl').write_text(''.join(encoded(d)+'\n' for d in decisions))
    return summary


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--recovery', type=Path, required=True)
    cli.add_argument('--catalog', type=Path, default=Path('data/index_catalog/search_candidates_002/search.sqlite'))
    cli.add_argument('--reviewed', type=Path, help='Earlier reviewed recovery documents.jsonl')
    cli.add_argument('--survey', type=Path, default=Path('data/index_entry_parser/global_targets_20261106_004'))
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(run(args.recovery,args.catalog,args.reviewed,args.survey,args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
