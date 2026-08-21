#!/usr/bin/env python3
"""Audit keyword scripture parsing against the Vulgata Clementina."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scripture_keywords.vulgate_clementine import (  # noqa: E402
    DEFAULT_VULGATE_JSON,
    VulgateClementine,
    enrich_keyword,
)


CitationParser = Callable[[str], Sequence[Mapping[str, Any]]]


def load_default_citation_parser() -> CitationParser:
    from scripture_ref_normalizer import extract_citations_from_value_cached

    def parse(keyword: str) -> Sequence[Mapping[str, Any]]:
        return extract_citations_from_value_cached(
            keyword,
            source_kind="keywords",
            source_path="scripture_embedding_audit",
            support_mode=False,
        )

    return parse


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Keyword DB not found: {path}")
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info('{table}')"))


def _citation_record_for_report(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "raw",
            "normalized",
            "book_canonical",
            "number",
            "verse",
            "alt_number",
            "kind",
        )
    }


def audit_keywords(
    con: sqlite3.Connection,
    bible: VulgateClementine,
    *,
    citation_parser: CitationParser | None = None,
    only_flagged: bool = False,
    limit: int | None = None,
    output: TextIO | None = None,
) -> dict[str, Any]:
    parser = citation_parser or load_default_citation_parser()
    has_flag = _has_column(con, "keywords", "is_scripture_citation")
    if only_flagged and not has_flag:
        raise ValueError("keywords.is_scripture_citation is required by --only-flagged")

    flag_expr = "COALESCE(is_scripture_citation, 0)" if has_flag else "0"
    sql = (
        f"SELECT id, keyword_original, {flag_expr} AS is_scripture_citation "
        "FROM keywords"
    )
    params: list[object] = []
    if only_flagged:
        sql += " WHERE COALESCE(is_scripture_citation, 0) = 1"
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    status_counts: Counter[str] = Counter()
    lookup_status_counts: Counter[str] = Counter()
    checked = 0
    flagged = 0
    parser_positive = 0
    resolved = 0
    failure_rows = 0
    flag_mismatches = 0

    for row in con.execute(sql, params):
        checked += 1
        keyword = row["keyword_original"] or ""
        is_flagged = bool(row["is_scripture_citation"])
        flagged += int(is_flagged)
        issues: list[str] = []
        try:
            records = list(parser(keyword))
        except Exception as exc:
            records = []
            issues.append("parser_exception")
            status_counts["parser_exception"] += 1
            report = {
                "keyword_id": row["id"],
                "keyword": keyword,
                "is_scripture_citation": is_flagged,
                "issues": issues,
                "parser_error": f"{type(exc).__name__}: {exc}",
                "citations": [],
                "lookups": [],
            }
            failure_rows += 1
            if output is not None:
                output.write(json.dumps(report, ensure_ascii=False) + "\n")
            continue

        chapter_records = [record for record in records if record.get("number") is not None]
        if chapter_records:
            parser_positive += 1
        if is_flagged and not chapter_records:
            issues.append("flag_true_parser_without_chapter")
            status_counts["flag_true_parser_without_chapter"] += 1
        if chapter_records and not is_flagged:
            issues.append("parser_positive_flag_false")
            status_counts["parser_positive_flag_false"] += 1
            flag_mismatches += 1

        enrichment = None
        if chapter_records:
            enrichment = enrich_keyword(keyword, chapter_records, bible)
            if enrichment.enriched:
                resolved += 1
                status_counts["resolved"] += 1
            else:
                issues.append(enrichment.status)
                status_counts[enrichment.status] += 1
                for lookup in enrichment.lookups:
                    if not lookup.ok:
                        lookup_status_counts[lookup.status] += 1
        elif not is_flagged:
            status_counts["not_scripture"] += 1

        if not issues:
            continue
        failure_rows += 1
        report = {
            "keyword_id": row["id"],
            "keyword": keyword,
            "is_scripture_citation": is_flagged,
            "issues": issues,
            "citations": [_citation_record_for_report(record) for record in records],
            "lookups": [
                asdict(lookup) for lookup in (enrichment.lookups if enrichment else ())
            ],
        }
        if output is not None:
            output.write(json.dumps(report, ensure_ascii=False) + "\n")

    return {
        "checked": checked,
        "flagged": flagged,
        "parser_positive": parser_positive,
        "resolved": resolved,
        "failure_rows": failure_rows,
        "flag_mismatches": flag_mismatches,
        "status_counts": dict(sorted(status_counts.items())),
        "lookup_status_counts": dict(sorted(lookup_status_counts.items())),
        "only_flagged": only_flagged,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o parser de keywords bíblicas contra a Vulgata Clementina."
    )
    parser.add_argument("--db", type=Path, default=Path("data/patristica_keywords.db"))
    parser.add_argument("--bible-json", type=Path, default=DEFAULT_VULGATE_JSON)
    parser.add_argument("--out", type=Path, help="JSONL somente com falhas e divergências.")
    parser.add_argument(
        "--only-flagged",
        action="store_true",
        help="Audita apenas keywords com is_scripture_citation=1.",
    )
    parser.add_argument("--limit", type=int, help="Limite de keywords, para smoke tests.")
    parser.add_argument(
        "--fail-on-failure",
        action="store_true",
        help="Retorna exit code 1 quando houver falha ou divergência.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bible = VulgateClementine.from_json(args.bible_json)
    output = args.out.open("w", encoding="utf-8") if args.out else None
    try:
        with connect_readonly(args.db) as con:
            summary = audit_keywords(
                con,
                bible,
                only_flagged=args.only_flagged,
                limit=args.limit,
                output=output,
            )
    finally:
        if output is not None:
            output.close()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 1 if args.fail_on_failure and summary["failure_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
