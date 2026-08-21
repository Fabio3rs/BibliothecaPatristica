#!/usr/bin/env python3
"""Export the summary scripture PoC as compact positional JSON.

Format v2::

    {
      "v": 2,
      "parser": "2.x",
      "v11n": "unknown",
      "page": "physical",
      "source_mask": ["standalone", "embedded"],
      "volumes": ["PG001", ...],
      "books": [["genesis", "Gênesis"], ...],
      "references": [
        [bookIndex, granularityCode, flatSegments,
         [[volumeIndex, [pageDelta, sourceMask, ...]], ...]]
      ]
    }

``flatSegments`` stores four integers per segment: start chapter, start verse
(zero for none), end chapter, end verse (zero for none). Pages are physical
pages from ``resumos.pagina_num``. The first page delta of each volume is the
absolute page; subsequent values are positive deltas. Source-mask bit 0 means
standalone keyword and bit 1 means an embedded association.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from patristica_pipeline.scripture_book_catalog import (  # noqa: E402
    BOOKS,
    canonical_book_label,
)


DEFAULT_DB = Path("/tmp/summary_scripture_citations_v2_3.db")
DEFAULT_JSON = Path("/tmp/scripture-keywords-reference-pages.v2.json")
GRANULARITY_CODES = {"chapter": 1, "verse": 2, "range": 3, "list": 4}


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def build_payload(connection: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, int]]:
    parser_row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'parser_version'"
    ).fetchone()
    parser_version = str(parser_row[0]) if parser_row else "unknown"
    volumes = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT volume_id FROM pages ORDER BY volume_id"
        )
    ]
    volume_indexes = {value: index for index, value in enumerate(volumes)}

    present_books = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT book_key FROM scripture_references"
        )
    }
    ordered_keys = [book.key for book in BOOKS if book.key in present_books]
    ordered_keys.extend(sorted(present_books - set(ordered_keys)))
    books = [[key, canonical_book_label(key) or key] for key in ordered_keys]
    book_indexes = {value: index for index, value in enumerate(ordered_keys)}

    segments: dict[int, list[int]] = {}
    for row in connection.execute(
        """
        SELECT reference_id, start_chapter, start_verse, end_chapter, end_verse
          FROM scripture_reference_segments
         ORDER BY reference_id, seq
        """
    ):
        segments.setdefault(int(row["reference_id"]), []).extend(
            (
                int(row["start_chapter"]),
                int(row["start_verse"] or 0),
                int(row["end_chapter"]),
                int(row["end_verse"] or 0),
            )
        )

    postings: dict[int, dict[str, list[tuple[int, int]]]] = {}
    for row in connection.execute(
        """
        SELECT reference_id, volume_id, physical_page,
               has_standalone, has_embedded
          FROM reference_pages
         ORDER BY reference_id, volume_id, physical_page
        """
    ):
        mask = int(bool(row["has_standalone"])) | (
            int(bool(row["has_embedded"])) << 1
        )
        postings.setdefault(int(row["reference_id"]), {}).setdefault(
            str(row["volume_id"]), []
        ).append((int(row["physical_page"]), mask))

    references: list[list[Any]] = []
    largest_posting = 0
    for row in connection.execute(
        """
        SELECT id, book_key, granularity, canonical_key
          FROM scripture_references
         ORDER BY book_key, canonical_key
        """
    ):
        reference_id = int(row["id"])
        volume_rows: list[list[Any]] = []
        posting_count = 0
        for volume_id, locations in postings.get(reference_id, {}).items():
            previous_page = 0
            flat_locations: list[int] = []
            for page, mask in locations:
                flat_locations.extend((page - previous_page, mask))
                previous_page = page
                posting_count += 1
            volume_rows.append([volume_indexes[volume_id], flat_locations])
        volume_rows.sort(key=lambda item: item[0])
        largest_posting = max(largest_posting, posting_count)
        references.append(
            [
                book_indexes[str(row["book_key"])],
                GRANULARITY_CODES[str(row["granularity"])],
                segments[reference_id],
                volume_rows,
            ]
        )

    stats = {
        "volumes": len(volumes),
        "books": len(books),
        "references": len(references),
        "reference_page_pairs": sum(
            len(locations)
            for reference_postings in postings.values()
            for locations in reference_postings.values()
        ),
        "largest_reference_posting": largest_posting,
    }
    payload = {
        "v": 2,
        "parser": parser_version,
        "v11n": "unknown",
        "page": "physical",
        "source_mask": ["standalone", "embedded"],
        "volumes": volumes,
        "books": books,
        "references": references,
    }
    return payload, stats


def export_json(db_path: Path, output_path: Path) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(output_path)
    gzip_path = output_path.with_suffix(output_path.suffix + ".gz")
    if gzip_path.exists():
        raise FileExistsError(gzip_path)
    connection = connect_readonly(db_path)
    try:
        payload, stats = build_payload(connection)
    finally:
        connection.close()
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    output_path.write_bytes(raw)
    with gzip_path.open("wb") as compressed_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=compressed_file,
            mtime=0,
        ) as stream:
            stream.write(raw)
    gzip_bytes = gzip_path.stat().st_size
    stats.update(
        {
            "raw_bytes": len(raw),
            "gzip_bytes": gzip_bytes,
            "gzip_ratio": round(gzip_bytes / len(raw), 6) if raw else 0,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "json": str(output_path),
            "gzip": str(gzip_path),
        }
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stats = export_json(args.db, args.out)
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
