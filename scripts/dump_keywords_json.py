#!/usr/bin/env python3
"""Dump SQLite table to JSON (streaming) - scripts/dump_keywords_json.py

Usage examples:
  python3 scripts/dump_keywords_json.py --db data/patristica_keywords.db --out dumptrecho.json
  python3 scripts/dump_keywords_json.py --db data/patristica_keywords.db --out dumptrecho.json --limit 100 --pretty
"""
import sqlite3
import json
import argparse
import sys
import os


def stream_dump(db_path, table='keywords', out_path='dump_keywords.json', limit=None, pretty=False, order_by='keyword_norm'):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    order_clause = f" ORDER BY {order_by}" if order_by else ''
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ''
    query = f"SELECT * FROM {table}{order_clause}{limit_clause}"

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('[')
        first = True
        for row in cur.execute(query):
            obj = dict(row)
            if not first:
                f.write(',\n')
            else:
                first = False
            if pretty:
                f.write(json.dumps(obj, ensure_ascii=False, indent=2))
            else:
                f.write(json.dumps(obj, ensure_ascii=False))
        f.write(']\n')

    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Dump an SQLite table to a JSON array (streaming).')
    parser.add_argument('--db', required=True, help='Path to sqlite DB file')
    parser.add_argument('--table', default='keywords', help='Table name to dump')
    parser.add_argument('--out', default='dump_keywords.json', help='Output JSON file path')
    parser.add_argument('--limit', type=int, default=None, help='Limit number of rows (optional)')
    parser.add_argument('--pretty', action='store_true', help='Pretty-print JSON objects (adds newlines/indents)')
    parser.add_argument('--order-by', default='keyword_norm', help='ORDER BY clause column (default: keyword_norm)')

    args = parser.parse_args()

    try:
        stream_dump(args.db, table=args.table, out_path=args.out, limit=args.limit, pretty=args.pretty, order_by=args.order_by)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(2)

    print(f'Wrote JSON to {args.out}')
