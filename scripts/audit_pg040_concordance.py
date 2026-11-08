#!/usr/bin/env python3
"""Read-only PG040 concordance investigation; emits leads, never resolutions.

Uses the documented Nemesius scope (physical 253–415), index notice on 426,
and concordance on 427. OCR table alignment is not facsimile verification.
"""
from collections import Counter, defaultdict
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_alphabetical_numbering import supported_sequence_nodes, queries_for
from scripts.resolve_alphabetical_unresolved import QUERY, connect_readonly, cited_start
from scripts.scan_editorial_ocr import block_parts, compact_match
from tools.corpus_utils import page_files, choose_page_file
from tools.indexing.index_target_locator import normalize_for_search

VERSION = 'pg040_concordance_audit_v1'
TABLE_ROW = re.compile(r'^\s*(?:P\.\s*)?(\d+)(?=[.\s]|$)(.*)$')
NUMBER = re.compile(r'(?<![\w])(\d{1,3})(?!\w)')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_page(path):
    data = path.read_bytes()
    raw = data.decode('utf-8')
    return raw, {'file': str(path.resolve()), 'sha256': digest(data)}


def extract_concordance(raw, source):
    columns = []
    for block in block_parts(raw):
        label = block['body'].strip().splitlines()[0]
        if not label.startswith(('Edit. Antv.', 'Edit. Oxon.')):
            continue
        rows = []
        offset = block['start_offset']
        for line in block['body'].splitlines(keepends=True):
            match = TABLE_ROW.match(line)
            if match:
                rows.append({'number': int(match[1]), 'literal': line.strip(),
                             'file_line': raw.count('\n', 0, offset) + 1})
            offset += len(line)
        columns.append({'edition': 'Antwerp' if 'Antv.' in label else 'Oxford', 'rows': rows})
    if len(columns) != 6 or [c['edition'] for c in columns] != ['Antwerp', 'Oxford'] * 3:
        raise ValueError('Unexpected concordance columns; review source layout')
    groups, pairs = [], []
    for group, (left, right) in enumerate(zip(columns[::2], columns[1::2]), 1):
        a, o = left['rows'], right['rows']
        consecutive = all(y['number'] == x['number'] + 1 for x, y in zip(a, a[1:]))
        aligned = len(a) == len(o) and consecutive
        groups.append({'group': group, 'status': 'ocr_aligned' if aligned else 'unaligned',
                       'antwerp_rows': a, 'oxford_rows': o,
                       'reason': None if aligned else 'unequal_column_lengths_or_nonconsecutive_antwerp'})
        if not aligned:
            continue
        for i, (antwerp, oxford) in enumerate(zip(a, o)):
            anomaly = (0 < i < len(o)-1 and
                       not o[i-1]['number'] < oxford['number'] < o[i+1]['number'])
            pairs.append({'antwerp': antwerp['number'], 'oxford_raw_number': oxford['number'],
                          'antwerp_cell': antwerp, 'oxford_cell': oxford, 'source': source,
                          'group': group, 'status': 'ocr_anomaly' if anomaly else 'ocr_aligned_unverified',
                          'anomaly_reason': 'nonmonotonic_neighbor_numbers' if anomaly else None})
    return {'source': source, 'groups': groups, 'pairs': pairs}


def extract_markers(raw, source, physical):
    markers = []
    for block_index, block in enumerate(block_parts(raw)):
        if block['kind'] != 'texto_principal' or block['script'] != 'grego':
            continue
        body = block['body']
        for match in NUMBER.finditer(body):
            number = int(match[1])
            if not 9 <= number <= 181:
                continue
            before, after = body[:match.start()].rstrip(), body[match.end():].lstrip()
            if before.endswith(('(', '[')) or after.startswith((')', ']')):
                continue
            markers.append({'number': number, 'physical_page': physical,
                            'source': source, 'block_index': block_index,
                            'source_offset': block['start_offset'] + match.start(),
                            **compact_match(raw, block, match)})
    return markers


def build_intervals(markers, sources):
    nodes = defaultdict(set)
    for m in markers:
        nodes[m['number']].add(m['physical_page'])
    supported = supported_sequence_nodes(nodes)
    occurrences = defaultdict(list)
    for m in markers:
        key = m['number'], m['physical_page']
        m['sequence_support'] = supported.get(key)
        # Multiple raw occurrences remain ambiguous even if only one is supported.
        occurrences[m['number']].append(m)
    intervals = {}
    for number in range(9, 182):
        starts, ends = occurrences[number], occurrences[number+1]
        item = {'antwerp': number, 'status': 'missing_or_ambiguous_boundary',
                'start_occurrences': starts, 'end_occurrences': ends, 'files': []}
        if len(starts) == len(ends) == 1:
            start, end = starts[0], ends[0]
            a, b = start['physical_page'], end['physical_page']
            ordered = a < b or (a == b and start['source_offset'] < end['source_offset'])
            complete = all(n in sources for n in range(a, b+1))
            if ordered and 0 <= b-a <= 3 and complete and start['sequence_support'] and end['sequence_support']:
                item['status'] = 'bounded_marker_candidate'
                item['files'] = [{'physical_page': n, **sources[n]} for n in range(a, b+1)]
                item['limitation'] = 'Boundary files included; Greek OCR order and marker identity require review.'
        intervals[number] = item
    return intervals


