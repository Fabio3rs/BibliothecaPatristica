#!/usr/bin/env python3
"""Audit campaign dependencies and compare logical rows, never SQLite file bytes."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def database_digest(path):
    result = {}
    with closing(readonly(path)) as db:
        for table, order in [('items', 'id'), ('candidates', 'item_id,file')]:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                continue
            digest = hashlib.sha256()
            count = 0
            for row in db.execute(f'SELECT * FROM {table} ORDER BY {order}'):
                value = dict(row)
                for k in value:
                    if k.endswith('_json') and value[k] is not None:
                        value[k] = json.loads(value[k])
                digest.update((encode(value)+'\n').encode())
                count += 1
            result[table] = {'rows': count, 'sha256': digest.hexdigest()}
    return result


def audit(manifest, out):
    spec = json.loads(manifest.read_text())
    rows = []
    for campaign in spec['campaigns']:
        checks = []
        for dep in campaign['inputs']:
            path = Path(dep['path'])
            checks.append({**dep, 'exists': path.exists()})
        row = {**campaign, 'input_checks': checks}
        artifact = Path(campaign['artifact'])
        row['artifact_exists'] = artifact.is_file()
        row['status'] = 'recipe_available_not_verified' if campaign['from_raw'] else 'derived_dependencies_not_eliminated'
        if any(not d['exists'] for d in checks):
            row['status'] = 'missing_input'
        if artifact.is_file():
            with closing(readonly(artifact)) as db:
                row['counts'] = {}
                for table in ['items','candidates']:
                    if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                        row['counts'][table] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        row['raw_rebuild_verified'] = False
        rows.append(row)
    result = {'version': 'index-regeneration-audit-v1', 'campaigns': rows,
              'all_raw_rebuilds_verified': False,
              'criterion': 'Existing files and commands are not proof; require fresh raw-input runs and logical-output comparison.'}
    out.mkdir(parents=True, exist_ok=False)
    (out/'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    lines = ['# Regeneração das campanhas de índices', '',
             'Disponibilidade de receitas e dependências; não certifica reprodução integral.', '',
             '| Campanha | Registros | Pares candidatos | Estado |', '|---|---:|---:|---|']
    for row in rows:
        counts = row.get('counts', {})
        lines.append(f"| {row['id']} | {counts.get('items', '—')} | {counts.get('candidates', '—')} | {row['status']} |")
        missing = [d['path'] for d in row['input_checks'] if not d['exists']]
        if missing:
            lines.append('\nInsumos ausentes de '+row['id']+': '+', '.join(missing)+'\n')
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return {'campaigns': len(rows), 'missing_inputs': sum(not d['exists'] for r in rows for d in r['input_checks']),
            'all_raw_rebuilds_verified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('audit')
    a.add_argument('--manifest', type=Path, required=True)
    a.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('compare')
    c.add_argument('left', type=Path)
    c.add_argument('right', type=Path)
    c.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'audit':
        result = audit(args.manifest, args.out)
    else:
        left, right = database_digest(args.left), database_digest(args.right)
        result = {'status': 'identical' if left == right and left else 'different', 'left': left, 'right': right,
                  'comparison': 'all items and candidates columns, JSON object keys canonicalized; excludes metadata tables, database layout and FTS rowids'}
        with args.out.open('x') as f:
            f.write(json.dumps(result, indent=2)+'\n')
    print(encode(result))
    if result.get('status') == 'different':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
