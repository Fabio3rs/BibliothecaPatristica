"""Join literal-parser candidates to bounded internal-number sequences, offline.

Only experimental SQLite is writable. A numbering profile is mandatory;
sequence-supported markers produce review windows, never resolved references.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import uuid

from scripts.audit_alphabetical_numbering import build_series, supported_sequence_nodes
from tools.corpus_utils import page_files, page_number
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.index_target_locator import parse_ocr_page_path
from tools.indexing.parser_target_notation import interpret
from tools.indexing.title_page_experiment import extract_title_page
from tools.indexing.parser_target_exports import editorial_maps, review_queues, write_jsonl

VERSION = 'parser-target-window-v4'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def markdown_cell(value):
    return ' '.join(str(value).split()).replace('|', '\\|')


def volume_bindings(profile, specs, root):
    """Check the evidence for declared bibliographic routes, never infer a tome.

    Quotes/hashes establish provenance of the profile's interpretation; they do
    not automatically certify its editorial meaning or any target page.
    """
    bindings, sources, work_groups = {}, {}, {}
    for spec in specs:
        key = spec.get('key', 'default')
        cross_volume = spec['volume'] != profile['volume']
        required = cross_volume or profile['notation'] in {'explicit_tome_page', 'explicit_tome_list'} or bool(spec.get('tome'))
        evidence = spec.get('binding_evidence', [])
        if required and (not evidence or not all(
                isinstance(spec.get(k), str) and spec[k].strip() for k in ['cited_work', 'cited_edition'])):
            raise ValueError(f'{key}: tome/cross-volume mapping requires work, edition and binding evidence')
        if profile['notation'] in {'explicit_tome_page', 'explicit_tome_list'} and spec.get('tome') != key:
            raise ValueError(f'{key}: series key must match its explicit tome')
        checked = []
        for proof in evidence:
            path = (root / proof['path']).resolve()
            if not path.is_relative_to((root / spec['volume']).resolve()):
                raise ValueError(f'{key}: binding evidence must belong to target volume')
            data = path.read_bytes()
            quotes = proof.get('quotes')
            if (sha(data) != proof['sha256'] or not isinstance(quotes, list) or not quotes
                    or any(not isinstance(q, str) or not q.strip() or q not in data.decode('utf-8') for q in quotes)):
                raise ValueError(f'{key}: binding evidence changed or quote missing')
            sources[str(path)] = sha(data)
            item = {**proof, 'path': str(path)}
            if proof.get('role') == 'work_title_page':
                title = extract_title_page(data.decode('utf-8'), source=str(path),
                                           volume=spec['volume'], physical=page_number(path))
                if title['status'] != 'structured_candidate' or title['work_tome_roman'] != spec.get('tome'):
                    raise ValueError(f'{key}: structured title page does not support configured work tome')
                previous = work_groups.setdefault(spec.get('cited_work'), title['work_group_key'])
                if previous != title['work_group_key']:
                    raise ValueError(f'{key}: conflicting title-page author/work for the same cited work')
                item['structured_title_page'] = title
            checked.append(item)
        binding = {'index_volume': profile['volume'], 'target_volume': spec['volume'],
                   'cross_volume': cross_volume, 'series_key': key, 'namespace': spec['id'],
                   'cited_work': spec.get('cited_work'), 'cited_edition': spec.get('cited_edition'),
                   'cited_tome': spec.get('tome'),
                   'index_physical_scope': [profile['index_physical_start'], profile['index_physical_end']],
                   'target_physical_scope': [spec['physical_start'], spec['physical_end']],
                   'editorial_notice': profile['notice'], 'binding_evidence': checked,
                   'route_status': 'documented_profile' if checked else 'local_profile'}
        binding['binding_id'] = sha(json.dumps(binding, sort_keys=True, ensure_ascii=False).encode())
        bindings[key] = binding
    return bindings, sources


def reference_route(index_volume, binding):
    if binding is None:
        return {'index_volume': index_volume, 'target_volume': None, 'cross_volume': None,
                'binding_id': None, 'cited_work': None, 'cited_edition': None,
                'cited_tome': None, 'route_status': 'unmapped'}
    return {k: binding[k] for k in ['index_volume', 'target_volume', 'cross_volume',
            'binding_id', 'cited_work', 'cited_edition', 'cited_tome', 'route_status']}


def reference_numbers(entry):
    """Called only inside a profile explicitly declaring a plain page list.

    Keep repeats and occurrence order; never flatten page/line, ranges or tomes.
    """
    raw = entry.get('references_raw') or ''
    if entry.get('entry_type') != 'material' or entry.get('flags'):
        return None
    if not re.fullmatch(r'\s*[0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*\s*', raw):
        return None
    result = [int(x) for x in raw.split(',')]
    return result if all(result) else None


def marker_options(series, number):
    options = []
    for seq in sorted(series['nodes'].get(number, ())):
        support = series['supported'].get((number, seq))
        if not support:
            continue
        page = series['pages'][seq]
        for occurrence in page['occurrences'][number]:
            options.append({'physical': seq, 'file': page['file'], 'sha256': page['sha256'],
                            'occurrence': occurrence, 'sequence_support': support})
    return options


def locate_number(series, number, max_file_gap=3, range_end=None):
    if range_end is not None and range_end < number:
        raise ValueError('Reversed editorial range')
    end_boundary = (range_end if range_end is not None else number) + 1
    starts, ends = marker_options(series, number), marker_options(series, end_boundary)
    record = {'number': number, 'status': 'missing_supported_boundary',
              'start_options': starts, 'end_options': ends, 'files': [],
              'verified': False, 'namespace': series['spec']['id'],
              'range_end': range_end, 'end_boundary_number': end_boundary}
    if len(starts) > 1 or len(ends) > 1:
        record['status'] = 'ambiguous_markers'
    if len(starts) != 1 or len(ends) != 1:
        return record
    start, end = starts[0], ends[0]
    a, b = start['physical'], end['physical']
    order = series.get('page_order')
    if order is not None:
        if len(order) != len(set(order)) or set(order) != set(series['pages']):
            raise ValueError('Explicit page order must cover each source exactly once')
        positions = {physical: rank for rank, physical in enumerate(order)}
        left, right = positions[a], positions[b]
        window = order[left:right + 1]
        record['file_order_basis'] = 'explicit_editorial_order'
    else:
        left, right = a, b
        window = range(a, b + 1)
    if right < left or (a == b and start['occurrence']['body_offset'] >= end['occurrence']['body_offset']):
        record['status'] = 'reversed_markers'
        return record
    if right - left > max_file_gap:
        record['status'] = 'window_exceeds_cap'
        return record
    if any(n not in series['pages'] for n in window):
        record['status'] = 'missing_intermediate_file'
        return record
    record['status'] = 'bounded_window_candidate'
    record['files'] = [{'physical': n, 'file': series['pages'][n]['file'],
                        'sha256': series['pages'][n]['sha256'],
                        'header': series['pages'][n]['header']}
                       for n in window]
    return record


def build_header_series(spec, root):
    """Only literal bracketed header numbers, within explicitly scoped work bounds."""
    grouped = page_files(root / spec['volume'] / 'text', suffixes=['.txt'])
    sources, pages, nodes = [], {}, defaultdict(set)
    for n in sorted(grouped):
        if not spec['physical_start'] <= n <= spec['physical_end']:
            continue
        path = grouped[n][0].resolve()
        data = path.read_bytes()
        header = parse_ocr_page_path(path)['header_text']
        checksum = sha(data)
        numbers = sorted({int(m[1]) for m in re.finditer(r'\[\s*([0-9]{1,4})\s*\]', header)})
        pages[n] = {'file': str(path), 'sha256': checksum, 'header': header, 'numbers': numbers}
        sources.append([str(path), checksum])
        for number in numbers:
            nodes[number].add(n)
    return {'spec': spec, 'sources': sources, 'pages': pages, 'nodes': nodes,
            'supported': supported_sequence_nodes(nodes), 'anchor_rejected_nodes': 0}


def locate_header(series, number):
    choices = [n for n in sorted(series['nodes'].get(number, ())) if (number, n) in series['supported']]
    files = [{'physical': n, **series['pages'][n], 'sequence_support': series['supported'][number, n]}
             for n in choices]
    return {'number': number, 'namespace': series['spec']['id'], 'verified': False,
            'status': 'header_page_candidate' if len(files) == 1 else 'ambiguous_header_pages' if files else 'missing_supported_header',
            'files': files}


def load_candidates(db, run_id, profile, review_records=None):
    counts, candidates, source_hashes = Counter(), [], {}
    con = readonly(db)
    try:
        marker = con.execute("SELECT value FROM experiment_meta WHERE key='kind'").fetchone()
        if not marker or marker[0] != 'index-parser-survey-v1':
            raise ValueError('Not a parser experiment database')
        run = con.execute('SELECT status FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if not run or run[0] != 'completed':
            raise ValueError('Require a completed parser run')
        for row in con.execute('''SELECT p.*, t.result_json FROM run_pages p
                JOIN tasks t USING(fingerprint) WHERE p.run_id=? ORDER BY volume,physical,source''', (run_id,)):
            result = json.loads(row['result_json'])
            page = result['page']
            if not page:
                counts['source_task_errors'] += 1
                continue
            entries = page['entries']
            if row['volume'] != profile['volume'] or not (
                    profile['index_physical_start'] <= row['physical'] <= profile['index_physical_end']):
                counts['outside_configured_index_scope'] += len(entries)
                continue
            if row['profile'] != profile['parser_profile']:
                counts['parser_profile_mismatch'] += len(entries)
                continue
            raw_bytes = Path(row['source']).read_bytes()
            if sha(raw_bytes) != page['sha256']:
                raise ValueError(f"Stale parser source: {row['source']}")
            source_hashes[row['source']] = page['sha256']
            raw = raw_bytes.decode('utf-8')
            for entry in entries:
                if raw[entry['char_start']:entry['char_end']] != entry['entry_raw']:
                    raise ValueError('Parser entry span no longer matches source')
                if profile['notation'] == 'plain_page_list':
                    numbers = reference_numbers(entry)
                    interpretation = None if numbers is None else {
                        'query_text': entry['text_display'], 'interpreted_references': [
                            {'number': int(m[0]), 'series_key': 'default', 'ref_raw': m[0],
                             'source_span': [entry['reference_span']['char_start']+m.start(),
                                             entry['reference_span']['char_start']+m.end()]}
                            for m in re.finditer(r'[0-9]+', entry['references_raw'])]}
                else:
                    interpretation = interpret(entry, profile['notation'])
                if interpretation is None:
                    counts['entry_or_notation_requires_review'] += 1
                    if review_records is not None:
                        review_records.append({'fingerprint': row['fingerprint'], 'entry': entry,
                            'source': row['source'], 'source_sha256': page['sha256'],
                            'volume': row['volume'], 'physical': row['physical'],
                            'reason': 'entry_or_notation_requires_review'})
                    continue
                counts['eligible_entries'] += 1
                candidates.append({'fingerprint': row['fingerprint'], 'entry': entry,
                                   'source': row['source'], 'source_sha256': page['sha256'],
                                   'volume': row['volume'], 'physical': row['physical'],
                                   'numbers': [r['number'] for r in interpretation['interpreted_references']],
                                   **interpretation})
    finally:
        con.close()
    return candidates, counts, source_hashes


def run(args):
    profile = json.loads(args.profile.read_text())
    if profile.get('notation') not in {'plain_page_list', 'explicit_tome_page', 'explicit_tome_list', 'page_line_leo_simple', 'po_page_parts'}:
        raise ValueError('Unsupported notation profile')
    if args.limit < 1 or args.max_file_gap < 0:
        raise ValueError('Require limit > 0 and max-file-gap >= 0')
    specs = profile['series'] if isinstance(profile['series'], list) else [profile['series']]
    keys = [s.get('key', 'default') for s in specs]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate series keys')
    for spec in specs:
        method = spec.get('method', profile.get('method'))
        if method not in {'body_sequence', 'bracketed_header_sequence'}:
            raise ValueError('Unsupported numbering method')
        grouped = page_files(args.root / spec['volume'] / 'text', suffixes=['.txt'])
        duplicates = [n for n, paths in grouped.items() if len(paths) > 1
                      and spec['physical_start'] <= n <= spec['physical_end']]
        if duplicates:
            raise ValueError(f'Ambiguous physical source files: {duplicates}')
    proof = profile['notice']
    notice_path = args.root / proof['path']
    notice = notice_path.read_bytes()
    if sha(notice) != proof['sha256'] or proof['quote'] not in notice.decode('utf-8'):
        raise ValueError('Editorial notice changed or quote missing')
    bindings, binding_sources = volume_bindings(profile, specs, args.root)
    notation_pending = []
    candidates, counts, source_hashes = load_candidates(args.experiment_db, args.parser_run, profile, notation_pending)
    source_hashes[str(notice_path.resolve())] = sha(notice)
    source_hashes.update(binding_sources)
    focused = [c for c in candidates if any(t.casefold() in c['entry']['text_display'].casefold()
                                          for t in args.include_term)]
    if len(focused) > args.limit:
        raise ValueError('Increase limit to preserve all explicitly requested matching entries')
    remaining = [c for c in candidates if c not in focused]
    selected = focused + random.Random(args.seed).sample(remaining, min(len(remaining), args.limit-len(focused)))
    if not selected:
        raise ValueError('No eligible experimental entries')
    args.out.mkdir(parents=True, exist_ok=False)
    series_by_key = {}
    for spec in specs:
        method = spec.get('method', profile.get('method'))
        series = build_series(spec, args.root) if method == 'body_sequence' else build_header_series(spec, args.root)
        series['method'] = method
        series_by_key[spec.get('key', 'default')] = series
        source_hashes.update(dict(series['sources']))
    records = []
    for candidate in selected:
        refs = []
        for i, interpreted in enumerate(candidate['interpreted_references']):
            series = series_by_key.get(interpreted['series_key'])
            binding = bindings.get(interpreted['series_key'])
            if binding and interpreted.get('tome') != binding['cited_tome']:
                raise ValueError('Reference tome conflicts with its configured volume binding')
            if series is None:
                located = {'status': 'namespace_not_mapped', 'files': [], 'verified': False}
            elif series['method'] == 'body_sequence':
                located = locate_number(series, interpreted['number'], args.max_file_gap, interpreted.get('range_end'))
            else:
                located = locate_header(series, interpreted['number'])
            refs.append({**interpreted, **located, **reference_route(profile['volume'], binding), 'occurrence': i+1})
        files = sorted({f['file'] for r in refs for f in r['files']})
        records.append({**candidate, 'references': refs, 'candidate_files': files,
                        'all_occurrences_bounded': all(r['status'] in {'bounded_window_candidate', 'header_page_candidate'} for r in refs),
                        'application_supported': False})
    for path, checksum in source_hashes.items():
        if sha(Path(path).read_bytes()) != checksum:
            raise ValueError(f'OCR changed during experiment: {path}')
    maps = editorial_maps(series_by_key, bindings)
    semantic_jobs, mechanical_pending = review_queues(records, maps)
    run_id = uuid.uuid4().hex
    config = {'version': VERSION, 'run_id': run_id, 'parser_run': args.parser_run,
              'profile': profile, 'profile_sha256': sha(args.profile.read_bytes()), 'volume_bindings': bindings,
              'code_hashes': {str(Path(p).resolve()): sha(Path(p).read_bytes()) for p in
                              [__file__, 'scripts/audit_alphabetical_numbering.py', 'tools/indexing/parser_target_notation.py',
                               'tools/indexing/title_page_experiment.py', 'tools/indexing/parser_target_exports.py']},
              'seed': args.seed, 'limit': args.limit, 'max_file_gap': args.max_file_gap,
              'include_terms': args.include_term, 'root': str(args.root.resolve()),
              'experiment_db': str(args.experiment_db.resolve()), 'selection': 'focused_terms_then_seeded_random',
              'limitations': ['Pilot target window is bounded, not a whole-volume benchmark.',
                  'Internal integers can be notes/citations. Sequence support is heuristic.',
                  'Boundary files include text outside the cited editorial page.',
                  'body_offset measures cleaned selected-script body, not raw XML.',
                  'No semantic validation or automatic resolution.']}
    statistics = {'run_id': run_id, 'selection_counts': dict(counts), 'selected_entries': len(records),
                  'reference_occurrences': sum(len(r['references']) for r in records),
                  'reference_statuses': dict(Counter(ref['status'] for r in records for ref in r['references'])),
                  'reference_routes': dict(Counter(
                      f"{ref['index_volume']}->{ref['target_volume'] or 'unmapped'}"
                      for r in records for ref in r['references'])),
                  'entries_all_occurrences_bounded': sum(r['all_occurrences_bounded'] for r in records),
                  'target_files_scanned': sum(len(s['pages']) for s in series_by_key.values()), 'focused_entries': len(focused),
                  'reviewed_anchor_count': sum(len(s.get('reviewed_anchors', [])) for s in specs),
                  'anchor_rejected_nodes': sum(s['anchor_rejected_nodes'] for s in series_by_key.values()),
                  'max_files_per_entry': max(map(lambda r: len(r['candidate_files']), records)),
                  'semantic_candidate_jobs': len(semantic_jobs), 'mechanical_pending_occurrences': len(mechanical_pending),
                  'notation_pending_entries': len(notation_pending), 'editorial_maps': len(maps),
                  'production_db_updates': 0, 'inference_calls': 0, 'import_ready': False}
    write_json(args.out/'config.json', config)
    write_json(args.out/'volume_bindings.json', bindings)
    write_json(args.out/'sources.json', source_hashes)
    write_json(args.out/'results.json', records)
    write_json(args.out/'editorial_maps.json', maps)
    write_jsonl(args.out/'semantic_candidates.jsonl', semantic_jobs)
    write_jsonl(args.out/'mechanical_pending.jsonl', mechanical_pending)
    write_jsonl(args.out/'notation_pending.jsonl', notation_pending)
    write_json(args.out/'summary.json', statistics)
    # The only writable database is the validated experiment DB, never production.
    con = sqlite3.connect(args.experiment_db)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''CREATE TABLE IF NOT EXISTS parser_locator_runs(
            run_id TEXT PRIMARY KEY, parser_run TEXT, config_json TEXT, summary_json TEXT);
            CREATE TABLE IF NOT EXISTS parser_locator_results(
            run_id TEXT, fingerprint TEXT, candidate_id TEXT, result_json TEXT,
            PRIMARY KEY(run_id,fingerprint,candidate_id));''')
        with con:
            con.execute('INSERT INTO parser_locator_runs VALUES(?,?,?,?)',
                        (run_id, args.parser_run, json.dumps(config), json.dumps(statistics)))
            con.executemany('INSERT INTO parser_locator_results VALUES(?,?,?,?)',
                            [(run_id, r['fingerprint'], r['entry']['id'], json.dumps(r, ensure_ascii=False)) for r in records])
    finally:
        con.close()
    lines = ['# Piloto de localização de candidatos do parser', '',
             'Somente candidatos para revisão; nenhum destino promovido. Janelas limitadas pelo perfil.', '']
    for b in bindings.values():
        lines.append(f"- Índice {b['index_volume']}; série {markdown_cell(b['series_key'])}; "
                     f"obra {markdown_cell(b['cited_work'] or 'não identificada')}; "
                     f"edição {markdown_cell(b['cited_edition'] or 'não identificada')}; "
                     f"tomo {markdown_cell(b['cited_tome'] or 'não indicado')}: "
                     f"procurar em **{b['target_volume']}**. Evidências em `volume_bindings.json`.")
    lines.extend(['',
             '| Verbete | Referência → arquivos físicos candidatos | Todas as ocorrências delimitadas |',
             '|---|---|---|'])
    for r in records:
        cells = [f"{ref.get('series_key', '')}: {markdown_cell(ref.get('ref_raw', ref['number']))} → " +
                 (', '.join(Path(f['file']).parent.parent.name + '/' + str(f['physical']) for f in ref['files']) or ref['status'])
                 for ref in r['references']]
        lines.append('| ' + markdown_cell(r['query_text']) + ' | ' + '; '.join(cells) +
                     ' | ' + str(r['all_occurrences_bounded']) + ' |')
    (args.out/'report.md').write_text('\n'.join(lines)+'\n')
    return statistics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--experiment-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    ap.add_argument('--parser-run', required=True)
    ap.add_argument('--profile', type=Path, required=True)
    ap.add_argument('--root', type=Path, default=Path('teste'))
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--include-term', action='append', default=[])
    ap.add_argument('--max-file-gap', type=int, default=3)
    ap.add_argument('--out', type=Path, required=True)
    print(json.dumps(run(ap.parse_args()), ensure_ascii=False, indent=2))
