#!/usr/bin/env python3
"""Snapshot eligible pending catalog occurrences, then build readonly page maps.

Writes a fresh, replayable overlay only. Merge with consolidate_index_search.py
after review. --include-linked audits existing links without replacing them.
"""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.reviewed_index_targets import encoded, route, run, write_json


def select(catalog, destination, volumes, include_linked=False):
    counts = Counter()
    with closing(readonly(catalog)) as src, closing(sqlite3.connect(destination)) as out:
        out.execute('PRAGMA journal_mode=WAL')
        out.execute(src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='items'").fetchone()[0])
        query = 'SELECT * FROM items WHERE volume IN (' + ','.join('?' for _ in volumes) + ')'
        if not include_linked:
            query += " AND NOT EXISTS (SELECT 1 FROM candidate_storage c WHERE c.item_id=items.id AND c.navigable=1)"
        query += ' ORDER BY id'
        for row in src.execute(query, volumes):
            if route(json.loads(row['metadata_json'])) not in {'pl003_tertullian', 'pl098_cenni', 'pg058_bracket'}:
                counts['outside_reviewed_sections'] += 1
                continue
            out.execute('INSERT INTO items VALUES(' + ','.join('?' for _ in row) + ')', tuple(row))
            counts[row['volume']] += 1
        out.commit()
        out.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--volume', action='append', choices=['PL003', 'PL098', 'PG058'])
    parser.add_argument('--include-linked', action='store_true')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    args.out.mkdir(parents=True, exist_ok=False)
    selected = args.out/'selected.sqlite'
    counts = select(args.catalog, selected, args.volume or ['PL003', 'PL098', 'PG058'], args.include_linked)
    write_json(args.out/'selection.json', dict(catalog=str(args.catalog.resolve()), counts=counts,
        include_linked=args.include_linked, canonical_updates=0))
    run(selected, args.catalog, args.root.resolve(), args.out/'targets', args.workers)


if __name__ == '__main__':
    main()
