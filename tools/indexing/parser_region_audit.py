"""Reconcile existing parser candidates with regional evidence; read-only SQLite inputs."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

from tools.indexing.entry_parser_bulk import readonly


def assignment(raw, entry, regions):
    a, b = entry['char_start'], entry['char_end']
    if not 0 <= a < b <= len(raw):
        return 'invalid_entry_span', None
    if hashlib.sha256(raw[a:b].encode()).hexdigest() != entry['text_sha256']:
        return 'invalid_entry_span', None
    starts = [r['span'][0] for r in regions]
    lo, hi = max(0, bisect_right(starts, a)-1), bisect_left(starts, b)
    touched = [r for r in regions[lo:hi] if r['span'][1] > a and r.get('section_id')]
    if not touched:
        return 'outside_candidate_regions', None
    ids = {r['section_id'] for r in touched}
    if len(ids) != 1:
        return 'crosses_candidate_sections', None
    cursor = a
    for row in touched:
        start, end = max(a, row['span'][0]), min(b, row['span'][1])
        if raw[cursor:start].strip():
            return 'partially_covered', None
        cursor = max(cursor, end)
    if raw[cursor:b].strip():
        return 'partially_covered', None
    kind = touched[0]['kind']
    status = ('inside_index_candidate' if kind.endswith('_index') or kind == 'index_unspecified'
              else 'inside_general_or_other_material')
    return status, {'section_id': touched[0]['section_id'], 'kind': kind}


def orphan_page(page, entries):
    headers = [e for e in page.get('signals', []) if e['role'] == 'running_header'
               and (str(e.get('kind')).endswith('_index') or e.get('kind') == 'index_unspecified')]
    if not headers or not entries:
        return None
    return {key: page[key] for key in ['source', 'source_sha256', 'volume', 'physical']} | {
        'reason': 'index_like_header_without_supported_ownership', 'header_signals': headers,
        'candidate_count': len(entries), 'candidate_ids': [e['id'] for e in entries],
        'section_opening_known': False, 'verified': False, 'import_ready': False}


def run(args):
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new audit output directory')
    source = readonly(args.parser_db)
    regional = readonly(args.region_db)
    try:
        source.execute('BEGIN'); regional.execute('BEGIN')
        if not source.execute('SELECT 1 FROM runs WHERE run_id=?', (args.parser_run,)).fetchone():
            raise ValueError('Unknown parser run')
        region_run = regional.execute('SELECT summary_json FROM editorial_region_runs WHERE run_id=?', (args.region_run,)).fetchone()
        if not region_run or json.loads(region_run[0])['status'] != 'completed':
            raise ValueError('Select a completed regional run')
        rows = source.execute('''SELECT r.source,r.volume,r.physical,t.result_json
            FROM run_pages r JOIN tasks t USING(fingerprint)
            WHERE r.run_id=? ORDER BY r.volume,r.physical,r.source''', (args.parser_run,))
        args.out.mkdir(parents=True, exist_ok=True)
        totals, volumes, issues = Counter(), defaultdict(Counter), []
        samples, seen, orphan_pages = {}, Counter(), []
        rng = random.Random(args.seed)
        with (args.out/'assignments.jsonl').open('w') as stream:
            for row in rows:
                totals['pages'] += 1
                parsed = json.loads(row['result_json']).get('page')
                if not parsed:
                    totals['parser_pages_without_result'] += 1
                    continue
                entries = parsed['entries']
                totals['original_candidates'] += len(entries)
                result = regional.execute('SELECT result_json FROM editorial_region_pages WHERE run_id=? AND source=?',
                                          (args.region_run, row['source'])).fetchone()
                if not result:
                    totals['candidates_without_regional_source'] += len(entries)
                    issues.append({'source': row['source'], 'reason': 'missing_regional_source'})
                    continue
                page = json.loads(result[0])
                try:
                    data = Path(row['source']).read_bytes()
                    checksum = hashlib.sha256(data).hexdigest()
                    raw = data.decode('utf-8')
                except (OSError, UnicodeError) as exc:
                    totals['unreadable_candidates'] += len(entries)
                    issues.append({'source': row['source'], 'reason': str(exc)})
                    continue
                if checksum != page.get('source_sha256') or checksum != parsed['sha256']:
                    totals['stale_candidates'] += len(entries)
                    issues.append({'source': row['source'], 'reason': 'source_hash_mismatch'})
                    continue
                unassigned = []
                for entry in entries:
                    status, region = assignment(raw, entry, page['regions'])
                    if status == 'outside_candidate_regions':
                        unassigned.append(entry)
                    record = {'volume': row['volume'], 'physical': row['physical'], 'source': row['source'],
                              'source_sha256': checksum, 'candidate_id': entry['id'],
                              'span': [entry['char_start'], entry['char_end']],
                              'snippet': entry['entry_raw'][:300], 'status': status, 'region': region,
                              'verified': False, 'import_ready': False}
                    stream.write(json.dumps(record, ensure_ascii=False)+'\n')
                    totals[status] += 1; volumes[row['volume']][status] += 1
                    key = (row['volume'][:2], status)
                    seen[key] += 1
                    if rng.randrange(seen[key]) == 0:
                        samples[key] = record
                orphan = orphan_page(page, unassigned)
                if orphan:
                    orphan_pages.append(orphan)
        summary = {'parser_run': args.parser_run, 'region_run': args.region_run,
                   'totals': dict(totals), 'by_volume': dict(volumes), 'seed': args.seed,
                   'db_updates': 0, 'inference_calls': 0, 'quality_verified': False,
                   'orphan_index_pages': len(orphan_pages),
                   'orphan_index_candidates': sum(p['candidate_count'] for p in orphan_pages),
                   'meaning': 'Geometric source-span compatibility only; neither extraction nor ownership is certified.'}
        for name, value in [('summary.json', summary), ('issues.json', issues),
                            ('review_sample.json', [samples[k] for k in sorted(samples)])]:
            (args.out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        with (args.out/'orphan_index_pages.jsonl').open('w') as stream:
            for page in orphan_pages:
                stream.write(json.dumps(page, ensure_ascii=False)+'\n')
        return summary
    finally:
        source.close(); regional.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parser-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    parser.add_argument('--region-db', type=Path, default=Path('data/index_entry_parser/editorial_regions.sqlite'))
    parser.add_argument('--parser-run', required=True)
    parser.add_argument('--region-run', required=True)
    parser.add_argument('--seed', type=int, default=73)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
