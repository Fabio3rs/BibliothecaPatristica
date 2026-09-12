#!/usr/bin/env python3
"""Build an offline scripture DB from summary keywords and deterministic OCR.

The two evidence sources remain separate:

* ``keyword`` mentions come from the published top-level summary keywords;
* ``ocr`` mentions come from ``data/scripture_citations.db`` and are mapped to
  summary pages by ``(volume_id, pagina_file basename)``.

The OCR evidence DB can be refreshed incrementally before compaction with
``--refresh-ocr --workers 20``.  The compact output is deliberately separate
from both input databases and is not a web publication artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.scripture.book_catalog import (  # noqa: E402
    canonical_book_key,
    canonical_book_label,
)
from tools.scripture.citation_index import (  # noqa: E402
    DEFAULT_CITATION_DB,
    DETECTOR_VERSION,
    MAX_SCAN_WORKERS,
    build_citation_database,
    discover_volume_roots,
)
from tools.scripture.keyword_parser_v2 import (  # noqa: E402
    PARSER_VERSION,
    ScriptureReference,
    ScriptureSegment,
    parse_scripture_keyword,
)
from scripts.scripture_keywords.build_summary_scripture_db_v2 import (  # noqa: E402
    extract_published_keywords,
)
from scripts.scripture_keywords.vulgate_clementine import (  # noqa: E402
    BOOK_NAME_MAP,
    DEFAULT_VULGATE_JSON,
)


SCHEMA_VERSION = "summary-scripture-v3-offline-2"
DEFAULT_SOURCE_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_OUTPUT_DB = Path("/tmp/summary_scripture_citations_v3.db")


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE build_runs (
    id INTEGER PRIMARY KEY,
    source_db TEXT NOT NULL,
    ocr_db TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    detector_version INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    versification_profile TEXT NOT NULL,
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
    ocr_file_sha256 TEXT NOT NULL DEFAULT '',
    UNIQUE(volume_id, physical_page),
    UNIQUE(volume_id, pagina_file)
);

CREATE TABLE scripture_references (
    id INTEGER PRIMARY KEY,
    versification_id TEXT NOT NULL,
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
    source_origin TEXT NOT NULL CHECK(source_origin IN ('keyword', 'ocr')),
    source_path TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    source_context TEXT NOT NULL,
    detector_rule TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    raw_label TEXT NOT NULL,
    raw_reference TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    flags_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(page_id, reference_id, source_origin, source_path, raw_reference)
);

CREATE TABLE rejected_mentions (
    id INTEGER PRIMARY KEY,
    page_id INTEGER REFERENCES pages(id) ON DELETE CASCADE,
    source_origin TEXT NOT NULL CHECK(source_origin IN ('keyword', 'ocr')),
    source_path TEXT NOT NULL,
    raw_label TEXT NOT NULL,
    raw_reference TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    UNIQUE(page_id, source_origin, source_path, raw_reference, issue_code)
);

CREATE INDEX idx_pages_location ON pages(volume_id, physical_page);
CREATE INDEX idx_references_book ON scripture_references(book_key, canonical_key);
CREATE INDEX idx_segments_start ON scripture_reference_segments(
    start_chapter, start_verse, end_chapter, end_verse
);
CREATE INDEX idx_mentions_reference ON scripture_mentions(reference_id, page_id);
CREATE INDEX idx_mentions_page ON scripture_mentions(page_id, reference_id);
CREATE INDEX idx_mentions_origin ON scripture_mentions(source_origin, source_context);
CREATE INDEX idx_rejected_issue ON rejected_mentions(issue_code, source_origin);

CREATE VIEW reference_pages AS
SELECT
    m.reference_id,
    p.volume_id,
    p.physical_page,
    MAX(m.source_origin = 'keyword' AND m.source_context = 'standalone')
      + 2 * MAX(m.source_origin = 'keyword' AND m.source_context = 'embedded')
      + 4 * MAX(m.source_origin = 'ocr') AS source_mask,
    COUNT(*) AS mention_count
FROM scripture_mentions AS m
JOIN pages AS p ON p.id = m.page_id
GROUP BY m.reference_id, p.id;
"""


