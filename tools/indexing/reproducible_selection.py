"""Freeze parser selection recipes, then rebuild from raw OCR without old databases.

The recipe contains file identities, profiles and neighbor identities, never parsed
entries. Historical output equality and repeatability of current code are separate.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import hashlib
import json
import multiprocessing
from pathlib import Path
import platform

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing import entry_parser_experiment as parser

VERSION = 'raw-index-replay-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value).hexdigest()


def source_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Source escapes corpus root: ' + relative)
    return path


def freeze(database, runs, root, out):
    root = root.resolve()
    rows = {}
    with closing(readonly(database)) as db:
        for run in runs:
            state = db.execute('SELECT status FROM runs WHERE run_id=?', (run,)).fetchone()
            if not state or state[0] != 'completed':
                raise ValueError('Parser run not completed: ' + run)
            for row in db.execute('SELECT * FROM run_pages WHERE run_id=? ORDER BY source', (run,)):
                meta = json.loads(row['metadata_json'])
                relative = str(Path(row['source']).resolve().relative_to(root))
                neighbors = [{'relation': c['relation'],
                              'source': str(Path(c['source']).resolve().relative_to(root)),
                              'expected_sha256': c['sha256']} for c in meta.get('context', [])]
                rows[relative] = {'source': relative, 'volume': row['volume'], 'physical': row['physical'],
                    'profile': row['profile'], 'expected_sha256': meta.get('source_sha256'),
                    'discovery_ownership_unverified': bool(meta.get('discovery_evidence')),
                    'context': neighbors, 'selected_from_run': run}
    manifest = {'version': VERSION, 'precedence': runs, 'selection_policy': 'later_run_replaces_page',
                'scope': 'parser_runs_only; structured overrides and later recoveries are separate recipes',
                'pages': [rows[k] for k in sorted(rows)]}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as f:
        f.write(encoded(manifest) + '\n')
    return {'selected_pages': len(rows), 'volumes': len({r['volume'] for r in rows.values()}), 'manifest': str(out)}


def rebuild_page(task):
    row, root, policy = task
    actual_source = row.get('source_alias', {}).get('source', row['source'])
    source = source_path(root, actual_source)
    evidence = {'source': row['source'], 'volume': row['volume'], 'physical': row['physical']}
    if row.get('source_alias'):
        evidence['source_alias'] = row['source_alias']
    try:
        data = source.read_bytes()
        current_hash = sha(data)
        evidence.update(source_sha256=current_hash, expected_sha256=row.get('expected_sha256'))
        if policy == 'exact' and (row.get('source_alias') or not row.get('expected_sha256')):
            return {**evidence, 'status': 'input_identity_unverified', 'page': None}
        changed = bool(row.get('expected_sha256') and row['expected_sha256'] != current_hash)
        evidence['source_changed'] = changed
        if changed and policy == 'exact':
            return {**evidence, 'status': 'input_changed', 'page': None}
        context = []
        for neighbor in row.get('context', []):
            path = source_path(root, neighbor['source'])
            neighbor_data = path.read_bytes()
            neighbor_hash = sha(neighbor_data)
            if policy == 'exact' and neighbor.get('expected_sha256') != neighbor_hash:
                return {**evidence, 'status': 'context_changed', 'context_source': neighbor['source'], 'page': None}
            text = neighbor_data.decode('utf-8')
            titles = [b['body'] for b in parser.BLOCK.finditer(text)
                      if dict(parser.ATTR.findall(b['attrs'])).get('tipo') == 'cabecalho']
            context.append({'relation': neighbor['relation'], 'source': str(path), 'sha256': neighbor_hash, 'titles': titles})
        evidence['context_sources'] = [{k: c[k] for k in ('relation','source','sha256')} for c in context]
        page = parser.parse_page(data.decode('utf-8'), source=str(source), physical=row['physical'],
                                 volume=row['volume'], profile=row['profile'], context=context)
        if row.get('discovery_ownership_unverified'):
            page['ownership'] = 'uncertain'
            for entry in page['entries']:
                entry['flags'].append('discovery_ownership_unverified')
        return {**evidence, 'status': 'parsed' if page['entries'] else 'no_candidates', 'page': page}
    except (OSError, UnicodeError, ValueError) as exc:
        return {**evidence, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}', 'page': None}


def selected_recipes(manifest, root, volumes=(), aliases_path=None):
    """Resolve the documented source selection without opening a campaign database."""
    spec = json.loads(manifest.read_text())
    if spec.get('version') != VERSION:
        raise ValueError('Unsupported recipe')
    pages = [r for r in spec['pages'] if not volumes or r['volume'] in volumes]
    pages.sort(key=lambda r: (r['volume'], r['physical'], r['source']))
    if len({r['source'] for r in pages}) != len(pages):
        raise ValueError('Duplicate source in recipe')
    if aliases_path:
        aliases = json.loads(aliases_path.read_text())['aliases']
        for row in pages:
            alias = aliases.get(row['source'])
            if alias:
                path = source_path(root, alias['source'])
                from tools.corpus_utils import page_number
                if not alias.get('evidence') or path.parent != (root/row['volume']/'text').resolve() or page_number(path) != row['physical']:
                    raise ValueError('Alias must have evidence and preserve volume and physical page')
                row['source_alias'] = alias
    return pages


def replay(manifest, root, out, workers=4, policy='current', volumes=(), emit_pages=True, aliases_path=None):
    pages = selected_recipes(manifest, root, volumes, aliases_path)
    if workers < 1 or not pages:
        raise ValueError('Positive workers and a nonempty selection are required')
    out.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    signatures = []
    dependencies = sorted(Path('tools/indexing').glob('*.py')) + [Path('tools/corpus_utils.py')]
    code_before = {str(p): sha(p.read_bytes()) for p in dependencies}
    output = (out/'pages.jsonl').open('w', encoding='utf-8') if emit_pages else None
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            for record in pool.map(rebuild_page, ((r, root.resolve(), policy) for r in pages), chunksize=8):
                counts[record['status']] += 1
                counts['source_changed'] += record.get('source_changed', False)
                counts['source_alias_applied'] += bool(record.get('source_alias'))
                if record['page']:
                    counts['entries'] += len(record['page']['entries'])
                digest = sha(encoded(record).encode())
                signatures.append({k: record[k] for k in ('source','status')} | {'sha256': digest})
                if output:
                    output.write(encoded(record) + '\n')
                if len(signatures) % 250 == 0:
                    print(encoded({'completed_pages': len(signatures), 'counts': dict(counts)}), flush=True)
    finally:
        if output:
            output.close()
    code_after = {str(p): sha(p.read_bytes()) for p in dependencies}
    status = 'completed' if all(r['status'] in {'parsed','no_candidates'} for r in signatures) and code_before == code_after else 'incomplete'
    result = {'version': VERSION, 'status': status, 'input_policy': policy,
              'manifest_sha256': sha(manifest.read_bytes()), 'corpus_root': str(root.resolve()),
              'aliases_sha256': sha(aliases_path.read_bytes()) if aliases_path else None,
              'pages': len(signatures), 'counts': dict(counts), 'workers': workers,
              'python': platform.python_version(), 'code_sha256': code_before,
              'code_changed_during_run': code_before != code_after,
              'logical_sha256': sha(encoded(signatures).encode()), 'signatures': signatures,
              'historical_equality': 'not_asserted', 'databases_required': []}
    (out/'verification.json').write_text(encoded(result)+'\n')
    return {k: v for k, v in result.items() if k not in ('signatures','code_sha256')}


def compare(left, right):
    a, b = (json.loads(p.read_text()) for p in (left, right))
    x, y = ({r['source']: r for r in v['signatures']} for v in (a, b))
    changed = sorted(k for k in x.keys() & y.keys() if x[k] != y[k])
    compatible = all(a.get(k) == b.get(k) for k in ('version','manifest_sha256','aliases_sha256','input_policy','corpus_root','code_sha256','python'))
    return {'status': 'identical' if compatible and x == y and a['status'] == b['status'] == 'completed' else 'not_verified',
            'same_recipe_and_code': compatible, 'identical_records': x == y,
            'left_status': a['status'], 'right_status': b['status'],
            'missing': sorted(x.keys()-y.keys()), 'added': sorted(y.keys()-x.keys()), 'changed': changed,
            'historical_equality': 'not_asserted'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('freeze-selection')
    f.add_argument('--db', type=Path, required=True)
    f.add_argument('--run', action='append', required=True)
    f.add_argument('--root', type=Path, default=Path('teste'))
    f.add_argument('--out', type=Path, required=True)
    r = sub.add_parser('replay')
    r.add_argument('--manifest', type=Path, required=True)
    r.add_argument('--root', type=Path, default=Path('teste'))
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--workers', type=int, default=4)
    r.add_argument('--input-policy', choices=['current','exact'], default='current')
    r.add_argument('--volume', action='append', default=[])
    r.add_argument('--digest-only', action='store_true')
    r.add_argument('--source-aliases', type=Path, help='Documented old-to-current file mappings; never guess by suffix')
    c = sub.add_parser('compare')
    c.add_argument('left', type=Path)
    c.add_argument('right', type=Path)
    args = p.parse_args()
    if args.command == 'freeze-selection':
        result = freeze(args.db, args.run, args.root, args.out)
    elif args.command == 'replay':
        result = replay(args.manifest, args.root, args.out, args.workers, args.input_policy, args.volume, not args.digest_only, args.source_aliases)
    else:
        result = compare(args.left, args.right)
    print(encoded(result))
    if result.get('status') in ('not_verified','incomplete'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
