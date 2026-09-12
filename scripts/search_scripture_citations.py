#!/usr/bin/env python3
"""Search the deterministic scripture-citation SQLite index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.scripture.citation_index import (
    DEFAULT_CITATION_DB,
    search_citations,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search biblical OCR occurrences by editorial or physical page. "
            "Physical page means the numeric -NNN.txt filename suffix."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_CITATION_DB)
    parser.add_argument("--volume-id", required=True)
    locator = parser.add_mutually_exclusive_group(required=True)
    locator.add_argument("--editorial-page", type=int)
    locator.add_argument("--physical-page", type=int)
    parser.add_argument("--fascicle")
    parser.add_argument("--book")
    parser.add_argument("--ref", dest="reference")
    parser.add_argument("--text")
    parser.add_argument(
        "--context",
        choices=(
            "critical_apparatus",
            "note",
            "body",
            "index",
            "header",
            "footer",
        ),
    )
    parser.add_argument("--include-index", action="store_true")
    parser.add_argument("--include-ambiguous", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    return parser


def _format_row(row: dict[str, object]) -> str:
    editorial = row.get("editorial_page")
    editorial_label = f"editorial={editorial}" if editorial is not None else "editorial=?"
    return (
        f"{row.get('volume_id')} physical={row.get('physical_file_seq')} "
        f"{editorial_label} {row.get('book_key') or row.get('book_raw')} "
        f"{row.get('ref_norm') or row.get('ref_raw')} "
        f"[{row.get('context_kind')}] {row.get('file_path')}\n"
        f"  {row.get('snippet_raw')}"
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    rows = search_citations(
        db_path=args.db,
        volume_id=args.volume_id,
        editorial_page=args.editorial_page,
        physical_page=args.physical_page,
        fascicle=args.fascicle,
        book=args.book,
        reference=args.reference,
        text_query=args.text,
        context=args.context,
        include_index=args.include_index,
        include_ambiguous=args.include_ambiguous,
        limit=args.limit,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "volume_id": args.volume_id,
                    "count": len(rows),
                    "results": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for row in rows:
            print(_format_row(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
