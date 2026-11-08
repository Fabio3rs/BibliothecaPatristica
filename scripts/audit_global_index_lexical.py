#!/usr/bin/env python3
"""Audit existing corpus-wide candidate links against current and historical OCR.

Twenty independent volume processes; immutable inputs, isolated WAL experiment.
Lexical matches never promote numeric coincidences to resolved references.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rapidfuzz.distance import Levenshtein
from scripts.audit_po015_names_lexical import (
    PRIORITY, history_page_identity, match_tokens, read_history_text, script_of, tokens,
)
from scripts.catalog_po015_names import dump, sha
from tools.corpus_utils import page_files
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'global-index-lexical-v2'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def index_text(raw):
    page = read_history_text(raw)
    for b in page['blocks']:
        # Latin-script body is NOT necessarily a translation outside PO015.
        if b['kind'] == 'texto_principal':
            b['role'] = 'body'
        positions = defaultdict(list)
        for i, t in enumerate(b['tokens']):
            positions[t['norm']].append(i)
        b['positions'] = positions
    return page


def fast_matches(page, query):
    """Same token criteria as PO015, vocabulary prefilter avoids repeated scans."""
    qs = list(tokens(query))
    if not qs:
        return []
    first = qs[0]['norm']
    found = []
    for b in page['blocks']:
        starts = []
        for word, positions in b['positions'].items():
            if word == first or (min(len(word), len(first)) >= 5 and
                    abs(len(word) - len(first)) <= 2 and script_of(word) == script_of(first) and
                    Levenshtein.distance(word, first, score_cutoff=2) <= 2 and
                    1 - Levenshtein.distance(word, first) / max(len(word), len(first)) >= 0.8):
                starts.extend(positions)
        for pos in sorted(starts):
            window = b['tokens'][pos:pos + len(qs)]
            for h in match_tokens(query, window):
                a, z = h['start'], h['end']
                found.append({'method': h['method'], 'score': h['score'], 'role': b['role'],
                              'block_kind': b['kind'], 'block': b['ordinal'],
                              'char_start': b['body_start'] + a, 'char_end': b['body_start'] + z,
                              'text_raw': b['body'][a:z],
                              'snippet': ' '.join(b['body'][max(0, a - 80):z + 80].split())})
    return sorted(found, key=lambda h: (PRIORITY[h['method']], -h['score'], h['char_start']))


def search(page, query):
    hits = fast_matches(page, query)
    scope = 'full_label'
    q = list(tokens(query))
    if not hits and len(q) > 1:
        hits = fast_matches(page, q[0]['raw'])
        scope = 'first_token_only'
    return {'method': hits[0]['method'] if hits else 'absent', 'query_scope': scope,
            'matches': hits, 'match_count': len(hits)}


def load_records(survey, volume, catalog):
    directory = survey / 'volumes' / volume
    original_summary = json.loads((directory / 'summary.json').read_text())
    entries = json.loads((catalog / 'entries.json').read_text()) if volume == 'PO015' else []
    replaced = {e['source'] for e in entries}
    groups = {}
    for line in (directory / 'groups.jsonl').open():
        g = json.loads(line)
        if g['source'] not in replaced:
            groups[g['id']] = g
    def records():
        for line in (directory / 'occurrences.jsonl').open():
            r = json.loads(line)
            g = groups.get(r['group_id'])
            if g is None:
                continue
            yield {'id': r['id'], 'group_id': r['group_id'], 'query': r.get('owner_raw', g['owner_raw']),
                   'source': g['source'], 'source_sha256': g['source_sha256'],
                   'source_span': r.get('source_span', [r.get('char_start', g['char_start']), r.get('char_end', g['char_end'])]),
                   'ref_raw': r.get('ref_raw', r.get('raw', g['raw'])),
                   'group_source_span': [g['char_start'], g['char_end']],
                   'reference_interpretation': {k: r[k] for k in ('candidate_role', 'series_key', 'grammar_version',
                       'uncertainties', 'flags', 'owner_span', 'detail_raw', 'line_raw', 'parser_residuals') if k in r},
                   'tier': r['tier'], 'original_status': r['status'], 'group_flags': g.get('flags', []),
                   'target_files': r.get('files', []), 'selection': 'global_survey'}
        for e in entries:
            for r in e['references']:
                yield {'id': 'po015:' + r['id'], 'group_id': e['id'], 'query': e['name_raw'],
                       'source': r['source'], 'source_sha256': r['source_sha256'],
                       'source_span': [r['char_start'], r['char_end']], 'ref_raw': r['ref_raw'],
                       'reference_interpretation': {k: r[k] for k in ('notation_status', 'detail_raw',
                           'uncertainties', 'grammar_version') if k in r},
                       'tier': 'po015_catalog', 'original_status': r['status'], 'group_flags': [],
                       'target_files': r['target_files'], 'selection': 'po015_catalog_override'}
    return records(), original_summary, Counter(g['status'] for g in groups.values())


def volume_worker(config, volume):
    started = time.monotonic()
    out = Path(config['out']) / 'volumes' / volume
    out.mkdir(parents=True)
    survey, root = Path(config['survey']), Path(config['root'])
    records, baseline, group_statuses = load_records(survey, volume, Path(config['catalog']))
    counts, tiers, version_counts = Counter(), defaultdict(Counter), Counter()
    history = readonly(config['versions_db'])
    history.execute('BEGIN')
    versions_seen, targets_seen, source_proofs = set(), set(), {}
    snapshot_dir = out / 'version_sources'
    snapshot_dir.mkdir()
    inventory = (out / 'version_inventory.jsonl').open('w')
    targets_log = (out / 'targets.jsonl').open('w')

    @lru_cache(maxsize=8192)
    def source_text(path, expected):
        try:
            raw = Path(path).read_bytes()
        except OSError:
            return None, 'missing_source'
        if hashlib.sha256(raw).hexdigest() != expected:
            return None, 'changed_source'
        source_proofs[path] = expected
        return raw.decode('utf-8'), None

    @lru_cache(maxsize=96)
    def current_page(path, expected):
        try:
            raw = Path(path).read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            if checksum != expected:
                if (path, expected) not in targets_seen:
                    targets_seen.add((path, expected))
                    targets_log.write(encoded({'file': path, 'sha256': expected,
                                               'actual_sha256': checksum, 'state': 'changed_target'}) + '\n')
                return None, 'changed_target'
            page = index_text(raw.decode('utf-8'))
            state = 'read'
        except (OSError, ValueError, UnicodeError) as exc:
            page, state = None, 'target_read_or_format_error:' + type(exc).__name__
        if (path, expected) not in targets_seen:
            targets_seen.add((path, expected))
            targets_log.write(encoded({'file': path, 'sha256': expected, 'state': state}) + '\n')
        return page, None if page is not None else state

    @lru_cache(maxsize=24)
    def images_for(target_volume):
        return page_files(root / target_volume / 'images', suffixes=['.png'])

    @lru_cache(maxsize=48)
    def alternatives(path, physical, expected):
        target_volume = Path(path).parent.parent.name
        imgs = images_for(target_volume).get(physical, [])
        image_path = imgs[0] if len(imgs) == 1 else None
        image_sha = sha(image_path) if image_path else None
        available = []
        total = 0
        for row in history.execute('''SELECT r.id,r.volume_id,r.page_num,r.image_hash,r.text_hash,
                r.text_content,r.is_current,r.created_at,r.engine_version_id,e.engine,e.model
                FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id''', (target_volume, physical)):
            total += 1
            meta = dict(row)
            raw = meta.pop('text_content')
            actual = hashlib.sha256(raw.encode()).hexdigest()
            identity = history_page_identity(target_volume, physical, meta['image_hash'], image_sha)
            meta.update(image=str(image_path.resolve()) if image_path else None,
                        local_image_sha256=image_sha, page_identity=identity,
                        computed_text_sha256=actual, target_file=path)
            page = None
            if identity.startswith('excluded_'):
                meta['status'] = identity
            elif actual != meta['text_hash']:
                meta['status'] = 'text_hash_mismatch'
            elif actual == expected:
                meta['status'] = 'identical_to_current'
            else:
                try:
                    page = index_text(raw)
                    meta['status'] = 'searched'
                except ValueError:
                    meta['status'] = 'unsupported_format'
            if meta['id'] not in versions_seen:
                versions_seen.add(meta['id'])
                version_counts[meta['status']] += 1
                inventory.write(encoded(meta) + '\n')
            if page is not None:
                available.append((meta, page))
        return available, 'available' if available else 'no_searchable_alternative' if total else 'no_history'

    try:
        with (out / 'evidence.jsonl').open('w') as results:
            for r in records:
                counts['occurrences'] += 1
                tier = tiers[r['tier']]
                tier['occurrences'] += 1
                row = {**{k: v for k, v in r.items() if k != 'target_files'},
                       'volume': volume, 'verified': False, 'import_ready': False,
                       'line_status': 'not_inferred', 'targets': []}
                raw, error = source_text(r['source'], r['source_sha256'])
                a, b = r['source_span']
                if error or raw[a:b] != r['ref_raw']:
                    status = error or 'invalid_reference_span'
                elif not list(tokens(r['query'])):
                    status = 'empty_query'
                elif not r['target_files']:
                    status = 'no_candidate_files'
                else:
                    for target in r['target_files']:
                        path = target.get('file', target.get('path'))
                        page, error = current_page(path, target['sha256'])
                        t = {**target, 'file': path, 'physical': target['physical'], 'sha256': target['sha256'],
                             'current': None, 'alternatives': [], 'error': error}
                        if page is not None:
                            t['current'] = search(page, r['query'])
                            if t['current']['method'] == 'absent':
                                docs, state = alternatives(path, target['physical'], target['sha256'])
                                t['history_state'] = state
                                for meta, alt in docs:
                                    found = search(alt, r['query'])
                                    if found['method'] == 'absent':
                                        continue
                                    snapshot = snapshot_dir / f'version-{meta["id"]}.txt'
                                    if not snapshot.exists():
                                        snapshot.write_bytes(alt['raw'].encode())
                                    t['alternatives'].append({**found, 'version_id': meta['id'],
                                        'engine': meta['engine'], 'model': meta['model'],
                                        'text_sha256': meta['text_hash'], 'image_sha256': meta['image_hash'],
                                        'page_identity': meta['page_identity'],
                                        'snapshot': str(snapshot.resolve())})
                        row['targets'].append(t)
                    currents = [t['current'] for t in row['targets'] if t['current']]
                    present = [c for c in currents if c['method'] != 'absent']
                    recovered = [h for t in row['targets'] for h in t['alternatives']]
                    if present:
                        status = 'current_' + min((c['method'] for c in present), key=PRIORITY.get)
                    elif recovered:
                        status = 'historical_' + min((c['method'] for c in recovered), key=PRIORITY.get)
                    elif any(t['error'] for t in row['targets']):
                        status = 'target_incomplete'
                    else:
                        status = 'no_lexical_match'
                    row['full_label_evidence'] = any(c['query_scope'] == 'full_label' for c in present + recovered)
                    row['first_token_only_evidence'] = bool(present or recovered) and not row['full_label_evidence']
                    row['history_added_candidate_files'] = sum(bool(t['alternatives']) for t in row['targets'])
                    row['candidate_files_not_read'] = sum(bool(t['error']) for t in row['targets'])
                    if row['full_label_evidence']:
                        counts['full_label_evidence'] += 1
                    if row['first_token_only_evidence']:
                        counts['first_token_only_evidence'] += 1
                row['status'] = status
                counts[status] += 1
                tier[status] += 1
                results.write(encoded(row) + '\n')
                if counts['occurrences'] % 1000 == 0:
                    dump(out / 'progress.json', {'volume': volume, 'pid': os.getpid(), 'counts': dict(counts)})
        changed = [p for p, expected in source_proofs.items() if not Path(p).exists() or sha(Path(p)) != expected]
        summary = {'volume': volume, 'status': 'completed' if not changed else 'source_changed_during_run',
                   'pid': os.getpid(), 'seconds': time.monotonic() - started, 'counts': dict(counts),
                   'tiers': {k: dict(v) for k, v in tiers.items()}, 'historical_versions': dict(version_counts),
                   'source_changes': changed, 'target_files_opened': len(targets_seen),
                   'baseline_selected_pages': baseline.get('counts', {}).get('selected_source_pages', 0),
                   'baseline_group_states': dict(group_statuses),
                   'coverage_state': 'audited_candidates' if counts['occurrences'] else
                        'no_candidate_occurrences' if baseline.get('counts', {}).get('selected_source_pages', 0) else 'no_selected_extraction',
                   'verified_links': 0, 'production_updates': 0, 'inference_calls': 0}
        summary['input_sha256'] = {name: sha(survey / 'volumes' / volume / name)
                                  for name in ('groups.jsonl', 'occurrences.jsonl', 'summary.json')}
        if volume == 'PO015':
            summary['input_sha256']['catalog_entries'] = sha(Path(config['catalog']) / 'entries.json')
        dump(out / 'summary.json', summary)
        return summary
    finally:
        history.close()
        inventory.close()
        targets_log.close()


def aggregate(out, summaries, config):
    totals, history, states = Counter(), Counter(), Counter()
    tiers = defaultdict(Counter)
    with sqlite3.connect(out / 'audit.sqlite') as con:
        con.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE volumes(volume TEXT PRIMARY KEY,status TEXT,summary_json TEXT);
            CREATE TABLE evidence(id TEXT PRIMARY KEY,volume TEXT,tier TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for s in sorted(summaries, key=lambda s: s['volume']):
            totals.update(s.get('counts', {})); history.update(s.get('historical_versions', {}))
            states[s.get('coverage_state', 'error')] += 1
            for k, v in s.get('tiers', {}).items():
                tiers[k].update(v)
            con.execute('INSERT INTO volumes VALUES(?,?,?)', (s['volume'], s['status'], encoded(s)))
            if s['status'] != 'completed':
                continue
            with (out / 'volumes' / s['volume'] / 'evidence.jsonl').open() as f:
                for line in f:
                    r = json.loads(line)
                    con.execute('INSERT INTO evidence VALUES(?,?,?,?,?)', (r['id'], s['volume'], r['tier'], r['status'], line.strip()))
            con.commit()
        con.execute('CREATE INDEX evidence_volume_status ON evidence(volume,status)')
        summary = {'version': VERSION, 'volumes': len(summaries),
                   'completed': sum(s['status'] == 'completed' for s in summaries),
                   'worker_processes_used': len({s['pid'] for s in summaries if 'pid' in s}),
                   'coverage_states': dict(states), 'counts': dict(totals),
                   'tiers': {k: dict(v) for k, v in tiers.items()}, 'historical_version_checks': dict(history),
                   'verified_links': 0, 'production_updates': 0, 'inference_calls': 0,
                   'warning': 'Mixed editorial families, not certified names. Numeric namespaces remain unchanged. History checked only when current candidate text has no lexical match; counts are per occurrence, not printed entry.'}
        con.execute('INSERT INTO metadata VALUES(?,?)', ('config', encoded(config)))
        con.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        con.commit()
        assert con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    dump(out / 'summary.json', summary)
    import csv
    with (out / 'by_volume.csv').open('w', newline='') as f:
        fields = ['volume', 'status', 'coverage_state'] + sorted(totals)
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for s in sorted(summaries, key=lambda s: s['volume']):
            writer.writerow({**{k: s.get('counts', {}).get(k, 0) for k in sorted(totals)},
                             **{k: s.get(k, '') for k in fields[:3]}})
    (out / 'report.md').write_text('# Auditoria lexical global\n\n' + summary['warning'] +
        '\n\n```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n\n' +
        'Detalhes: by_volume.csv; volumes/*/evidence.jsonl; audit.sqlite (WAL).\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--survey', type=Path, default=Path('data/index_entry_parser/global_targets_20261106_002'))
    parser.add_argument('--catalog', type=Path, default=Path('data/index_catalog/po015_names_002'))
    parser.add_argument('--root', type=Path, default=Path('teste'))
    parser.add_argument('--versions-db', type=Path, default=Path('data/ocr_versions.db'))
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--volume', action='append')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    config = {k: str(v.resolve()) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config.update(version=VERSION, code_sha256=sha(Path(__file__)), lexical_code_sha256=sha(ROOT / 'scripts/audit_po015_names_lexical.py'))
    volumes = sorted(p.name for p in (args.survey / 'volumes').iterdir() if p.is_dir())
    if args.volume:
        if set(args.volume) - set(volumes):
            parser.error('Unknown volume')
        volumes = sorted(set(args.volume))
    args.out.mkdir(parents=True, exist_ok=False)
    dump(args.out / 'config.json', config)
    summaries = []
    with (args.out / 'execution.jsonl').open('w') as log:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {pool.submit(volume_worker, config, v): v for v in volumes}
            for future in as_completed(futures):
                volume = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'volume': volume, 'status': 'failed', 'error': repr(exc)}
                summaries.append(result)
                line = encoded(result)
                log.write(line + '\n'); log.flush()
                print(line, flush=True)
    print(json.dumps(aggregate(args.out, summaries, config), ensure_ascii=False, indent=2))
    return 0 if all(s['status'] == 'completed' for s in summaries) else 1


if __name__ == '__main__':
    raise SystemExit(main())
