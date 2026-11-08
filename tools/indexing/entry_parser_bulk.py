"""Parallel, read-only-source survey of registered index pages; isolated SQLite staging."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
from pathlib import Path
import random
import sqlite3
import uuid

from tools.corpus_utils import page_files, page_number
from tools.indexing import entry_parser_experiment as parser

VERSION = 'index-parser-survey-v1'
ROOT = parser.ROOT


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    from tools.indexing.experiment_storage import register_json_storage
    register_json_storage(con)
    con.execute('PRAGMA query_only=ON')
    return con


def inventory(source_db, root, volumes=()):
    """Snapshot registered origins plus physical section ranges. Never use editorial numbers."""
    con = readonly(source_db)
    try:
        con.execute('BEGIN')
        sections = [dict(r) for r in con.execute('SELECT * FROM alphabetical_sections ORDER BY section_key')]
        rows = [dict(r) for r in con.execute('''SELECT s.volume_id,e.section_key,e.entry_key,e.entry_order,
            e.entry_raw,e.lemma_raw,e.context_raw,e.heading_letter,e.index_entry_source_ocr_file
            FROM alphabetical_entries e JOIN alphabetical_sections s USING(section_key)
            ORDER BY s.volume_id,e.section_key,e.entry_order,e.entry_key''')]
    finally:
        con.close()
    if volumes:
        sections = [s for s in sections if s['volume_id'] in volumes]
        rows = [r for r in rows if r['volume_id'] in volumes]
    tasks, issues = {}, []
    listings = {}

    def listing(volume):
        if volume not in listings:
            listings[volume] = page_files(root / volume / 'text', suffixes=['.txt'])
        return listings[volume]

    def add(volume, path, reason, section=None):
        path = path.resolve()
        key = (volume, str(path))
        task = tasks.setdefault(key, {'volume': volume, 'source': str(path),
            'physical': page_number(path), 'reasons': [], 'sections': [], 'old_rows': []})
        if reason not in task['reasons']:
            task['reasons'].append(reason)
        if section is not None and section not in task['sections']:
            task['sections'].append(section)
        return task

    def local_path(volume, value):
        # Preserve exact basename; do not substitute a different OCR with the same page number.
        return root / volume / 'text' / Path(value).name

    for row in rows:
        value = row['index_entry_source_ocr_file']
        if not value:
            issues.append({'kind': 'entry_missing_origin', 'entry_key': row['entry_key'], 'volume': row['volume_id']})
            continue
        add(row['volume_id'], local_path(row['volume_id'], value), 'entry_origin')['old_rows'].append(row)
    for s in sections:
        volume = s['volume_id']
        start = s.get('index_ocr_file_start') or s.get('file_start')
        end = s.get('index_ocr_file_end') or s.get('file_end')
        a, b = page_number(Path(start)) if start else None, page_number(Path(end)) if end else None
        if a is None or b is None or not a <= b or b - a > 2000:
            issues.append({'kind': 'unusable_physical_section_range', 'volume': volume, 'section_key': s['section_key']})
            continue
        for number in range(a, b + 1):
            candidates = listing(volume).get(number, [])
            if len(candidates) != 1:
                issues.append({'kind': 'missing_or_ambiguous_range_source', 'volume': volume,
                               'physical': number, 'section_key': s['section_key'],
                               'candidates': [str(p) for p in candidates]})
                continue
            add(volume, candidates[0], 'section_range', s)
    for task in tasks.values():
        task['profile'] = ('po-auto-v1' if task['volume'].startswith('PO') else 'pg069' if task['volume'] == 'PG069' and task['physical'] is not None
                           and 740 <= task['physical'] <= 744 else 'generic-literal-experimental')
        if task['profile'] == 'po-auto-v1' and task['physical'] is not None:
            task['context'] = []
            for offset, relation in [(-1, 'previous'), (1, 'next')]:
                neighbors = listing(task['volume']).get(task['physical'] + offset, [])
                if len(neighbors) != 1:
                    continue
                neighbor = neighbors[0]
                if neighbor.stem.rsplit('-', 1)[0] != Path(task['source']).stem.rsplit('-', 1)[0]:
                    continue
                try:
                    data = neighbor.read_bytes()
                    text = data.decode('utf-8')
                except (OSError, UnicodeError):
                    continue
                titles = [b['body'] for b in parser.BLOCK.finditer(text)
                          if dict(parser.ATTR.findall(b['attrs'])).get('tipo') == 'cabecalho']
                task['context'].append({'relation': relation, 'source': str(neighbor.resolve()),
                                        'sha256': parser.digest(data), 'titles': titles})
    return sorted(tasks.values(), key=lambda t: (t['volume'], t['physical'] or -1, t['source'])), issues


def work(task):
    try:
        raw = task['raw']
        page = parser.parse_page(raw, source=task['source'], physical=task['physical'],
                                 volume=task['volume'], profile=task['profile'], context=task.get('context'))
        if task.get('discovery_evidence'):
            page['ownership'] = 'uncertain'
            for entry in page['entries']:
                entry['flags'].append('discovery_ownership_unverified')
        diffs = parser.compare_rows(page, task['old_rows'])
        return {'status': 'parsed' if page['entries'] else 'no_candidates', 'page': page,
                'diffs': diffs, 'old_rows': task['old_rows'], 'error': None}
    except Exception as exc:
        return {'status': 'error', 'error': f'{type(exc).__name__}: {exc}', 'page': None,
                'diffs': [], 'old_rows': task['old_rows']}


def discovery_inventory(manifest, root, registered, volumes=()):
    """Feed bounded discovery evidence to the existing parser, never certify ownership."""
    known = {(t['volume'], str(Path(t['source']).resolve())): t for t in registered}
    tasks = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        volume = item['volume']
        if volumes and volume not in volumes:
            continue
        # Keep exact source identity. Never choose an arbitrary file by suffix.
        source = Path(item['source']).resolve()
        folder = (root/volume/'text').resolve()
        if folder.parent.parent != root.resolve() or source.parent != folder:
            raise ValueError('Discovery source must belong to its declared corpus volume')
        if page_number(source) != item['physical']:
            raise ValueError('Discovery physical identity differs from source filename')
        data = source.read_bytes()
        if parser.digest(data) != item['sha256']:
            raise ValueError(f'Stale discovery source: {source}')
        raw = data.decode('utf-8')
        signals = item.get('signals', [])
        if not any(s.get('kind') == 'index_heading_hint' for s in signals):
            raise ValueError('Discovery item lacks an index heading hint')
        for signal in signals:
            a, b = signal['span']
            if not 0 <= a < b <= len(raw) or raw[a:b] != signal['quote']:
                raise ValueError('Discovery evidence span differs from OCR')
        key = (volume,str(source))
        if key in tasks:
            raise ValueError('Duplicate discovery source')
        task = dict(known.get(key) or {'volume':volume, 'source':str(source),
            'physical':item['physical'], 'reasons':[], 'sections':[], 'old_rows':[],
            'profile':'po-auto-v1' if volume.startswith('PO') else 'generic-literal-experimental'})
        task['reasons'] = [*task['reasons'], 'bounded_discovery_hint']
        task['discovery_evidence'] = signals
        task['expected_source_sha256'] = item['sha256']
        tasks[key] = task
    return sorted(tasks.values(),key=lambda t:(t['volume'],t['physical'],t['source']))


def open_experiment(path, source_db):
    path = path.resolve()
    source_db = source_db.resolve()
    if path == source_db or (path.exists() and source_db.exists() and path.samefile(source_db)):
        raise ValueError('Experiment DB must differ from source DB')
    if path.exists():
        old = readonly(path)
        try:
            tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            marker = old.execute("SELECT value FROM experiment_meta WHERE key='kind'").fetchone() if 'experiment_meta' in tables else None
            if tables and (not marker or marker[0] != VERSION):
                raise ValueError('Refusing to use an unrelated SQLite database')
        finally:
            old.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS experiment_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,created_at TEXT,config_json TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS tasks(fingerprint TEXT PRIMARY KEY,status TEXT,result_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS run_pages(run_id TEXT,source TEXT,volume TEXT,physical INTEGER,
            fingerprint TEXT,profile TEXT,metadata_json TEXT,PRIMARY KEY(run_id,source),
            FOREIGN KEY(run_id) REFERENCES runs(run_id),FOREIGN KEY(fingerprint) REFERENCES tasks(fingerprint));
        CREATE TABLE IF NOT EXISTS candidates(fingerprint TEXT,candidate_id TEXT,status TEXT,entry_json TEXT,diff_json TEXT,
            PRIMARY KEY(fingerprint,candidate_id),FOREIGN KEY(fingerprint) REFERENCES tasks(fingerprint));
        CREATE TABLE IF NOT EXISTS reviews(run_id TEXT,item_key TEXT,verdict TEXT,notes TEXT,reviewer TEXT,reviewed_at TEXT,
            PRIMARY KEY(run_id,item_key));
        CREATE INDEX IF NOT EXISTS candidates_status ON candidates(status);
    ''')
    con.execute("INSERT OR IGNORE INTO experiment_meta VALUES('kind',?)", (VERSION,))
    con.commit()
    return con


def save(con, run_id, task, result, cached=False):
    key = task['fingerprint']
    with con:
        if not cached:
            con.execute('''INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(fingerprint)
                           DO UPDATE SET status=excluded.status,result_json=excluded.result_json''',
                        (key, result['status'], encoded(result)))
            con.execute('DELETE FROM candidates WHERE fingerprint=?', (key,))
            diffs = {d['candidate_id']: d for d in result['diffs']}
            for entry in (result['page'] or {}).get('entries', []):
                diff = diffs[entry['id']]
                con.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',
                            (key, entry['id'], diff['status'], encoded(entry), encoded(diff)))
        metadata = {k: v for k, v in task.items() if k not in {'raw', 'old_rows'}}
        con.execute('INSERT INTO run_pages VALUES(?,?,?,?,?,?,?)',
                    (run_id, task['source'], task['volume'], task['physical'], key, task['profile'], encoded(metadata)))


def sample_run(con, run_id, out, seed=42, per_stratum=2):
    """Sample candidates AND residuals/empty pages, to expose missed extraction too."""
    groups = defaultdict(list)
    for row in con.execute('''SELECT p.*,t.result_json,t.status AS task_status FROM run_pages p
                             JOIN tasks t USING(fingerprint) WHERE p.run_id=? ORDER BY volume,physical,source''', (run_id,)):
        result = json.loads(row['result_json'])
        page = result['page']
        common = {'volume': row['volume'], 'physical': row['physical'], 'source': row['source'],
                  'fingerprint': row['fingerprint'], 'profile': row['profile']}
        image = Path(row['source']).parent.parent / 'images' / (Path(row['source']).stem + '.png')
        common['image'] = str(image) if image.is_file() else None
        diff_by_id = {d['candidate_id']: d for d in result['diffs']}
        if page:
            for entry in page['entries']:
                diff = diff_by_id[entry['id']]
                item = dict(common, kind='candidate', item_key=entry['id'], entry=entry, comparison=diff)
                groups[(row['volume'], diff['status'])].append(item)
            for i, residual in enumerate(page['residuals']):
                item = dict(common, kind='residual', item_key=f"{row['fingerprint']}:residual:{i}", residual=residual)
                groups[(row['volume'], 'residual')].append(item)
        if not page or not page['entries']:
            groups[(row['volume'], 'page_without_candidates')].append(dict(common, kind='page',
                item_key=row['fingerprint'], error=result['error'], old_rows=result['old_rows']))
    selected = []
    for stratum, items in sorted(groups.items()):
        items.sort(key=lambda i: i['item_key'])
        rng = random.Random(f'{seed}:{stratum}')
        for item in rng.sample(items, min(per_stratum, len(items))):
            item.update(stratum=list(stratum), population=len(items), verdict=None, review_notes=None)
            selected.append(item)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'sample.json').write_text(encoded({'run_id': run_id, 'seed': seed, 'per_stratum': per_stratum,
        'sampling': 'uniform_without_replacement_within_volume_and_status', 'items': selected}) + '\n')
    lines = ['# Amostra para comparação manual', '',
             'Amostra estratificada por volume e situação; não estima precisão global sem ponderação.',
             'Sem gold automático. Conferir OCR/fac-símile e preencher verdict/review_notes em sample.json.', '']
    for item in selected:
        lines += [f"## {item['volume']} / {item['physical']} — {item['stratum'][1]}", '',
                  f"Perfil: `{item['profile']}`; item: `{item['item_key']}`.", '',
                  f"Fonte: `{item['source']}`", '', f"Fac-símile: `{item['image']}`", '']
        if item['kind'] == 'candidate':
            entry = item['entry']
            lines += [f"Linhas OCR: {entry['line_start']}–{entry['line_end']}", '', '```text', entry['entry_raw'], '```', '',
                      f"Sinais: {', '.join(entry['flags']) or 'nenhum'}", '', 'Registros anteriores alinhados:', '']
            lines += [f"- `{r['entry_key']}`: {parser.display(r['entry_raw'])}" for r in item['comparison']['old_entries']]
        elif item['kind'] == 'residual':
            lines += ['```text', item['residual']['raw'], '```', '']
        else:
            lines += [f"Erro: {item['error']}; registros anteriores: {len(item['old_rows'])}", '']
    (out / 'sample.md').write_text('\n'.join(lines) + '\n')
    return len(selected)


def summarize(con, run_id):
    totals, by_volume, flags = Counter(), defaultdict(Counter), Counter()
    for row in con.execute('''SELECT p.volume,p.profile,t.status,t.result_json FROM run_pages p
                             JOIN tasks t USING(fingerprint) WHERE run_id=?''', (run_id,)):
        r = json.loads(row['result_json'])
        counts = Counter(pages=1)
        counts[row['status']] += 1
        counts['profile_unvalidated_pages'] += row['profile'] != 'pg069'
        if r['page']:
            counts['candidates'] += len(r['page']['entries'])
            counts['residuals'] += len(r['page']['residuals'])
            counts.update(d['status'] for d in r['diffs'])
            flags.update(f for e in r['page']['entries'] for f in e['flags'])
            counts.update('format:' + f for f in r['page'].get('format_profiles', []))
        totals.update(counts)
        by_volume[row['volume']].update(counts)
    return {'run_id': run_id, 'totals': dict(totals), 'by_volume': dict(by_volume),
            'flags': dict(flags), 'source_db_updates': 0, 'inference_calls': 0, 'import_ready': False}


def run(args):
    # Protect source DB before creating output directories or opening SQLite for writes.
    if args.out.resolve() in {args.source_db.resolve(), args.experiment_db.resolve()}:
        raise ValueError('Output directory cannot be a database path')
    if args.sample_run:
        con = readonly(args.experiment_db)
        try:
            if not con.execute('SELECT 1 FROM runs WHERE run_id=?', (args.sample_run,)).fetchone():
                raise ValueError('Unknown run ID')
            if args.out.exists():
                raise ValueError('Choose a new sample output directory')
            print(encoded({'sample_items': sample_run(con, args.sample_run, args.out, args.seed, args.sample_per_stratum)}))
        finally:
            con.close()
        return
    con = open_experiment(args.experiment_db, args.source_db)
    try:
        tasks, issues = inventory(args.source_db, args.root.resolve(), args.volume)
        discovery_manifest = getattr(args, 'discovery_manifest', None)
        if discovery_manifest:
            tasks = discovery_inventory(discovery_manifest, args.root.resolve(), tasks, args.volume)
        if args.limit:
            tasks = tasks[:args.limit]
        args.out.mkdir(parents=True, exist_ok=False)
        code_hash = fingerprint({p: parser.digest((ROOT / p).read_bytes()) for p in [
            'tools/indexing/entry_parser_bulk.py', 'tools/indexing/entry_parser_experiment.py',
            'tools/indexing/entry_parser_po.py',
            'scripts/scan_editorial_ocr.py', 'tools/ocr_xml_utils.py', 'tools/corpus_utils.py']})
        run_id = uuid.uuid4().hex
        config = {'version': VERSION, 'code_hash': code_hash, 'source_db': str(args.source_db.resolve()),
                  'volumes': args.volume, 'workers': args.workers, 'seed': args.seed,
                  'limit': args.limit, 'inventory_sha256': fingerprint(tasks), 'inventory_issues': issues}
        if discovery_manifest:
            config.update(discovery_manifest=str(discovery_manifest.resolve()),
                          discovery_manifest_sha256=parser.digest(discovery_manifest.read_bytes()),
                          ownership='uncertain')
        con.execute('INSERT INTO runs VALUES(?,?,?,?)', (run_id, datetime.now(timezone.utc).isoformat(), encoded(config), 'running'))
        con.commit()
        (args.out / 'config.json').write_text(encoded(dict(config, run_id=run_id)) + '\n')
        completed = cache_hits = 0
        task_iter = iter(tasks)
        pending = {}
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            exhausted = False
            while pending or not exhausted:
                while not exhausted and len(pending) < args.workers * 2:
                    task = next(task_iter, None)
                    if task is None:
                        exhausted = True
                        break
                    try:
                        data = Path(task['source']).read_bytes()
                        task['source_sha256'] = parser.digest(data)
                        if task.get('expected_source_sha256') and task['source_sha256'] != task['expected_source_sha256']:
                            raise ValueError('Discovery source changed after inventory preparation')
                        task['raw'] = data.decode('utf-8')
                        error = None
                    except (OSError, UnicodeError, ValueError) as exc:
                        task['source_sha256'] = None
                        error = f'{type(exc).__name__}: {exc}'
                    task['fingerprint'] = fingerprint({'code': code_hash,
                        'task': {k: v for k, v in task.items() if k != 'raw'}})
                    cached = con.execute('SELECT result_json FROM tasks WHERE fingerprint=? AND status != ?',
                                         (task['fingerprint'], 'error')).fetchone() if args.resume else None
                    if cached:
                        save(con, run_id, task, json.loads(cached[0]), cached=True)
                        completed += 1
                        cache_hits += 1
                    elif error:
                        save(con, run_id, task, {'status': 'error', 'error': error, 'page': None,
                                                'diffs': [], 'old_rows': task['old_rows']})
                        completed += 1
                    else:
                        pending[pool.submit(work, task)] = task
                if pending:
                    done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = pending.pop(future)
                        save(con, run_id, task, future.result())
                        completed += 1
                    if done and args.verbose:
                        print(encoded({'completed_pages': completed, 'total_pages': len(tasks),
                                       'in_flight': len(pending), 'cache_hits': cache_hits}), flush=True)
        summary = summarize(con, run_id)
        summary.update(cache_hits=cache_hits, inventory_issues=len(issues),
                       sampled_items=sample_run(con, run_id, args.out, args.seed, args.sample_per_stratum))
        with con:
            con.execute('UPDATE runs SET status=? WHERE run_id=?', ('completed', run_id))
        (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-db', type=Path, default=ROOT / 'data/alphabetical_indices.db')
    p.add_argument('--experiment-db', type=Path, default=ROOT / 'data/index_entry_parser/experiments.sqlite')
    p.add_argument('--root', type=Path, default=ROOT / 'teste')
    p.add_argument('--volume', action='append', default=[])
    p.add_argument('--all-volumes', action='store_true', help='All registered origins and physical section ranges')
    p.add_argument('--discovery-manifest', type=Path,
                   help='Parse only hash-checked discovery_candidates.jsonl sources; ownership stays uncertain')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--limit', type=int, default=0, help='Max physical sources; 0 means all selected')
    p.add_argument('--resume', action='store_true', help='Reuse compatible successful task snapshots')
    p.add_argument('--sample-run', help='Generate another review sample from an existing run without parsing')
    p.add_argument('--sample-per-stratum', type=int, default=2)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()
    if args.workers < 1 or args.limit < 0 or args.sample_per_stratum < 1:
        p.error('workers/sample-per-stratum must be positive; limit must be nonnegative')
    if not args.sample_run and not args.discovery_manifest and bool(args.volume) == bool(args.all_volumes):
        p.error('Choose --volume (repeatable) or --all-volumes')
    if args.volume and args.all_volumes:
        p.error('Choose --volume or --all-volumes')
    run(args)
