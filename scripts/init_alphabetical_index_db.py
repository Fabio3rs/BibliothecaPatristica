#!/usr/bin/env python3
from __future__ import annotations

import argparse

from alphabetical_index_db import DEFAULT_DB, connect_db, init_schema


def main() -> None:
    ap = argparse.ArgumentParser(description="Initialize the alphabetical_indices SQLite database.")
    ap.add_argument("--db", default=DEFAULT_DB, help="Database path")
    args = ap.parse_args()

    with connect_db(args.db) as con:
        init_schema(con)
    print(f"[OK] initialized {args.db}")


if __name__ == "__main__":
    main()
