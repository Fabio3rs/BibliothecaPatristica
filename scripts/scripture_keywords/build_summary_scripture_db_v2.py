#!/usr/bin/env python3
"""Build a compact, auditable scripture index from legacy summary keywords."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.scripture.keyword_parser_v2 import (  # noqa: E402
    PARSER_VERSION,
    ScriptureReference,
    parse_scripture_keyword,
)


SCHEMA_VERSION = "summary-scripture-v2-poc-1"
DEFAULT_SOURCE_DB = PROJECT_ROOT / "data/patristica_resumos.db"
DEFAULT_OUTPUT_DB = Path("/tmp/summary_scripture_citations_v2.db")


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE build_runs (
    id INTEGER PRIMARY KEY,
    source_db TEXT NOT NULL,
    source_table TEXT NOT NULL CHECK(source_table = 'resumos'),
    parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE pages (
    id INTEGER PRIMARY KEY,
    resumo_id INTEGER NOT NULL UNIQUE,
    volume_id TEXT NOT NULL,
    physical_page INTEGER NOT NULL,
    pagina_file TEXT NOT NULL,
    source_row_created_at TEXT NOT NULL,
    keywords_source TEXT NOT NULL,
    keywords_model TEXT NOT NULL,
    keywords_sha256 TEXT NOT NULL,
    UNIQUE(volume_id, physical_page)
);

CREATE TABLE scripture_references (
    id INTEGER PRIMARY KEY,
    versification_id TEXT NOT NULL DEFAULT 'unknown',
    book_key TEXT NOT NULL,
    granularity TEXT NOT NULL CHECK(
        granularity IN ('chapter', 'verse', 'range', 'list')
    ),
    canonical_key TEXT NOT NULL UNIQUE,
    normalized TEXT NOT NULL
);

CREATE TABLE scripture_reference_segments (
    reference_id INTEGER NOT NULL REFERENCES scripture_references(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    start_chapter INTEGER NOT NULL CHECK(start_chapter > 0),
    start_verse INTEGER CHECK(start_verse > 0),
    end_chapter INTEGER NOT NULL CHECK(end_chapter > 0),
    end_verse INTEGER CHECK(end_verse > 0),
    PRIMARY KEY(reference_id, seq)
) WITHOUT ROWID;

CREATE TABLE scripture_mentions (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    reference_id INTEGER NOT NULL REFERENCES scripture_references(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('standalone', 'embedded')),
    raw_label TEXT NOT NULL,
    raw_reference TEXT NOT NULL,
    alternate_chapter INTEGER,
    flags_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(page_id, reference_id, source_path, raw_reference)
);

CREATE TABLE parse_issues (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    raw_label TEXT NOT NULL,
    raw_fragment TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    UNIQUE(page_id, source_path, raw_fragment, issue_code)
);

CREATE INDEX idx_pages_location ON pages(volume_id, physical_page);
CREATE INDEX idx_references_book ON scripture_references(book_key, canonical_key);
CREATE INDEX idx_segments_start ON scripture_reference_segments(
    start_chapter, start_verse, end_chapter, end_verse
);
CREATE INDEX idx_mentions_reference ON scripture_mentions(reference_id, page_id);
CREATE INDEX idx_mentions_page ON scripture_mentions(page_id, reference_id);
CREATE INDEX idx_issues_code ON parse_issues(issue_code, page_id);

CREATE VIEW reference_pages AS
SELECT
    m.reference_id,
    p.volume_id,
    p.physical_page,
    MAX(m.source_kind = 'standalone') AS has_standalone,
    MAX(m.source_kind = 'embedded') AS has_embedded,
    COUNT(*) AS mention_count
FROM scripture_mentions AS m
JOIN pages AS p ON p.id = m.page_id
GROUP BY m.reference_id, p.id;
"""


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Summary DB not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _keyword_text(item: Any) -> str | None:
    if isinstance(item, str):
        text = item.strip()
        return text or None
    if not isinstance(item, dict):
        return None
    for key in ("keyword", "label", "name", "value", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_published_keywords(raw_json: str) -> tuple[list[str], str | None]:
    """Return exactly the top-level keywords exported by enrichment shards."""

    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return [], "keywords_json is not an object"
    values = payload.get("keywords") or payload.get("keywords_ranking") or []
    if not isinstance(values, list):
        return [], "keywords/keywords_ranking is not an array"
    output: list[str] = []
    for item in values:
        text = _keyword_text(item)
        if text is not None:
            output.append(text)
    return output, None


def _source_rows(
    connection: sqlite3.Connection,
    *,
    document: str | None,
    limit: int | None,
) -> Iterable[sqlite3.Row]:
    sql = """
        SELECT id, documento, pagina_num, pagina_file, criado_em,
               keywords_json, keywords_source, keywords_modelo
          FROM resumos
         WHERE COALESCE(keywords_json, '') != ''
    """
    params: list[object] = []
    if document:
        sql += " AND documento = ?"
        params.append(document)
    sql += " ORDER BY documento, pagina_num, id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return connection.execute(sql, params)


def _ensure_page(
    destination: sqlite3.Connection,
    row: sqlite3.Row,
    keywords_hash: str,
    cache: dict[int, int],
) -> int:
    resumo_id = int(row["id"])
    cached = cache.get(resumo_id)
    if cached is not None:
        return cached
    cursor = destination.execute(
        """
        INSERT INTO pages (
            resumo_id, volume_id, physical_page, pagina_file,
            source_row_created_at, keywords_source, keywords_model, keywords_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resumo_id,
            row["documento"],
            int(row["pagina_num"]),
            row["pagina_file"] or "",
            row["criado_em"] or "",
            row["keywords_source"] or "",
            row["keywords_modelo"] or "",
            keywords_hash,
        ),
    )
    page_id = int(cursor.lastrowid)
    cache[resumo_id] = page_id
    return page_id


def _ensure_reference(
    destination: sqlite3.Connection,
    reference: ScriptureReference,
    cache: dict[str, int],
) -> int:
    cached = cache.get(reference.canonical_key)
    if cached is not None:
        return cached
    cursor = destination.execute(
        """
        INSERT OR IGNORE INTO scripture_references (
            versification_id, book_key, granularity, canonical_key, normalized
        ) VALUES ('unknown', ?, ?, ?, ?)
        """,
        (
            reference.book_key,
            reference.granularity,
            reference.canonical_key,
            reference.normalized,
        ),
    )
    if cursor.lastrowid:
        reference_id = int(cursor.lastrowid)
        destination.executemany(
            """
            INSERT INTO scripture_reference_segments (
                reference_id, seq, start_chapter, start_verse,
                end_chapter, end_verse
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    reference_id,
                    seq,
                    segment.start_chapter,
                    segment.start_verse,
                    segment.end_chapter,
                    segment.end_verse,
                )
                for seq, segment in enumerate(reference.segments)
            ],
        )
    else:
        selected = destination.execute(
            "SELECT id FROM scripture_references WHERE canonical_key = ?",
            (reference.canonical_key,),
        ).fetchone()
        if selected is None:
            raise RuntimeError(f"Could not resolve reference {reference.canonical_key}")
        reference_id = int(selected[0])
    cache[reference.canonical_key] = reference_id
    return reference_id