@dataclass(frozen=True)
class SourcePage:
    resumo_id: int
    volume_id: str
    physical_page: int
    pagina_file: str
    created_at: str
    keywords_json: str
    keywords_source: str
    keywords_model: str

    @property
    def file_key(self) -> tuple[str, str]:
        return self.volume_id, Path(self.pagina_file).name


class VersificationBounds:
    def __init__(
        self,
        profile: str,
        chapter_verses: Mapping[str, tuple[int, ...]],
        source_sha256: str,
    ) -> None:
        self.profile = profile
        self.chapter_verses = dict(chapter_verses)
        self.source_sha256 = source_sha256

    @classmethod
    def from_json(cls, path: Path) -> "VersificationBounds":
        raw = path.read_bytes()
        payload = json.loads(raw)
        books = payload.get("books") if isinstance(payload, dict) else None
        if not isinstance(books, list):
            raise ValueError("versification JSON must contain a books array")

        source_by_key: dict[str, str] = {}
        for label, source_name in BOOK_NAME_MAP.items():
            book_key = canonical_book_key(label)
            if book_key:
                source_by_key[book_key] = source_name
        raw_bounds: dict[str, tuple[int, ...]] = {}
        for book in books:
            if not isinstance(book, dict) or not isinstance(book.get("name"), str):
                continue
            maxima = []
            for chapter in book.get("chapters") or []:
                verses = chapter.get("verses") if isinstance(chapter, dict) else []
                maxima.append(
                    max(
                        (
                            int(verse["verse"])
                            for verse in verses or []
                            if isinstance(verse, dict)
                            and isinstance(verse.get("verse"), int)
                        ),
                        default=0,
                    )
                )
            raw_bounds[book["name"]] = tuple(maxima)
        chapter_verses = {
            book_key: raw_bounds[source_name]
            for book_key, source_name in source_by_key.items()
            if source_name in raw_bounds
        }
        return cls(
            "vulgate-clementine",
            chapter_verses,
            hashlib.sha256(raw).hexdigest(),
        )

    def validate(self, reference: ScriptureReference) -> tuple[str, str]:
        bounds = self.chapter_verses.get(reference.book_key)
        if not bounds:
            return "book_not_in_profile", reference.book_key
        for segment in reference.segments:
            start_position = (segment.start_chapter, segment.start_verse or 0)
            end_position = (segment.end_chapter, segment.end_verse or 0)
            if end_position < start_position:
                return (
                    "range_endpoint_before_start",
                    f"range ends at {end_position[0]}:{end_position[1]} "
                    f"before {start_position[0]}:{start_position[1]}",
                )
            for chapter, verse, endpoint in (
                (segment.start_chapter, segment.start_verse, "start"),
                (segment.end_chapter, segment.end_verse, "end"),
            ):
                if not 1 <= chapter <= len(bounds):
                    return (
                        "chapter_out_of_profile",
                        f"{endpoint} chapter {chapter} exceeds {len(bounds)}",
                    )
                if verse is not None and not 1 <= verse <= bounds[chapter - 1]:
                    return (
                        "verse_out_of_profile",
                        f"{endpoint} verse {verse} exceeds {bounds[chapter - 1]} "
                        f"for {reference.book_key} {chapter}",
                    )
        return "valid", ""


def connect_readonly(path: Path, label: str) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _source_pages(
    connection: sqlite3.Connection,
    *,
    document: str | None,
    limit: int | None,
) -> list[SourcePage]:
    sql = """
        SELECT id, documento, pagina_num, pagina_file, criado_em,
               keywords_json, keywords_source, keywords_modelo
          FROM resumos
    """
    params: list[object] = []
    if document:
        sql += " WHERE documento = ?"
        params.append(document)
    sql += " ORDER BY documento, pagina_num, id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [
        SourcePage(
            resumo_id=int(row["id"]),
            volume_id=str(row["documento"]),
            physical_page=int(row["pagina_num"]),
            pagina_file=str(row["pagina_file"] or ""),
            created_at=str(row["criado_em"] or ""),
            keywords_json=str(row["keywords_json"] or ""),
            keywords_source=str(row["keywords_source"] or ""),
            keywords_model=str(row["keywords_modelo"] or ""),
        )
        for row in connection.execute(sql, params)
    ]