def investigate(db, root):
    grouped = page_files(root, suffixes=('.txt',))

    def select(n):
        path, ambiguous = choose_page_file(grouped.get(n, []), volume_id='PG040', page_num=n)
        if not path or ambiguous:
            raise ValueError(f'Missing or ambiguous physical page {n}')
        return path

    raw_table, table_source = read_page(select(427))
    raw_notice, notice_source = read_page(select(426))
    if not re.search(r'Numeri\s+signant\s+paginas\s+editionis\s+Antverpiensis', raw_notice):
        raise ValueError('Index edition notice missing')
    table = extract_concordance(raw_table, table_source)
    sources, bodies, markers, missing = {}, {}, [], []
    for n in range(253, 416):
        try:
            path = select(n)
        except ValueError:
            missing.append(n)
            continue
        raw, proof = read_page(path)
        sources[n] = proof
        bodies[n] = normalize_for_search('\n'.join(
            b['body'] for b in block_parts(raw) if b['kind'] == 'texto_principal'))
        markers.extend(extract_markers(raw, proof, n))
    intervals = build_intervals(markers, sources)
    pairs = {p['antwerp']: p for p in table['pairs']}
    for pair in pairs.values():
        interval = intervals[pair['antwerp']]
        pair['antwerp_interval_status'] = interval['status']
        pair['physical_candidates'] = interval['files'] if pair['status'] != 'ocr_anomaly' else []
        pair['mapping_basis'] = 'Oxford via OCR table and Antwerp markers; no independent Oxford anchor'
    with closing(connect_readonly(db)) as con:
        rows = [dict(r) for r in con.execute(QUERY, ('PG040',))]
    records = []
    for row in rows:
        number = cited_start(row)
        interval = intervals.get(number)
        source_start = Path(row['section_file_start'] or '')
        in_scope = source_start == select(426) or source_start.resolve() == select(426).resolve()
        existing = row['locator_status'] == 'resolved' or row['target_file'] or row['resolved_target_ocr_file']
        status = ('existing_resolution_preserved' if existing else 'section_outside_index_scope' if not in_scope
                  else interval['status'] if interval else 'number_outside_marker_scope')
        files = interval['files'] if interval and in_scope else []
        queries = [q for q in queries_for(row) if len(q) >= 4]
        hints = []
        for candidate in files:
            body = bodies[candidate['physical_page']]
            matches = [q for q in queries if ' '+q+' ' in ' '+body+' ']
            if matches:
                hints.append({**candidate, 'normalized_queries': matches,
                              'scope': 'whole physical body; not bounded to the cited Greek span'})
        records.append({'ref_id': row['ref_id'], 'entry_key': row['entry_key'],
                        'lemma': row['lemma_raw'], 'reference_raw': row['ref_raw'],
                        'antwerp': number, 'db_locator_status': row['locator_status'],
                        'status': status, 'physical_candidates': files,
                        'whole_file_name_hints': hints,
                        'oxford_concordance': pairs.get(number), 'row_snapshot': row,
                        'needs_review': True, 'application_supported': False})
    summary = {'mode': 'diagnostic_only', 'application_supported': False, 'version': VERSION,
               'db': str(Path(db).resolve()),
               'reference_snapshot_sha256': digest(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()),
               'body_files_read': len(sources), 'missing_or_ambiguous_body_pages': missing,
               'table_groups': [{'group': g['group'], 'status': g['status'],
                                 'antwerp_rows': len(g['antwerp_rows']), 'oxford_rows': len(g['oxford_rows'])}
                                for g in table['groups']],
               'aligned_pairs': len(pairs), 'anomalous_pairs': [p['antwerp'] for p in pairs.values() if p['status']=='ocr_anomaly'],
               'marker_occurrences': len(markers),
               'interval_counts': dict(Counter(i['status'] for i in intervals.values())),
               'reference_counts': dict(Counter(r['status'] for r in records)),
               'pending_bounded_with_single_whole_file_name_hint': sum(
                   r['status']=='bounded_marker_candidate' and len(r['whole_file_name_hints'])==1 for r in records),
               'api_calls': 0, 'db_updates': 0,
               'scope': {'volume': 'PG040', 'work': 'Nemesius', 'physical_start': 253, 'physical_end': 415,
                         'index_notice': notice_source, 'table': table_source},
               'script_sha256': digest(Path(__file__).read_bytes()),
               'inputs': [table_source, notice_source, *sources.values()]}
    return summary, table, markers, list(intervals.values()), records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    parser.add_argument('--root', type=Path, default=ROOT/'teste/PG040/text')
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.out_dir.exists():
        parser.error('Use a new output directory')
    summary, table, markers, intervals, records = investigate(args.db, args.root)
    args.out_dir.mkdir(parents=True)
    for name, value in [('summary', summary), ('concordance', table), ('markers', markers),
                        ('intervals', intervals), ('references', records)]:
        with (args.out_dir/(name+'.json')).open('x', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write('\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('inputs','scope')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
