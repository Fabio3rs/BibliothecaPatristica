#!/usr/bin/env python3
"""Export one self-contained summary-scripture shard per biblical book."""

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

from tools.scripture.book_catalog import (  # noqa: E402
    BOOKS,
    aliases_for_book,
    canonical_book_label,
)
from scripts.scripture_keywords.export_summary_scripture_json_v2 import (  # noqa: E402
    GRANULARITY_CODES,
    connect_readonly,
)


DEFAULT_DB = Path("/tmp/summary_scripture_citations_v2_3.db")
DEFAULT_OUTPUT = Path("/tmp/scripture-keywords-shards-v2-book")
SIZE_THRESHOLDS = (32 * 1024, 64 * 1024, 128 * 1024)


def _book_order() -> dict[str, int]:
    return {book.key: index for index, book in enumerate(BOOKS)}


def _book_payload(
    connection: sqlite3.Connection,
    *,
    parser_version: str,
    book_key: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    reference_rows = connection.execute(
        """
        SELECT id, granularity, canonical_key
          FROM scripture_references
         WHERE book_key = ?
         ORDER BY canonical_key
        """,
        (book_key,),
    ).fetchall()
    reference_ids = [int(row["id"]) for row in reference_rows]
    if not reference_ids:
        raise ValueError(f"Book has no references: {book_key}")

    placeholders = ",".join("?" for _ in reference_ids)
    segments: dict[int, list[int]] = {}
    for row in connection.execute(
        f"""
        SELECT reference_id, start_chapter, start_verse, end_chapter, end_verse
          FROM scripture_reference_segments
         WHERE reference_id IN ({placeholders})
         ORDER BY reference_id, seq
        """,
        reference_ids,
    ):
        segments.setdefault(int(row["reference_id"]), []).extend(
            (
                int(row["start_chapter"]),
                int(row["start_verse"] or 0),
                int(row["end_chapter"]),
                int(row["end_verse"] or 0),
            )
        )

    posting_rows = connection.execute(
        f"""
        SELECT reference_id, volume_id, physical_page,
               has_standalone, has_embedded
          FROM reference_pages
         WHERE reference_id IN ({placeholders})
         ORDER BY reference_id, volume_id, physical_page
        """,
        reference_ids,
    ).fetchall()
    volumes = sorted({str(row["volume_id"]) for row in posting_rows})
    volume_indexes = {volume: index for index, volume in enumerate(volumes)}
    postings: dict[int, dict[str, list[tuple[int, int]]]] = {}
    for row in posting_rows:
        mask = int(bool(row["has_standalone"])) | (
            int(bool(row["has_embedded"])) << 1
        )
        postings.setdefault(int(row["reference_id"]), {}).setdefault(
            str(row["volume_id"]), []
        ).append((int(row["physical_page"]), mask))

    references: list[list[Any]] = []
    largest_posting = 0
    for row in reference_rows:
        reference_id = int(row["id"])
        locations_by_volume: list[list[Any]] = []
        posting_count = 0
        for volume_id, locations in postings.get(reference_id, {}).items():
            previous_page = 0
            flat_locations: list[int] = []
            for page, mask in locations:
                flat_locations.extend((page - previous_page, mask))
                previous_page = page
                posting_count += 1
            locations_by_volume.append(
                [volume_indexes[volume_id], flat_locations]
            )
        locations_by_volume.sort(key=lambda item: item[0])
        largest_posting = max(largest_posting, posting_count)
        references.append(
            [
                GRANULARITY_CODES[str(row["granularity"])],
                segments[reference_id],
                locations_by_volume,
            ]
        )

    payload = {
        "v": 2,
        "parser": parser_version,
        "v11n": "unknown",
        "page": "physical",
        "source_mask": ["standalone", "embedded"],
        "book": [book_key, canonical_book_label(book_key) or book_key],
        "volumes": volumes,
        "references": references,
    }
    stats = {
        "references": len(references),
        "reference_page_pairs": len(posting_rows),
        "volumes": len(volumes),
        "largest_reference_posting": largest_posting,
    }
    return payload, stats


def export_book_shards(
    db_path: Path,
    output_dir: Path,
    *,
    include_debug_json: bool = False,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True)
    connection = connect_readonly(db_path)
    try:
        parser_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'parser_version'"
        ).fetchone()
        parser_version = str(parser_row[0]) if parser_row else "unknown"
        present_books = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT book_key FROM scripture_references"
            )
        ]
        order = _book_order()
        present_books.sort(key=lambda key: (order.get(key, 999), key))

        routes: dict[str, dict[str, Any]] = {}
        total_raw = 0
        total_gzip = 0
        for book_key in present_books:
            payload, stats = _book_payload(
                connection,
                parser_version=parser_version,
                book_key=book_key,
            )
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            compressed = gzip.compress(raw, compresslevel=9, mtime=0)
            raw_sha256 = hashlib.sha256(raw).hexdigest()
            ordinal = order.get(book_key, 999)
            slug = book_key.replace(" ", "-")
            filename = f"{ordinal:03d}-{slug}.{raw_sha256[:12]}.json.gz"
            debug_filename = f"{ordinal:03d}-{slug}.{raw_sha256[:12]}.json"
            (output_dir / filename).write_bytes(compressed)
            if include_debug_json:
                (output_dir / debug_filename).write_bytes(raw)
            total_raw += len(raw)
            total_gzip += len(compressed)
            route = {
                "book_id": ordinal,
                "label": canonical_book_label(book_key) or book_key,
                "aliases": sorted(aliases_for_book(book_key)),
                "url": filename,
                "raw_bytes": len(raw),
                "gzip_bytes": len(compressed),
                "sha256": raw_sha256,
                **stats,
            }
            if include_debug_json:
                route["debug_url"] = debug_filename
            routes[book_key] = route
    finally:
        connection.close()

    ordered_routes = dict(
        sorted(routes.items(), key=lambda item: (item[1]["book_id"], item[0]))
    )
    threshold_counts = {
        str(threshold): sum(
            route["gzip_bytes"] > threshold for route in ordered_routes.values()
        )
        for threshold in SIZE_THRESHOLDS
    }
    largest = sorted(
        ordered_routes.items(),
        key=lambda item: item[1]["gzip_bytes"],
        reverse=True,
    )[:10]
    manifest = {
        "schema": "bibliotheca-scripture-book-shards-v2",
        "v": 2,
        "strategy": "one-shard-per-book",
        "parser": parser_version,
        "v11n": "unknown",
        "page": "physical",
        "source": {
            "table": "resumos",
            "scope": "legacy-v1-published-keywords",
        },
        "routes": ordered_routes,
        "stats": {
            "shards": len(ordered_routes),
            "raw_bytes": total_raw,
            "gzip_bytes": total_gzip,
            "books_over_gzip_threshold": threshold_counts,
            "largest": [
                {
                    "book_key": book_key,
                    "gzip_bytes": route["gzip_bytes"],
                    "references": route["references"],
                    "reference_page_pairs": route["reference_page_pairs"],
                }
                for book_key, route in largest
            ],
        },
    }
    manifest_raw = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    (output_dir / "manifest.json").write_bytes(manifest_raw)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--debug-json",
        action="store_true",
        help="Write uncompressed JSON copies next to the public gzip shards.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = export_book_shards(
        args.db,
        args.out,
        include_debug_json=args.debug_json,
    )
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
