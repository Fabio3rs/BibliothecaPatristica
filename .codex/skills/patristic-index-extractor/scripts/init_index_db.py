#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from index_db import DEFAULT_DB, connect_db, init_schema


def main() -> None:
    ap = argparse.ArgumentParser(description='Initialize the patristic index SQLite database.')
    ap.add_argument('--db', type=Path, default=DEFAULT_DB, help='Database path')
    args = ap.parse_args()

    with connect_db(args.db) as con:
        init_schema(con)
    print(f'[OK] initialized {args.db}')


if __name__ == '__main__':
    main()