def _final_stats(destination: sqlite3.Connection, counters: Counter[str]) -> dict[str, int]:
    stats = dict(sorted(counters.items()))
    queries = {
        "stored_pages": "SELECT COUNT(*) FROM pages",
        "unique_references": "SELECT COUNT(*) FROM scripture_references",
        "reference_segments": "SELECT COUNT(*) FROM scripture_reference_segments",
        "mentions": "SELECT COUNT(*) FROM scripture_mentions",
        "mentions_standalone": (
            "SELECT COUNT(*) FROM scripture_mentions WHERE source_kind = 'standalone'"
        ),
        "mentions_embedded": (
            "SELECT COUNT(*) FROM scripture_mentions WHERE source_kind = 'embedded'"
        ),
        "unique_reference_page_pairs": "SELECT COUNT(*) FROM reference_pages",
        "issue_rows": "SELECT COUNT(*) FROM parse_issues",
        "books": "SELECT COUNT(DISTINCT book_key) FROM scripture_references",
        "volumes": "SELECT COUNT(DISTINCT volume_id) FROM pages",
    }
    for key, sql in queries.items():
        stats[key] = int(destination.execute(sql).fetchone()[0])
    return stats


def build_database(
    source_db: Path,
    output_db: Path,
    *,
    document: str | None = None,
    limit: int | None = None,
    progress_every: int = 10_000,
) -> dict[str, int]:
    if output_db.exists():
        raise FileExistsError(f"Output already exists: {output_db}")
    output_db.parent.mkdir(parents=True, exist_ok=True)
    source = connect_readonly(source_db)
    destination = sqlite3.connect(output_db)
    destination.execute("PRAGMA journal_mode = WAL")
    destination.execute("PRAGMA synchronous = NORMAL")
    destination.executescript(SCHEMA_SQL)
    started_at = datetime.now(timezone.utc).isoformat()
    destination.execute(
        """
        INSERT INTO build_runs (
            source_db, source_table, parser_version, schema_version, started_at, status
        ) VALUES (?, 'resumos', ?, ?, ?, 'running')
        """,
        (str(source_db.resolve()), PARSER_VERSION, SCHEMA_VERSION, started_at),
    )
    destination.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("schema_version", SCHEMA_VERSION),
            ("parser_version", PARSER_VERSION),
            ("source_table", "resumos"),
            ("source_contract", "legacy-v1-published-keywords"),
            ("page_semantics", "volume_id + physical_page from resumos.pagina_num"),
        ),
    )
    destination.commit()

    counters: Counter[str] = Counter()
    page_cache: dict[int, int] = {}
    reference_cache: dict[str, int] = {}
    try:
        destination.execute("BEGIN")
        for row in _source_rows(source, document=document, limit=limit):
            counters["source_rows"] += 1
            raw_json = row["keywords_json"] or ""
            keywords, parse_error = extract_published_keywords(raw_json)
            if parse_error:
                counters["invalid_keyword_payloads"] += 1
                continue
            counters["keyword_items"] += len(keywords)
            keywords_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
            collection = str(row["documento"] or "")[:2].upper()
            for rank, label in enumerate(keywords):
                result = parse_scripture_keyword(label, collection=collection)
                if not result.references and not result.issues:
                    continue
                page_id = _ensure_page(destination, row, keywords_hash, page_cache)
                source_path = f"keywords[{rank}]"
                if result.references:
                    counters["parser_positive_labels"] += 1
                for reference in result.references:
                    reference_id = _ensure_reference(
                        destination, reference, reference_cache
                    )
                    destination.execute(
                        """
                        INSERT OR IGNORE INTO scripture_mentions (
                            page_id, reference_id, source_path, source_rank, source_kind,
                            raw_label, raw_reference, alternate_chapter, flags_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            page_id,
                            reference_id,
                            source_path,
                            rank,
                            reference.source_kind,
                            label,
                            reference.raw,
                            reference.alternate_chapter,
                            json.dumps(reference.flags, ensure_ascii=False),
                        ),
                    )
                    counters[f"mentions_{reference.source_kind}"] += 1
                for issue in result.issues:
                    destination.execute(
                        """
                        INSERT OR IGNORE INTO parse_issues (
                            page_id, source_path, source_rank, raw_label, raw_fragment,
                            issue_code, detail
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            page_id,
                            source_path,
                            rank,
                            label,
                            issue.raw,
                            issue.code,
                            issue.detail,
                        ),
                    )
                    counters[f"issues_{issue.code}"] += 1
            if counters["source_rows"] % max(1, progress_every) == 0:
                destination.commit()
                destination.execute("BEGIN")
                print(
                    f"rows={counters['source_rows']} keywords={counters['keyword_items']} "
                    f"positive_labels={counters['parser_positive_labels']}",
                    file=sys.stderr,
                )
        destination.commit()
        stats = _final_stats(destination, counters)
        completed_at = datetime.now(timezone.utc).isoformat()
        destination.execute(
            """
            UPDATE build_runs
               SET completed_at = ?, status = 'complete', stats_json = ?
             WHERE id = 1
            """,
            (completed_at, json.dumps(stats, ensure_ascii=False, sort_keys=True)),
        )
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return stats
    except Exception:
        destination.rollback()
        destination.execute(
            "UPDATE build_runs SET status = 'failed' WHERE id = 1"
        )
        destination.commit()
        raise
    finally:
        source.close()
        destination.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build scripture citations from published legacy summary keywords."
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--out-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--document")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=10_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stats = build_database(
        args.source_db,
        args.out_db,
        document=args.document,
        limit=args.limit,
        progress_every=args.progress_every,
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
