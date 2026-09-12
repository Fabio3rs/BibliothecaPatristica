#!/usr/bin/env python3
"""Build the deterministic scripture-citation SQLite index."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.scripture.citation_index import (
    DEFAULT_CITATION_DB,
    DEFAULT_PAYLOAD_DIR,
    MAX_SCAN_WORKERS,
    build_citation_database,
    citation_database_report,
    discover_volume_roots,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan PG/PL/PO OCR files and build a persistent deterministic "
            "scripture-citation evidence database."
        )
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--all",
        action="store_true",
        help="Process every PG/PL/PO volume under the corpus root.",
    )
    scope.add_argument(
        "--collection",
        action="append",
        choices=("PG", "PL", "PO"),
        help="Process one collection; repeat to select more than one.",
    )
    scope.add_argument(
        "--volume-id",
        action="append",
        dest="volume_ids",
        help="Process one volume; repeat to select more than one.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=PROJECT_ROOT / "teste",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_CITATION_DB)
    parser.add_argument(
        "--payload-dir",
        type=Path,
        default=DEFAULT_PAYLOAD_DIR,
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, max(1, (os.cpu_count() or 1) - 1)),
        help="Worker processes used for OCR parsing; use 1 for serial mode.",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--chunksize", type=int, default=4)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rescan files even when their metadata and detector fingerprints match.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Explicitly document resume intent. Resume is already the default "
            "because unchanged file fingerprints are skipped."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write a JSON report; defaults to <db>.report.json.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.workers > MAX_SCAN_WORKERS:
        raise SystemExit(f"--workers must be <= {MAX_SCAN_WORKERS}")
    if args.batch_size < 1 or args.chunksize < 1:
        raise SystemExit("--batch-size and --chunksize must be >= 1")
    volumes = discover_volume_roots(
        corpus_root=args.corpus_root,
        volume_ids=args.volume_ids,
        collections=args.collection,
    )
    if not volumes:
        raise SystemExit("no matching OCR volumes found")
    summary = build_citation_database(
        db_path=args.db,
        volumes=volumes,
        workers=args.workers,
        batch_size=args.batch_size,
        chunksize=args.chunksize,
        force=args.force,
        payload_dir=args.payload_dir,
    )
    report = {
        "build": summary,
        "inventory": citation_database_report(args.db),
    }
    report_path = args.report or args.db.with_suffix(args.db.suffix + ".report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                **summary,
                "report": str(report_path.resolve()),
            },
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