def _page_lookup(
    pages: Iterable[SourcePage],
) -> tuple[dict[tuple[str, str], SourcePage], set[tuple[str, str]]]:
    lookup: dict[tuple[str, str], SourcePage] = {}
    collisions: set[tuple[str, str]] = set()
    for page in pages:
        key = page.file_key
        if not key[1]:
            continue
        if key in lookup and lookup[key].resumo_id != page.resumo_id:
            collisions.add(key)
            lookup.pop(key, None)
        elif key not in collisions:
            lookup[key] = page
    return lookup, collisions


def _ensure_page(
    destination: sqlite3.Connection,
    page: SourcePage,
    cache: dict[int, int],
    *,
    ocr_sha256: str = "",
) -> int:
    cached = cache.get(page.resumo_id)
    if cached is not None:
        if ocr_sha256:
            destination.execute(
                "UPDATE pages SET ocr_file_sha256 = ? WHERE id = ?",
                (ocr_sha256, cached),
            )
        return cached
    cursor = destination.execute(
        """
        INSERT INTO pages(
            resumo_id, volume_id, physical_page, pagina_file,
            source_row_created_at, keywords_source, keywords_model,
            keywords_sha256, ocr_file_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            page.resumo_id,
            page.volume_id,
            page.physical_page,
            Path(page.pagina_file).name,
            page.created_at,
            page.keywords_source,
            page.keywords_model,
            hashlib.sha256(page.keywords_json.encode("utf-8")).hexdigest(),
            ocr_sha256,
        ),
    )
    page_id = int(cursor.lastrowid)
    cache[page.resumo_id] = page_id
    return page_id


def _ensure_reference(
    destination: sqlite3.Connection,
    reference: ScriptureReference,
    cache: dict[str, int],
    versification_id: str,
) -> int:
    _legacy_profile, separator, locator = reference.canonical_key.partition("|")
    if not separator:
        raise ValueError(f"invalid reference canonical key: {reference.canonical_key}")
    canonical_key = f"{versification_id}|{locator}"
    cached = cache.get(canonical_key)
    if cached is not None:
        return cached
    cursor = destination.execute(
        """
        INSERT OR IGNORE INTO scripture_references(
            versification_id, book_key, granularity, canonical_key, normalized
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            versification_id,
            reference.book_key,
            reference.granularity,
            canonical_key,
            reference.normalized,
        ),
    )
    if cursor.lastrowid:
        reference_id = int(cursor.lastrowid)
        destination.executemany(
            """
            INSERT INTO scripture_reference_segments(
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
        row = destination.execute(
            "SELECT id FROM scripture_references WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"reference disappeared: {canonical_key}")
        reference_id = int(row[0])
    cache[canonical_key] = reference_id
    return reference_id


def _format_reference(
    book_key: str,
    segment: ScriptureSegment,
) -> str:
    label = canonical_book_label(book_key) or book_key
    value = f"{label} {segment.start_chapter}"
    if segment.start_verse is not None:
        value += f":{segment.start_verse}"
    if segment.end_chapter != segment.start_chapter:
        value += f"-{segment.end_chapter}"
        if segment.end_verse is not None:
            value += f":{segment.end_verse}"
    elif segment.end_verse != segment.start_verse:
        value += f"-{segment.end_verse}"
    return value


def _reference_from_ocr(row: sqlite3.Row) -> ScriptureReference | None:
    book_key = str(row["book_key"] or "")
    chapter = row["chapter_start"]
    if not book_key or not isinstance(chapter, int) or chapter < 1:
        return None
    verse = row["verse_start"] if isinstance(row["verse_start"], int) else None
    chapter_end = (
        row["chapter_end"] if isinstance(row["chapter_end"], int) else chapter
    )
    verse_end = row["verse_end"] if isinstance(row["verse_end"], int) else verse
    flags: list[str] = []
    if (
        verse is not None
        and chapter_end < chapter
        and chapter_end >= verse
        and verse_end is not None
    ):
        verse_end = chapter_end
        chapter_end = chapter
        flags.append("recovered_same_chapter_range_before_footnote")
    segment = ScriptureSegment(chapter, verse, chapter_end, verse_end)
    granularity = (
        "chapter"
        if verse is None and chapter_end == chapter
        else "verse"
        if verse is not None and chapter_end == chapter and verse_end == verse
        else "range"
    )
    raw_reference = str(row["ref_raw"] or row["citation_raw"] or "")
    return ScriptureReference(
        book_key=book_key,
        book_label=canonical_book_label(book_key) or book_key,
        granularity=granularity,
        segments=(segment,),
        raw=raw_reference,
        normalized=_format_reference(book_key, segment),
        start=0,
        end=len(raw_reference),
        source_kind="embedded",
        flags=tuple(flags + (["open_end"] if int(row["open_ended"] or 0) else [])),
    )


def _record_rejection(
    destination: sqlite3.Connection,
    *,
    page_id: int | None,
    origin: str,
    source_path: str,
    raw_label: str,
    raw_reference: str,
    code: str,
    detail: str,
) -> None:
    destination.execute(
        """
        INSERT OR IGNORE INTO rejected_mentions(
            page_id, source_origin, source_path, raw_label, raw_reference,
            issue_code, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (page_id, origin, source_path, raw_label, raw_reference, code, detail),
    )


def _insert_mention(
    destination: sqlite3.Connection,
    *,
    page_id: int,
    reference_id: int,
    origin: str,
    source_path: str,
    source_rank: int,
    source_context: str,
    detector_rule: str,
    confidence: float,
    raw_label: str,
    raw_reference: str,
    validation_status: str,
    flags: tuple[str, ...],
) -> bool:
    cursor = destination.execute(
        """
        INSERT OR IGNORE INTO scripture_mentions(
            page_id, reference_id, source_origin, source_path, source_rank,
            source_context, detector_rule, confidence, raw_label,
            raw_reference, validation_status, flags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            page_id,
            reference_id,
            origin,
            source_path,
            source_rank,
            source_context,
            detector_rule,
            confidence,
            raw_label,
            raw_reference,
            validation_status,
            json.dumps(flags, ensure_ascii=False),
        ),
    )
    return bool(cursor.rowcount)


def _ocr_rows(
    connection: sqlite3.Connection,
    *,
    document: str | None,
    include_index_sources: bool,
) -> Iterable[sqlite3.Row]:
    sql = """
        SELECT o.occurrence_id, o.occurrence_key, o.book_key, o.ref_raw,
               o.ref_norm, o.chapter_start, o.verse_start, o.chapter_end,
               o.verse_end, o.open_ended, o.normalization_status,
               o.historical_book_key,
               o.confidence, g.citation_raw, g.context_kind, g.detector_rule,
               f.volume_id, f.source_relpath, f.file_sha256, f.is_index_source
          FROM citation_occurrences AS o
          JOIN citation_groups AS g ON g.group_id = o.group_id
          JOIN citation_files AS f ON f.file_id = g.file_id
         WHERE 1 = 1
    """
    params: list[object] = []
    if not include_index_sources:
        sql += " AND f.is_index_source = 0 AND g.context_kind != 'index'"
    if document:
        sql += " AND f.volume_id = ?"
        params.append(document)
    sql += " ORDER BY f.volume_id, f.source_relpath, o.occurrence_id"
    return connection.execute(sql, params)


def _final_stats(destination: sqlite3.Connection, counters: Counter[str]) -> dict[str, int]:
    stats = dict(sorted(counters.items()))
    queries = {
        "stored_pages": "SELECT COUNT(*) FROM pages",
        "unique_references": "SELECT COUNT(*) FROM scripture_references",
        "reference_segments": "SELECT COUNT(*) FROM scripture_reference_segments",
        "mentions": "SELECT COUNT(*) FROM scripture_mentions",
        "mentions_keyword": "SELECT COUNT(*) FROM scripture_mentions WHERE source_origin='keyword'",
        "mentions_ocr": "SELECT COUNT(*) FROM scripture_mentions WHERE source_origin='ocr'",
        "reference_page_pairs": "SELECT COUNT(*) FROM reference_pages",
        "rejected_mentions": "SELECT COUNT(*) FROM rejected_mentions",
        "books": "SELECT COUNT(DISTINCT book_key) FROM scripture_references",
        "volumes": "SELECT COUNT(DISTINCT volume_id) FROM pages",
    }
    for key, sql in queries.items():
        stats[key] = int(destination.execute(sql).fetchone()[0])
    return stats


def build_database(
    source_db: Path,
    ocr_db: Path,
    output_db: Path,
    *,
    versification_json: Path = DEFAULT_VULGATE_JSON,
    document: str | None = None,
    limit: int | None = None,
    min_ocr_confidence: float = 0.70,
    include_index_sources: bool = False,
    progress_every: int = 25_000,
) -> dict[str, int]:
    if output_db.exists():
        raise FileExistsError(f"output already exists: {output_db}")
    if not 0.0 <= min_ocr_confidence <= 1.0:
        raise ValueError("min_ocr_confidence must be between 0 and 1")
    output_db.parent.mkdir(parents=True, exist_ok=True)
    bounds = VersificationBounds.from_json(versification_json)
    source = connect_readonly(source_db, "summary DB")
    ocr = connect_readonly(ocr_db, "OCR citation DB")
    detector_sql = (
        "SELECT detector_version, COUNT(*) FROM citation_files"
        + (" WHERE volume_id = ?" if document else "")
        + " GROUP BY detector_version"
    )
    detector_rows = ocr.execute(
        detector_sql,
        (document,) if document else (),
    ).fetchall()
    source_detector_versions = {
        int(row[0]): int(row[1]) for row in detector_rows
    }
    destination = sqlite3.connect(output_db)
    destination.execute("PRAGMA journal_mode = WAL")
    destination.execute("PRAGMA synchronous = NORMAL")
    destination.executescript(SCHEMA_SQL)
    started_at = datetime.now(timezone.utc).isoformat()
    destination.execute(
        """
        INSERT INTO build_runs(
            source_db, ocr_db, parser_version, detector_version,
            schema_version, versification_profile, started_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            str(source_db.resolve()),
            str(ocr_db.resolve()),
            PARSER_VERSION,
            DETECTOR_VERSION,
            SCHEMA_VERSION,
            bounds.profile,
            started_at,
        ),
    )
    destination.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("schema_version", SCHEMA_VERSION),
            ("parser_version", PARSER_VERSION),
            ("detector_version", str(DETECTOR_VERSION)),
            (
                "source_detector_versions",
                json.dumps(source_detector_versions, sort_keys=True),
            ),
            ("source_contract", "resumos-keywords+deterministic-ocr"),
            ("versification_profile", bounds.profile),
            ("versification_sha256", bounds.source_sha256),
            ("page_mapping", "volume_id + pagina_file basename"),
        ),
    )
    destination.commit()

    counters: Counter[str] = Counter()
    counters["ocr_files_detector_mismatch"] = sum(
        count
        for version, count in source_detector_versions.items()
        if version != DETECTOR_VERSION
    )
    page_cache: dict[int, int] = {}
    reference_cache: dict[str, int] = {}
    try:
        pages = _source_pages(source, document=document, limit=limit)
        lookup, collisions = _page_lookup(pages)
        counters["source_pages"] = len(pages)
        counters["page_filename_collisions"] = len(collisions)
        destination.execute("BEGIN")

        for page in pages:
            keywords, parse_error = extract_published_keywords(page.keywords_json)
            if parse_error:
                counters["invalid_keyword_payloads"] += 1
                continue
            counters["keyword_items"] += len(keywords)
            for rank, label in enumerate(keywords):
                parsed = parse_scripture_keyword(
                    label,
                    collection=page.volume_id[:2].upper(),
                )
                page_id: int | None = None
                for issue in parsed.issues:
                    page_id = page_id or _ensure_page(destination, page, page_cache)
                    _record_rejection(
                        destination,
                        page_id=page_id,
                        origin="keyword",
                        source_path=f"keywords[{rank}]",
                        raw_label=label,
                        raw_reference=issue.raw,
                        code=issue.code,
                        detail=issue.detail,
                    )
                    counters[f"keyword_rejected_{issue.code}"] += 1
                for reference in parsed.references:
                    validation, detail = bounds.validate(reference)
                    page_id = page_id or _ensure_page(destination, page, page_cache)
                    if validation != "valid":
                        _record_rejection(
                            destination,
                            page_id=page_id,
                            origin="keyword",
                            source_path=f"keywords[{rank}]",
                            raw_label=label,
                            raw_reference=reference.raw,
                            code=validation,
                            detail=detail,
                        )
                        counters[f"keyword_rejected_{validation}"] += 1
                        continue
                    reference_id = _ensure_reference(
                        destination,
                        reference,
                        reference_cache,
                        bounds.profile,
                    )
                    if _insert_mention(
                        destination,
                        page_id=page_id,
                        reference_id=reference_id,
                        origin="keyword",
                        source_path=f"keywords[{rank}]",
                        source_rank=rank,
                        source_context=reference.source_kind,
                        detector_rule="keyword_parser_v2",
                        confidence=1.0,
                        raw_label=label,
                        raw_reference=reference.raw,
                        validation_status=validation,
                        flags=reference.flags,
                    ):
                        counters["keyword_mentions_inserted"] += 1

        for row_index, row in enumerate(
            _ocr_rows(
                ocr,
                document=document,
                include_index_sources=include_index_sources,
            ),
            start=1,
        ):
            counters["ocr_occurrences_considered"] += 1
            key = (str(row["volume_id"]), Path(str(row["source_relpath"])).name)
            page = lookup.get(key)
            if page is None:
                counters[
                    "ocr_unmapped_collision" if key in collisions else "ocr_unmapped_page"
                ] += 1
                continue
            reference = _reference_from_ocr(row)
            page_id = _ensure_page(
                destination,
                page,
                page_cache,
                ocr_sha256=str(row["file_sha256"] or ""),
            )
            normalization_status = str(row["normalization_status"] or "")
            confidence = float(row["confidence"] or 0.0)
            if normalization_status != "normalized":
                historical_key = str(row["historical_book_key"] or "")
                detail = f"detector normalization_status={normalization_status}"
                if historical_key:
                    detail += f"; historical_book_key={historical_key}"
                _record_rejection(
                    destination,
                    page_id=page_id,
                    origin="ocr",
                    source_path=str(row["occurrence_key"]),
                    raw_label=str(row["citation_raw"] or ""),
                    raw_reference=str(row["ref_raw"] or ""),
                    code=f"detector_{normalization_status}",
                    detail=detail,
                )
                counters[f"ocr_rejected_detector_{normalization_status}"] += 1
                continue
            if confidence < min_ocr_confidence:
                _record_rejection(
                    destination,
                    page_id=page_id,
                    origin="ocr",
                    source_path=str(row["occurrence_key"]),
                    raw_label=str(row["citation_raw"] or ""),
                    raw_reference=str(row["ref_raw"] or ""),
                    code="confidence_below_threshold",
                    detail=(
                        f"confidence={confidence:.3f}; "
                        f"threshold={min_ocr_confidence:.3f}"
                    ),
                )
                counters["ocr_rejected_confidence_below_threshold"] += 1
                continue
            if reference is None:
                _record_rejection(
                    destination,
                    page_id=page_id,
                    origin="ocr",
                    source_path=str(row["occurrence_key"]),
                    raw_label=str(row["citation_raw"] or ""),
                    raw_reference=str(row["ref_raw"] or ""),
                    code="incomplete_ocr_reference",
                    detail="normalized OCR row lacks a canonical book/chapter",
                )
                counters["ocr_rejected_incomplete"] += 1
                continue
            validation, detail = bounds.validate(reference)
            if validation != "valid":
                _record_rejection(
                    destination,
                    page_id=page_id,
                    origin="ocr",
                    source_path=str(row["occurrence_key"]),
                    raw_label=str(row["citation_raw"] or ""),
                    raw_reference=reference.raw,
                    code=validation,
                    detail=detail,
                )
                counters[f"ocr_rejected_{validation}"] += 1
                continue
            reference_id = _ensure_reference(
                destination,
                reference,
                reference_cache,
                bounds.profile,
            )
            if _insert_mention(
                destination,
                page_id=page_id,
                reference_id=reference_id,
                origin="ocr",
                source_path=str(row["occurrence_key"]),
                source_rank=int(row["occurrence_id"]),
                source_context=str(row["context_kind"] or "unknown"),
                detector_rule=str(row["detector_rule"] or ""),
                confidence=confidence,
                raw_label=str(row["citation_raw"] or ""),
                raw_reference=reference.raw,
                validation_status=validation,
                flags=reference.flags,
            ):
                counters["ocr_mentions_inserted"] += 1
            if row_index % max(1, progress_every) == 0:
                destination.commit()
                destination.execute("BEGIN")
                print(
                    f"ocr={row_index} inserted={counters['ocr_mentions_inserted']} "
                    f"unmapped={counters['ocr_unmapped_page']}",
                    file=sys.stderr,
                )

        destination.commit()
        stats = _final_stats(destination, counters)
        destination.execute(
            """
            UPDATE build_runs
               SET completed_at = ?, status = 'complete', stats_json = ?
             WHERE id = 1
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                json.dumps(stats, ensure_ascii=False, sort_keys=True),
            ),
        )
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return stats
    except Exception:
        destination.rollback()
        destination.execute("UPDATE build_runs SET status = 'failed' WHERE id = 1")
        destination.commit()
        raise
    finally:
        source.close()
        ocr.close()
        destination.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an offline scripture DB from summary keywords plus the "
            "deterministic OCR citation database."
        )
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--ocr-db", type=Path, default=DEFAULT_CITATION_DB)
    parser.add_argument("--out-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument(
        "--versification-json",
        type=Path,
        default=DEFAULT_VULGATE_JSON,
    )
    parser.add_argument("--document")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-ocr-confidence", type=float, default=0.70)
    parser.add_argument("--include-index-sources", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25_000)
    parser.add_argument(
        "--refresh-ocr",
        action="store_true",
        help="Incrementally refresh the OCR evidence DB before compaction.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_SCAN_WORKERS,
        help=f"OCR scanner processes (1-{MAX_SCAN_WORKERS}; default: {MAX_SCAN_WORKERS}).",
    )
    parser.add_argument("--corpus-root", type=Path, default=PROJECT_ROOT / "teste")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.workers <= MAX_SCAN_WORKERS:
        raise SystemExit(f"--workers must be between 1 and {MAX_SCAN_WORKERS}")
    if args.refresh_ocr:
        volumes = discover_volume_roots(
            corpus_root=args.corpus_root,
            volume_ids=[args.document] if args.document else None,
        )
        build_citation_database(
            db_path=args.ocr_db,
            volumes=volumes,
            workers=args.workers,
        )
    stats = build_database(
        args.source_db,
        args.ocr_db,
        args.out_db,
        versification_json=args.versification_json,
        document=args.document,
        limit=args.limit,
        min_ocr_confidence=args.min_ocr_confidence,
        include_index_sources=args.include_index_sources,
        progress_every=args.progress_every,
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
