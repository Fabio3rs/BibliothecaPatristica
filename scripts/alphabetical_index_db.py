from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.scripture.book_catalog import (
    canonical_book_key,
    contextual_book_tradition,
    historical_noncanonical_book_key,
    normalize_book_alias,
)

DEFAULT_DB = Path("data/alphabetical_indices.db")
ALPHABETICAL_DB_SCHEMA_VERSION = 8

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS alphabetical_schema_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alphabetical_volumes (
    volume_id TEXT PRIMARY KEY,
    collection TEXT NOT NULL CHECK (collection IN ('PG', 'PL', 'PO')),
    source_root TEXT NOT NULL,
    volume_label TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alphabetical_sections (
    section_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    work_key TEXT,
    section_order INTEGER,
    section_kind TEXT NOT NULL CHECK (
        section_kind IN (
            'analytic_subject',
            'alphabetical_general',
            'onomastic_person',
            'onomastic_place',
            'onomastic_mixed',
            'author_index',
            'scripture_index',
            'pericope_index',
            'concordance_index',
            'foreign_terms',
            'ordo_rerum',
            'crosswalk_index',
            'editorial_closure'
        )
    ),
    heading_raw TEXT NOT NULL,
    heading_norm TEXT,
    heading_letter TEXT,
    page_start INTEGER,
    page_end INTEGER,
    file_start TEXT,
    file_end TEXT,
    index_editorial_page_start INTEGER,
    index_editorial_page_end INTEGER,
    index_ocr_file_start TEXT,
    index_ocr_file_end TEXT,
    pipeline_owner TEXT NOT NULL DEFAULT 'alphabetical' CHECK (
        pipeline_owner IN ('alphabetical', 'general')
    ),
    alphabetical_role TEXT NOT NULL DEFAULT 'owned_section' CHECK (
        alphabetical_role IN ('owned_section', 'stop_boundary')
    ),
    material_reference_mode TEXT NOT NULL DEFAULT 'remissive' CHECK (
        material_reference_mode IN (
            'remissive', 'parallel', 'source_only', 'boundary_only'
        )
    ),
    scripture_mode TEXT NOT NULL DEFAULT 'none' CHECK (
        scripture_mode IN (
            'none', 'citation_index', 'pericope_index',
            'concordance_component', 'textual_apparatus',
            'incidental_mention'
        )
    ),
    taxonomy_source TEXT NOT NULL DEFAULT 'legacy_inferred' CHECK (
        taxonomy_source IN ('explicit', 'legacy_inferred')
    ),
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (section_order IS NULL OR section_order >= 1),
    CHECK (page_start IS NOT NULL OR file_start IS NOT NULL),
    CHECK (page_end IS NOT NULL OR file_end IS NOT NULL),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_sections_volume_id
    ON alphabetical_sections(volume_id);
CREATE INDEX IF NOT EXISTS idx_alpha_sections_kind
    ON alphabetical_sections(section_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_sections_volume_order
    ON alphabetical_sections(volume_id, section_order)
    WHERE section_order IS NOT NULL;

CREATE TABLE IF NOT EXISTS alphabetical_nodes (
    node_key TEXT PRIMARY KEY,
    section_key TEXT NOT NULL REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE,
    parent_node_key TEXT REFERENCES alphabetical_nodes(node_key) ON DELETE CASCADE,
    node_order INTEGER NOT NULL,
    node_kind TEXT NOT NULL CHECK (
        node_kind IN (
            'letter_group',
            'heading_group',
            'rubric_group',
            'ordinal_group'
        )
    ),
    label_raw TEXT NOT NULL,
    label_norm TEXT,
    label_sort TEXT,
    node_level INTEGER NOT NULL,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (node_order >= 1),
    CHECK (node_level >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_section_key
    ON alphabetical_nodes(section_key);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_parent
    ON alphabetical_nodes(parent_node_key);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_kind
    ON alphabetical_nodes(node_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_nodes_section_order
    ON alphabetical_nodes(section_key, node_order);

CREATE TABLE IF NOT EXISTS alphabetical_entries (
    entry_key TEXT PRIMARY KEY,
    section_key TEXT NOT NULL REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE,
    parent_node_key TEXT REFERENCES alphabetical_nodes(node_key) ON DELETE SET NULL,
    entry_order INTEGER NOT NULL,
    entry_kind TEXT NOT NULL CHECK (
        entry_kind IN (
            'lemma',
            'sublemma',
            'cross_reference',
            'editorial_note',
            'heading_group',
            'scripture_citation',
            'scripture_pericope',
            'concordance_item'
        )
    ),
    lemma_raw TEXT,
    lemma_display TEXT,
    lemma_norm TEXT,
    lemma_sort TEXT,
    entry_raw TEXT NOT NULL,
    context_raw TEXT,
    heading_letter TEXT,
    inferred_printed_page INTEGER,
    section_start_file TEXT,
    editorial_anchor_file TEXT,
    target_file_best TEXT,
    index_editorial_page_estimate INTEGER,
    index_section_start_ocr_file TEXT,
    index_entry_source_ocr_file TEXT,
    resolved_target_ocr_file TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (entry_order >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_section_key
    ON alphabetical_entries(section_key);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_parent_node
    ON alphabetical_entries(parent_node_key);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_kind
    ON alphabetical_entries(entry_kind);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_lemma_norm
    ON alphabetical_entries(lemma_norm);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_lemma_sort
    ON alphabetical_entries(lemma_sort);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_entries_section_order
    ON alphabetical_entries(section_key, entry_order);

CREATE TABLE IF NOT EXISTS alphabetical_refs (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    scripture_ref_order INTEGER,
    ref_kind TEXT NOT NULL CHECK (
        ref_kind IN (
            'editorial_page',
            'editorial_column',
            'editorial_page_column',
            'editorial_range',
            'editorial_page_line',
            'target_locator',
            'scripture',
            'parallel_locator',
            'unresolved'
        )
    ),
    ref_raw TEXT NOT NULL,
    page_ref_raw TEXT,
    page_ref_int INTEGER,
    page_ref_col TEXT,
    line_ref_raw TEXT,
    range_start_raw TEXT,
    range_end_raw TEXT,
    target_file TEXT,
    target_file_probability REAL,
    cited_editorial_page_start_raw TEXT,
    cited_editorial_page_start_number INTEGER,
    cited_editorial_page_end_raw TEXT,
    cited_editorial_page_end_number INTEGER,
    cited_editorial_column_raw TEXT,
    cited_editorial_line_raw TEXT,
    cited_editorial_line_start_raw TEXT,
    cited_editorial_line_start_number INTEGER,
    cited_editorial_line_end_raw TEXT,
    cited_editorial_line_end_number INTEGER,
    resolved_target_ocr_file TEXT,
    target_ocr_file_candidate_score REAL,
    cited_location_parse_status TEXT NOT NULL DEFAULT 'unparsed' CHECK (
        cited_location_parse_status IN (
            'parsed', 'partial', 'unparsed', 'not_applicable'
        )
    ),
    locator_status TEXT NOT NULL DEFAULT 'unverified' CHECK (
        locator_status IN ('resolved', 'ambiguous', 'unresolved', 'unverified')
    ),
    section_start_file TEXT,
    editorial_anchor_file TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (ref_order >= 1),
    CHECK (scripture_ref_order IS NULL OR scripture_ref_order >= 1),
    CHECK (
        page_ref_raw IS NOT NULL
        OR target_file IS NOT NULL
        OR range_start_raw IS NOT NULL
        OR range_end_raw IS NOT NULL
        OR locator_status IN ('unresolved', 'unverified')
    ),
    CHECK (
        locator_status != 'resolved'
        OR (target_file IS NOT NULL AND TRIM(target_file) != '')
    ),
    CHECK (
        target_file_probability IS NULL
        OR (target_file_probability >= 0.0 AND target_file_probability <= 1.0)
    ),
    CHECK (
        target_ocr_file_candidate_score IS NULL
        OR (
            target_ocr_file_candidate_score >= 0.0
            AND target_ocr_file_candidate_score <= 1.0
        )
    ),
    CHECK (
        cited_editorial_page_start_number IS NULL
        OR cited_editorial_page_start_number >= 1
    ),
    CHECK (
        cited_editorial_page_end_number IS NULL
        OR cited_editorial_page_end_number >= 1
    ),
    CHECK (
        cited_editorial_line_start_number IS NULL
        OR cited_editorial_line_start_number >= 1
    ),
    CHECK (
        cited_editorial_line_end_number IS NULL
        OR cited_editorial_line_end_number >= 1
    ),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_entry_key
    ON alphabetical_refs(entry_key);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_page_ref_int
    ON alphabetical_refs(page_ref_int);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_cited_editorial_page
    ON alphabetical_refs(cited_editorial_page_start_number);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_kind
    ON alphabetical_refs(ref_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_refs_entry_order
    ON alphabetical_refs(entry_key, ref_order);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_scripture_parent
    ON alphabetical_refs(entry_key, scripture_ref_order)
    WHERE scripture_ref_order IS NOT NULL;

CREATE TABLE IF NOT EXISTS alphabetical_source_spans (
    span_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    ocr_file_path TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    block_type TEXT,
    text_sha256 TEXT,
    created_at TEXT NOT NULL,
    CHECK (line_start >= 1),
    CHECK (line_end >= line_start),
    CHECK (
        text_sha256 IS NULL
        OR text_sha256 GLOB '[0-9a-f][0-9a-f]*'
           AND length(text_sha256) = 64
    ),
    UNIQUE (
        volume_id, ocr_file_path, line_start, line_end,
        block_type, text_sha256
    )
);
CREATE INDEX IF NOT EXISTS idx_alpha_source_spans_volume_file
    ON alphabetical_source_spans(volume_id, ocr_file_path);

CREATE TABLE IF NOT EXISTS alphabetical_entry_source_spans (
    entry_key TEXT NOT NULL REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
    span_id INTEGER NOT NULL REFERENCES alphabetical_source_spans(span_id) ON DELETE CASCADE,
    span_order INTEGER NOT NULL DEFAULT 1,
    span_role TEXT NOT NULL DEFAULT 'primary' CHECK (
        span_role IN ('primary', 'continuation', 'supporting')
    ),
    PRIMARY KEY (entry_key, span_order),
    UNIQUE (entry_key, span_id),
    CHECK (span_order >= 1)
);

CREATE TABLE IF NOT EXISTS alphabetical_section_source_spans (
    section_key TEXT NOT NULL REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE,
    span_id INTEGER NOT NULL REFERENCES alphabetical_source_spans(span_id) ON DELETE CASCADE,
    span_order INTEGER NOT NULL DEFAULT 1,
    span_role TEXT NOT NULL DEFAULT 'owned' CHECK (
        span_role IN ('owned', 'context', 'uncertain')
    ),
    PRIMARY KEY (section_key, span_order),
    UNIQUE (section_key, span_id),
    CHECK (span_order >= 1)
);

CREATE TABLE IF NOT EXISTS alphabetical_boundaries (
    boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    boundary_order INTEGER NOT NULL,
    boundary_kind TEXT NOT NULL CHECK (
        boundary_kind IN (
            'ordo_rerum', 'editorial_closure', 'addenda_corrigenda',
            'errata', 'general_pipeline_material', 'other'
        )
    ),
    heading_raw TEXT,
    ocr_file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    reason TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    CHECK (boundary_order >= 1),
    CHECK (line_start IS NULL OR line_start >= 1),
    CHECK (line_end IS NULL OR line_start IS NOT NULL AND line_end >= line_start),
    UNIQUE (volume_id, boundary_order)
);

CREATE TABLE IF NOT EXISTS alphabetical_locator_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id INTEGER NOT NULL REFERENCES alphabetical_refs(ref_id) ON DELETE CASCADE,
    evidence_order INTEGER NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_ocr_file_path TEXT,
    observed_editorial_page_raw TEXT,
    observed_editorial_page_number INTEGER,
    detail TEXT,
    weight REAL,
    raw_json TEXT NOT NULL,
    CHECK (evidence_order >= 1),
    CHECK (
        observed_editorial_page_number IS NULL
        OR observed_editorial_page_number >= 1
    ),
    CHECK (weight IS NULL OR (weight >= 0.0 AND weight <= 1.0)),
    UNIQUE (ref_id, evidence_order)
);
CREATE INDEX IF NOT EXISTS idx_alpha_locator_evidence_ref
    ON alphabetical_locator_evidence(ref_id);
CREATE INDEX IF NOT EXISTS idx_alpha_locator_evidence_kind
    ON alphabetical_locator_evidence(evidence_kind);

CREATE TRIGGER IF NOT EXISTS trg_alpha_sections_legacy_alias_insert
AFTER INSERT ON alphabetical_sections
BEGIN
    UPDATE alphabetical_sections
    SET index_editorial_page_start = COALESCE(
            NEW.index_editorial_page_start, NEW.page_start
        ),
        index_editorial_page_end = COALESCE(
            NEW.index_editorial_page_end, NEW.page_end
        ),
        index_ocr_file_start = COALESCE(
            NEW.index_ocr_file_start, NEW.file_start
        ),
        index_ocr_file_end = COALESCE(NEW.index_ocr_file_end, NEW.file_end),
        page_start = COALESCE(NEW.page_start, NEW.index_editorial_page_start),
        page_end = COALESCE(NEW.page_end, NEW.index_editorial_page_end),
        file_start = COALESCE(NEW.file_start, NEW.index_ocr_file_start),
        file_end = COALESCE(NEW.file_end, NEW.index_ocr_file_end)
    WHERE section_key = NEW.section_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_sections_legacy_alias_update
AFTER UPDATE OF page_start, page_end, file_start, file_end
ON alphabetical_sections
BEGIN
    UPDATE alphabetical_sections
    SET index_editorial_page_start = NEW.page_start,
        index_editorial_page_end = NEW.page_end,
        index_ocr_file_start = NEW.file_start,
        index_ocr_file_end = NEW.file_end
    WHERE section_key = NEW.section_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_sections_canonical_alias_update
AFTER UPDATE OF index_editorial_page_start, index_editorial_page_end,
                index_ocr_file_start, index_ocr_file_end
ON alphabetical_sections
BEGIN
    UPDATE alphabetical_sections
    SET page_start = NEW.index_editorial_page_start,
        page_end = NEW.index_editorial_page_end,
        file_start = NEW.index_ocr_file_start,
        file_end = NEW.index_ocr_file_end
    WHERE section_key = NEW.section_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_entries_legacy_alias_insert
AFTER INSERT ON alphabetical_entries
BEGIN
    UPDATE alphabetical_entries
    SET index_editorial_page_estimate = COALESCE(
            NEW.index_editorial_page_estimate, NEW.inferred_printed_page
        ),
        index_section_start_ocr_file = COALESCE(
            NEW.index_section_start_ocr_file, NEW.section_start_file
        ),
        index_entry_source_ocr_file = COALESCE(
            NEW.index_entry_source_ocr_file, NEW.editorial_anchor_file
        ),
        resolved_target_ocr_file = COALESCE(
            NEW.resolved_target_ocr_file, NEW.target_file_best
        ),
        inferred_printed_page = COALESCE(
            NEW.inferred_printed_page, NEW.index_editorial_page_estimate
        ),
        section_start_file = COALESCE(
            NEW.section_start_file, NEW.index_section_start_ocr_file
        ),
        editorial_anchor_file = COALESCE(
            NEW.editorial_anchor_file, NEW.index_entry_source_ocr_file
        ),
        target_file_best = COALESCE(
            NEW.target_file_best, NEW.resolved_target_ocr_file
        )
    WHERE entry_key = NEW.entry_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_entries_legacy_alias_update
AFTER UPDATE OF inferred_printed_page, section_start_file,
                editorial_anchor_file, target_file_best
ON alphabetical_entries
BEGIN
    UPDATE alphabetical_entries
    SET index_editorial_page_estimate = NEW.inferred_printed_page,
        index_section_start_ocr_file = NEW.section_start_file,
        index_entry_source_ocr_file = NEW.editorial_anchor_file,
        resolved_target_ocr_file = NEW.target_file_best
    WHERE entry_key = NEW.entry_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_entries_canonical_alias_update
AFTER UPDATE OF index_editorial_page_estimate, index_section_start_ocr_file,
                index_entry_source_ocr_file, resolved_target_ocr_file
ON alphabetical_entries
BEGIN
    UPDATE alphabetical_entries
    SET inferred_printed_page = NEW.index_editorial_page_estimate,
        section_start_file = NEW.index_section_start_ocr_file,
        editorial_anchor_file = NEW.index_entry_source_ocr_file,
        target_file_best = NEW.resolved_target_ocr_file
    WHERE entry_key = NEW.entry_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_refs_target_alias_insert
AFTER INSERT ON alphabetical_refs
BEGIN
    UPDATE alphabetical_refs
    SET resolved_target_ocr_file = COALESCE(
            NEW.resolved_target_ocr_file, NEW.target_file
        ),
        target_ocr_file_candidate_score = COALESCE(
            NEW.target_ocr_file_candidate_score,
            NEW.target_file_probability
        ),
        target_file = COALESCE(NEW.target_file, NEW.resolved_target_ocr_file),
        target_file_probability = COALESCE(
            NEW.target_file_probability,
            NEW.target_ocr_file_candidate_score
        )
    WHERE ref_id = NEW.ref_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_refs_legacy_target_update
AFTER UPDATE OF target_file, target_file_probability ON alphabetical_refs
BEGIN
    UPDATE alphabetical_refs
    SET resolved_target_ocr_file = NEW.target_file,
        target_ocr_file_candidate_score = NEW.target_file_probability
    WHERE ref_id = NEW.ref_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_refs_canonical_target_update
AFTER UPDATE OF resolved_target_ocr_file, target_ocr_file_candidate_score
ON alphabetical_refs
BEGIN
    UPDATE alphabetical_refs
    SET target_file = NEW.resolved_target_ocr_file,
        target_file_probability = NEW.target_ocr_file_candidate_score
    WHERE ref_id = NEW.ref_id;
END;

CREATE TABLE IF NOT EXISTS alphabetical_scripture_refs (
    scripture_ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    ref_role TEXT NOT NULL CHECK (
        ref_role IN (
            'citation',
            'pericope',
            'concordance_component'
        )
    ),
    ref_raw TEXT NOT NULL,
    ref_norm TEXT,
    book_raw TEXT,
    book_norm TEXT,
    book_key TEXT,
    chapter_start INTEGER,
    verse_start INTEGER,
    chapter_end INTEGER,
    verse_end INTEGER,
    is_range INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (ref_order >= 1),
    CHECK (is_range IN (0, 1)),
    CHECK (chapter_start IS NULL OR chapter_start >= 1),
    CHECK (verse_start IS NULL OR verse_start >= 1),
    CHECK (chapter_end IS NULL OR chapter_end >= 1),
    CHECK (verse_end IS NULL OR verse_end >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_scripture_entry_key
    ON alphabetical_scripture_refs(entry_key);
CREATE INDEX IF NOT EXISTS idx_alpha_scripture_book_norm
    ON alphabetical_scripture_refs(book_norm);
CREATE INDEX IF NOT EXISTS idx_alpha_scripture_book_key
    ON alphabetical_scripture_refs(book_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_scripture_entry_order
    ON alphabetical_scripture_refs(entry_key, ref_order);

CREATE TRIGGER IF NOT EXISTS trg_alpha_refs_scripture_parent_insert
BEFORE INSERT ON alphabetical_refs
WHEN NEW.scripture_ref_order IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.scripture_ref_order < 1 THEN
            RAISE(ABORT, 'alphabetical_refs.scripture_ref_order must be positive')
        WHEN NOT EXISTS (
            SELECT 1
            FROM alphabetical_scripture_refs sr
            WHERE sr.entry_key = NEW.entry_key
              AND sr.ref_order = NEW.scripture_ref_order
        ) THEN
            RAISE(ABORT, 'alphabetical_refs scripture parent not found')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_alpha_refs_scripture_parent_update
BEFORE UPDATE OF entry_key, scripture_ref_order ON alphabetical_refs
WHEN NEW.scripture_ref_order IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.scripture_ref_order < 1 THEN
            RAISE(ABORT, 'alphabetical_refs.scripture_ref_order must be positive')
        WHEN NOT EXISTS (
            SELECT 1
            FROM alphabetical_scripture_refs sr
            WHERE sr.entry_key = NEW.entry_key
              AND sr.ref_order = NEW.scripture_ref_order
        ) THEN
            RAISE(ABORT, 'alphabetical_refs scripture parent not found')
    END;
END;

CREATE TABLE IF NOT EXISTS alphabetical_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'failed', 'completed', 'imported', 'skipped')
    ),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    notes TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_volume_id
    ON alphabetical_runs(volume_id);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_status
    ON alphabetical_runs(status);

CREATE TABLE IF NOT EXISTS alphabetical_volume_quality (
    volume_id TEXT PRIMARY KEY REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    db_schema_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('valid', 'partial', 'needs_reextract')),
    scripture_entry_count INTEGER NOT NULL,
    scripture_ref_count INTEGER NOT NULL,
    material_ref_count INTEGER NOT NULL,
    linked_material_ref_count INTEGER NOT NULL,
    unlinked_scripture_material_ref_count INTEGER NOT NULL,
    dangling_scripture_link_count INTEGER NOT NULL,
    unresolved_material_ref_count INTEGER NOT NULL,
    locator_partial INTEGER NOT NULL,
    forbidden_section_count INTEGER NOT NULL,
    unknown_scripture_book_count INTEGER NOT NULL,
    unverified_target_count INTEGER NOT NULL,
    entry_without_source_span_count INTEGER NOT NULL DEFAULT 0,
    legacy_inferred_taxonomy_count INTEGER NOT NULL DEFAULT 0,
    malformed_cited_location_count INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    CHECK (db_schema_version >= 1),
    CHECK (scripture_entry_count >= 0),
    CHECK (scripture_ref_count >= 0),
    CHECK (material_ref_count >= 0),
    CHECK (linked_material_ref_count >= 0),
    CHECK (unlinked_scripture_material_ref_count >= 0),
    CHECK (dangling_scripture_link_count >= 0),
    CHECK (unresolved_material_ref_count >= 0),
    CHECK (locator_partial IN (0, 1)),
    CHECK (forbidden_section_count >= 0),
    CHECK (unknown_scripture_book_count >= 0),
    CHECK (unverified_target_count >= 0),
    CHECK (entry_without_source_span_count >= 0),
    CHECK (legacy_inferred_taxonomy_count >= 0),
    CHECK (malformed_cited_location_count >= 0)
);

CREATE TABLE IF NOT EXISTS alphabetical_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text TEXT NOT NULL,
    language TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    section_kind TEXT NOT NULL DEFAULT '',
    sample_context TEXT,
    translated_text TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_translations_source_language_kind
    ON alphabetical_translations(source_text, language, source_kind, section_kind);
CREATE INDEX IF NOT EXISTS idx_alpha_translations_language
    ON alphabetical_translations(language);
CREATE INDEX IF NOT EXISTS idx_alpha_translations_model_name
    ON alphabetical_translations(model_name);
CREATE INDEX IF NOT EXISTS idx_alpha_translations_source_kind
    ON alphabetical_translations(source_kind, section_kind);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    _migrate_translation_schema(con)
    _migrate_scripture_material_links(con)
    _migrate_ref_locator_status(con)
    _migrate_scripture_book_keys(con)
    migrated_v8_fields = _migrate_v8_field_semantics(con)
    _migrate_quality_schema(con)
    con.executescript(SCHEMA_SQL)
    if migrated_v8_fields:
        backfill_locator_evidence(con)
    con.execute(
        """INSERT INTO alphabetical_schema_meta (meta_key, meta_value, updated_at)
        VALUES ('schema_version', ?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET
            meta_value = excluded.meta_value,
            updated_at = excluded.updated_at""",
        (str(ALPHABETICAL_DB_SCHEMA_VERSION), now_iso()),
    )
    stale_quality_rows = con.execute(
        """SELECT volume_id
        FROM alphabetical_volume_quality
        WHERE db_schema_version < ?
        ORDER BY volume_id""",
        (ALPHABETICAL_DB_SCHEMA_VERSION,),
    ).fetchall()
    for row in stale_quality_rows:
        refresh_volume_quality(con, str(row["volume_id"]))
    con.commit()


def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _add_column_if_missing(
    con: sqlite3.Connection,
    table_name: str,
    column_name: str,
    declaration: str,
) -> None:
    if column_name in _table_columns(con, table_name):
        return
    con.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
    )


def _positive_int_literal(value: object) -> int | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]+", text):
        return None
    number = int(text)
    return number if number >= 1 else None


EDITORIAL_NUMERIC_GLYPH_TRANSLATION = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉₋⁰¹²³⁴⁵⁶⁷⁸⁹⁻",
    "0123456789-0123456789-",
)


def canonical_ref_location(ref: dict[str, object]) -> dict[str, object | None]:
    """Return unambiguous cited-editorial endpoints from v1 or v8 fields.

    Editorial numbers are printed locators. OCR file paths are deliberately not
    inferred here: they belong to the separate target fields.
    """

    start_raw = ref.get("cited_editorial_page_start_raw")
    start_number = ref.get("cited_editorial_page_start_number")
    end_raw = ref.get("cited_editorial_page_end_raw")
    end_number = ref.get("cited_editorial_page_end_number")
    column_raw = ref.get("cited_editorial_column_raw")
    line_raw = ref.get("cited_editorial_line_raw")
    line_start_raw = ref.get("cited_editorial_line_start_raw")
    line_start_number = ref.get("cited_editorial_line_start_number")
    line_end_raw = ref.get("cited_editorial_line_end_raw")
    line_end_number = ref.get("cited_editorial_line_end_number")

    ref_kind = str(ref.get("ref_kind") or "")
    if start_raw is None:
        if ref_kind == "editorial_range":
            start_raw = ref.get("range_start_raw") or ref.get("page_ref_raw")
        else:
            start_raw = ref.get("page_ref_raw")
    if start_number is None:
        start_number = ref.get("page_ref_int")
    if start_number is None:
        start_number = _positive_int_literal(start_raw)
    if ref_kind == "editorial_range":
        if end_raw is None:
            end_raw = ref.get("range_end_raw")
        if end_number is None:
            end_number = _positive_int_literal(end_raw)
    if column_raw is None:
        column_raw = ref.get("page_ref_col")
    if line_raw is None:
        line_raw = ref.get("line_ref_raw")

    line_text = str(line_raw or "").strip().translate(
        EDITORIAL_NUMERIC_GLYPH_TRANSLATION
    )
    if line_text and line_start_number is None:
        cross_page = re.fullmatch(
            r"\D*(\d+)\s*[-–—]\s*(\d+)\s*[,_.:]\s*(\d+)\D*",
            line_text,
        )
        same_page_range = re.fullmatch(
            r"\D*(\d+)\s*[-–—]\s*(\d+)\D*",
            line_text,
        )
        single_line = re.fullmatch(r"\D*(\d+)\D*", line_text)
        if cross_page:
            line_start_raw = line_start_raw or cross_page.group(1)
            line_start_number = int(cross_page.group(1))
            end_raw = end_raw or cross_page.group(2)
            end_number = end_number or int(cross_page.group(2))
            line_end_raw = line_end_raw or cross_page.group(3)
            line_end_number = int(cross_page.group(3))
        elif same_page_range:
            line_start_raw = line_start_raw or same_page_range.group(1)
            line_start_number = int(same_page_range.group(1))
            line_end_raw = line_end_raw or same_page_range.group(2)
            line_end_number = int(same_page_range.group(2))
            end_raw = end_raw or start_raw
            end_number = end_number or start_number
        elif single_line:
            line_start_raw = line_start_raw or single_line.group(1)
            line_start_number = int(single_line.group(1))

    if ref_kind in {"target_locator", "parallel_locator", "unresolved"} and not (
        start_raw or line_raw or column_raw
    ):
        parse_status = "not_applicable"
    elif ref_kind == "editorial_page_line":
        parse_status = (
            "parsed" if start_number is not None and line_start_number is not None
            else "partial" if start_raw is not None or line_raw is not None
            else "unparsed"
        )
    elif ref_kind == "editorial_range":
        parse_status = (
            "parsed" if start_number is not None and end_number is not None
            else "partial" if start_raw is not None or end_raw is not None
            else "unparsed"
        )
    else:
        parse_status = (
            "parsed" if start_number is not None
            else "partial" if start_raw is not None or column_raw is not None
            else "unparsed"
        )

    return {
        "cited_editorial_page_start_raw": (
            str(start_raw) if start_raw is not None else None
        ),
        "cited_editorial_page_start_number": start_number,
        "cited_editorial_page_end_raw": (
            str(end_raw) if end_raw is not None else None
        ),
        "cited_editorial_page_end_number": end_number,
        "cited_editorial_column_raw": (
            str(column_raw) if column_raw is not None else None
        ),
        "cited_editorial_line_raw": (
            str(line_raw) if line_raw is not None else None
        ),
        "cited_editorial_line_start_raw": (
            str(line_start_raw) if line_start_raw is not None else None
        ),
        "cited_editorial_line_start_number": line_start_number,
        "cited_editorial_line_end_raw": (
            str(line_end_raw) if line_end_raw is not None else None
        ),
        "cited_editorial_line_end_number": line_end_number,
        "cited_location_parse_status": parse_status,
    }


def _migrate_v8_field_semantics(con: sqlite3.Connection) -> bool:
    migrated = False
    if _table_exists(con, "alphabetical_sections"):
        section_columns = {
            "index_editorial_page_start": "INTEGER",
            "index_editorial_page_end": "INTEGER",
            "index_ocr_file_start": "TEXT",
            "index_ocr_file_end": "TEXT",
            "pipeline_owner": "TEXT NOT NULL DEFAULT 'alphabetical'",
            "alphabetical_role": "TEXT NOT NULL DEFAULT 'owned_section'",
            "material_reference_mode": "TEXT NOT NULL DEFAULT 'remissive'",
            "scripture_mode": "TEXT NOT NULL DEFAULT 'none'",
            "taxonomy_source": "TEXT NOT NULL DEFAULT 'legacy_inferred'",
        }
        section_needs_backfill = any(
            name not in _table_columns(con, "alphabetical_sections")
            for name in section_columns
        )
        migrated = migrated or section_needs_backfill
        for name, declaration in section_columns.items():
            _add_column_if_missing(
                con, "alphabetical_sections", name, declaration
            )
        if section_needs_backfill:
            con.execute(
            """UPDATE alphabetical_sections
            SET index_editorial_page_start = COALESCE(
                    index_editorial_page_start, page_start
                ),
                index_editorial_page_end = COALESCE(
                    index_editorial_page_end, page_end
                ),
                index_ocr_file_start = COALESCE(
                    index_ocr_file_start, file_start
                ),
                index_ocr_file_end = COALESCE(index_ocr_file_end, file_end),
                pipeline_owner = CASE
                    WHEN json_valid(raw_json)
                     AND json_extract(raw_json, '$.pipeline_owner') IN (
                        'alphabetical', 'general'
                     ) THEN json_extract(raw_json, '$.pipeline_owner')
                    WHEN section_kind IN ('ordo_rerum', 'editorial_closure')
                        THEN 'general'
                    ELSE pipeline_owner
                END,
                alphabetical_role = CASE
                    WHEN json_valid(raw_json)
                     AND json_extract(raw_json, '$.alphabetical_role') IN (
                        'owned_section', 'stop_boundary'
                     ) THEN json_extract(raw_json, '$.alphabetical_role')
                    WHEN section_kind IN ('ordo_rerum', 'editorial_closure')
                        THEN 'stop_boundary'
                    ELSE alphabetical_role
                END,
                material_reference_mode = CASE
                    WHEN json_valid(raw_json)
                     AND json_extract(
                        raw_json, '$.material_reference_mode'
                     ) IN (
                        'remissive', 'parallel', 'source_only', 'boundary_only'
                     ) THEN json_extract(
                        raw_json, '$.material_reference_mode'
                     )
                    WHEN section_kind IN ('ordo_rerum', 'editorial_closure')
                        THEN 'boundary_only'
                    WHEN section_kind = 'concordance_index' THEN 'parallel'
                    ELSE material_reference_mode
                END,
                scripture_mode = CASE
                    WHEN json_valid(raw_json)
                     AND json_extract(raw_json, '$.scripture_mode') IN (
                        'none', 'citation_index', 'pericope_index',
                        'concordance_component', 'textual_apparatus',
                        'incidental_mention'
                     ) THEN json_extract(raw_json, '$.scripture_mode')
                    WHEN section_kind = 'scripture_index' THEN 'citation_index'
                    WHEN section_kind = 'pericope_index' THEN 'pericope_index'
                    WHEN section_kind = 'concordance_index'
                        THEN 'concordance_component'
                    ELSE scripture_mode
                END,
                taxonomy_source = CASE
                    WHEN json_valid(raw_json)
                     AND json_extract(raw_json, '$.pipeline_owner') IS NOT NULL
                     AND json_extract(raw_json, '$.alphabetical_role') IS NOT NULL
                     AND json_extract(
                        raw_json, '$.material_reference_mode'
                     ) IS NOT NULL
                     AND json_extract(raw_json, '$.scripture_mode') IS NOT NULL
                        THEN 'explicit'
                    ELSE 'legacy_inferred'
                END"""
            )

    if _table_exists(con, "alphabetical_entries"):
        entry_columns = {
            "index_editorial_page_estimate": "INTEGER",
            "index_section_start_ocr_file": "TEXT",
            "index_entry_source_ocr_file": "TEXT",
            "resolved_target_ocr_file": "TEXT",
        }
        entry_needs_backfill = any(
            name not in _table_columns(con, "alphabetical_entries")
            for name in entry_columns
        )
        migrated = migrated or entry_needs_backfill
        for name, declaration in entry_columns.items():
            _add_column_if_missing(
                con, "alphabetical_entries", name, declaration
            )
        if entry_needs_backfill:
            con.execute(
            """UPDATE alphabetical_entries
            SET index_editorial_page_estimate = COALESCE(
                    index_editorial_page_estimate, inferred_printed_page
                ),
                index_section_start_ocr_file = COALESCE(
                    index_section_start_ocr_file, section_start_file
                ),
                index_entry_source_ocr_file = COALESCE(
                    index_entry_source_ocr_file, editorial_anchor_file
                ),
                resolved_target_ocr_file = COALESCE(
                    resolved_target_ocr_file, target_file_best
                )"""
            )

    if not _table_exists(con, "alphabetical_refs"):
        return migrated
    ref_columns = {
        "cited_editorial_page_start_raw": "TEXT",
        "cited_editorial_page_start_number": "INTEGER",
        "cited_editorial_page_end_raw": "TEXT",
        "cited_editorial_page_end_number": "INTEGER",
        "cited_editorial_column_raw": "TEXT",
        "cited_editorial_line_raw": "TEXT",
        "cited_editorial_line_start_raw": "TEXT",
        "cited_editorial_line_start_number": "INTEGER",
        "cited_editorial_line_end_raw": "TEXT",
        "cited_editorial_line_end_number": "INTEGER",
        "resolved_target_ocr_file": "TEXT",
        "target_ocr_file_candidate_score": "REAL",
        "cited_location_parse_status": "TEXT NOT NULL DEFAULT 'unparsed'",
    }
    ref_needs_backfill = any(
        name not in _table_columns(con, "alphabetical_refs")
        for name in ref_columns
    )
    migrated = migrated or ref_needs_backfill
    for name, declaration in ref_columns.items():
        _add_column_if_missing(con, "alphabetical_refs", name, declaration)
    if not ref_needs_backfill:
        return migrated
    rows = con.execute(
        """SELECT ref_id, ref_kind, page_ref_raw, page_ref_int,
                  page_ref_col, line_ref_raw, range_start_raw, range_end_raw,
                  cited_editorial_page_start_raw,
                  cited_editorial_page_start_number,
                  cited_editorial_page_end_raw,
                  cited_editorial_page_end_number,
                  cited_editorial_column_raw, cited_editorial_line_raw,
                  cited_editorial_line_start_raw,
                  cited_editorial_line_start_number,
                  cited_editorial_line_end_raw,
                  cited_editorial_line_end_number
        FROM alphabetical_refs"""
    ).fetchall()
    repairs = []
    for row in rows:
        location = canonical_ref_location(dict(row))
        repairs.append(
            (
                *location.values(),
                int(row["ref_id"]),
            )
        )
    con.executemany(
        """UPDATE alphabetical_refs
        SET cited_editorial_page_start_raw = ?,
            cited_editorial_page_start_number = ?,
            cited_editorial_page_end_raw = ?,
            cited_editorial_page_end_number = ?,
            cited_editorial_column_raw = ?,
            cited_editorial_line_raw = ?,
            cited_editorial_line_start_raw = ?,
            cited_editorial_line_start_number = ?,
            cited_editorial_line_end_raw = ?,
            cited_editorial_line_end_number = ?,
            cited_location_parse_status = ?,
            resolved_target_ocr_file = COALESCE(
                resolved_target_ocr_file, target_file
            ),
            target_ocr_file_candidate_score = COALESCE(
                target_ocr_file_candidate_score, target_file_probability
            )
        WHERE ref_id = ?""",
        repairs,
    )
    return migrated


def backfill_locator_evidence(con: sqlite3.Connection) -> None:
    if not all(
        _table_exists(con, table_name)
        for table_name in ("alphabetical_refs", "alphabetical_locator_evidence")
    ):
        return
    rows = con.execute(
        """SELECT ref_id, raw_json
        FROM alphabetical_refs
        WHERE json_valid(raw_json)
          AND json_type(raw_json, '$.compact_locator.evidence') = 'array'"""
    ).fetchall()
    inserts: list[tuple[object, ...]] = []
    for row in rows:
        raw = json.loads(str(row["raw_json"]))
        locator = raw.get("compact_locator")
        evidence = locator.get("evidence") if isinstance(locator, dict) else []
        if not isinstance(evidence, list):
            continue
        for order, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                continue
            page_raw = item.get("editorial_page")
            page_number = _positive_int_literal(page_raw)
            weight = item.get("weight")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                weight = None
            inserts.append(
                (
                    int(row["ref_id"]),
                    order,
                    str(item.get("kind") or "unspecified"),
                    str(item["file"]) if item.get("file") else None,
                    str(page_raw) if page_raw is not None else None,
                    page_number,
                    str(item["detail"]) if item.get("detail") else None,
                    weight,
                    json.dumps(item, ensure_ascii=False, sort_keys=True),
                )
            )
    con.executemany(
        """INSERT OR IGNORE INTO alphabetical_locator_evidence (
            ref_id, evidence_order, evidence_kind, evidence_ocr_file_path,
            observed_editorial_page_raw, observed_editorial_page_number,
            detail, weight, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        inserts,
    )


def _migrate_scripture_material_links(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "alphabetical_refs"):
        return

    columns = _table_columns(con, "alphabetical_refs")
    if "scripture_ref_order" not in columns:
        con.execute("ALTER TABLE alphabetical_refs ADD COLUMN scripture_ref_order INTEGER")

    if not _table_exists(con, "alphabetical_scripture_refs"):
        return

    duplicate = con.execute(
        """SELECT entry_key, ref_order
        FROM alphabetical_scripture_refs
        GROUP BY entry_key, ref_order
        HAVING COUNT(*) > 1
        LIMIT 1"""
    ).fetchone()
    if duplicate is not None:
        raise sqlite3.IntegrityError(
            "Cannot migrate scripture/material links: duplicate "
            f"alphabetical_scripture_refs parent ({duplicate['entry_key']!r}, "
            f"{duplicate['ref_order']!r})."
        )

    con.execute("DROP INDEX IF EXISTS idx_alpha_scripture_entry_order_role")
    con.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_scripture_entry_order
        ON alphabetical_scripture_refs(entry_key, ref_order)"""
    )

    con.execute(
        """UPDATE alphabetical_refs
        SET scripture_ref_order = (
            SELECT MIN(sr.ref_order)
            FROM alphabetical_scripture_refs sr
            WHERE sr.entry_key = alphabetical_refs.entry_key
        )
        WHERE scripture_ref_order IS NULL
          AND (
              SELECT COUNT(*)
              FROM alphabetical_scripture_refs sr
              WHERE sr.entry_key = alphabetical_refs.entry_key
          ) = 1"""
    )


def _migrate_ref_locator_status(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "alphabetical_refs"):
        return
    if "locator_status" in _table_columns(con, "alphabetical_refs"):
        _reconcile_ref_locator_status(con)
        return

    con.execute(
        """
        CREATE TABLE alphabetical_refs_v7 (
            ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL
                REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
            ref_order INTEGER NOT NULL,
            scripture_ref_order INTEGER,
            ref_kind TEXT NOT NULL CHECK (
                ref_kind IN (
                    'editorial_page', 'editorial_column',
                    'editorial_page_column', 'editorial_range',
                    'editorial_page_line', 'target_locator', 'scripture',
                    'parallel_locator', 'unresolved'
                )
            ),
            ref_raw TEXT NOT NULL,
            page_ref_raw TEXT,
            page_ref_int INTEGER,
            page_ref_col TEXT,
            line_ref_raw TEXT,
            range_start_raw TEXT,
            range_end_raw TEXT,
            target_file TEXT,
            target_file_probability REAL,
            locator_status TEXT NOT NULL DEFAULT 'unverified' CHECK (
                locator_status IN (
                    'resolved', 'ambiguous', 'unresolved', 'unverified'
                )
            ),
            section_start_file TEXT,
            editorial_anchor_file TEXT,
            confidence REAL,
            raw_json TEXT NOT NULL,
            CHECK (ref_order >= 1),
            CHECK (scripture_ref_order IS NULL OR scripture_ref_order >= 1),
            CHECK (
                page_ref_raw IS NOT NULL
                OR target_file IS NOT NULL
                OR range_start_raw IS NOT NULL
                OR range_end_raw IS NOT NULL
                OR locator_status IN ('unresolved', 'unverified')
            ),
            CHECK (
                locator_status != 'resolved'
                OR (target_file IS NOT NULL AND TRIM(target_file) != '')
            ),
            CHECK (
                target_file_probability IS NULL
                OR (
                    target_file_probability >= 0.0
                    AND target_file_probability <= 1.0
                )
            ),
            CHECK (
                confidence IS NULL
                OR (confidence >= 0.0 AND confidence <= 1.0)
            )
        )
        """
    )
    con.execute(
        """
        INSERT INTO alphabetical_refs_v7 (
            ref_id, entry_key, ref_order, scripture_ref_order, ref_kind,
            ref_raw, page_ref_raw, page_ref_int, page_ref_col, line_ref_raw,
            range_start_raw, range_end_raw, target_file,
            target_file_probability, locator_status, section_start_file,
            editorial_anchor_file, confidence, raw_json
        )
        SELECT
            ref_id, entry_key, ref_order, scripture_ref_order, ref_kind,
            ref_raw, page_ref_raw, page_ref_int, page_ref_col, line_ref_raw,
            range_start_raw, range_end_raw, target_file,
            target_file_probability,
            CASE
                WHEN target_file IS NULL OR TRIM(target_file) = ''
                    THEN 'unresolved'
                WHEN json_valid(raw_json)
                 AND json_extract(
                        raw_json, '$.compact_locator.status'
                     ) = 'resolved'
                 AND EXISTS (
                    SELECT 1
                    FROM json_each(
                        CASE
                            WHEN json_valid(raw_json)
                                THEN COALESCE(
                                    json_extract(
                                        raw_json,
                                        '$.compact_locator.evidence'
                                    ),
                                    '[]'
                                )
                            ELSE '[]'
                        END
                    ) evidence
                    WHERE json_extract(evidence.value, '$.kind') IN (
                        'cited_page_match',
                        'direct_editorial_page',
                        'editorial_header_match',
                        'editorial_page_match',
                        'header_pair',
                        'neighbor_fit',
                        'neighbor_sequence',
                        'page_number_match',
                        'pagination_sequence'
                    )
                 )
                    THEN 'resolved'
                ELSE 'unverified'
            END,
            section_start_file, editorial_anchor_file, confidence, raw_json
        FROM alphabetical_refs
        """
    )
    con.execute("DROP TABLE alphabetical_refs")
    con.execute("ALTER TABLE alphabetical_refs_v7 RENAME TO alphabetical_refs")
    _reconcile_ref_locator_status(con)


def _reconcile_ref_locator_status(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE alphabetical_refs
        SET locator_status = CASE
            WHEN EXISTS (
                SELECT 1
                FROM json_each(
                    CASE
                        WHEN json_valid(alphabetical_refs.raw_json)
                            THEN COALESCE(
                                json_extract(
                                    alphabetical_refs.raw_json,
                                    '$.compact_locator.evidence'
                                ),
                                '[]'
                            )
                        ELSE '[]'
                    END
                ) evidence
                WHERE json_extract(evidence.value, '$.kind') IN (
                    'cited_page_match',
                    'direct_editorial_page',
                    'editorial_header_match',
                    'editorial_page_match',
                    'header_pair',
                    'neighbor_fit',
                    'neighbor_sequence',
                    'page_number_match',
                    'pagination_sequence'
                )
            )
             AND json_valid(alphabetical_refs.raw_json)
             AND json_extract(
                    alphabetical_refs.raw_json,
                    '$.compact_locator.status'
                 ) = 'resolved'
                THEN 'resolved'
            ELSE 'unverified'
        END
        WHERE target_file IS NOT NULL
          AND TRIM(target_file) != ''
        """
    )


def _migrate_scripture_book_keys(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "alphabetical_scripture_refs"):
        return
    columns = _table_columns(con, "alphabetical_scripture_refs")
    if "book_key" not in columns:
        con.execute("ALTER TABLE alphabetical_scripture_refs ADD COLUMN book_key TEXT")
    if not all(
        _table_exists(con, table_name)
        for table_name in (
            "alphabetical_entries",
            "alphabetical_sections",
            "alphabetical_volumes",
        )
    ):
        return
    rows = con.execute(
        """SELECT sr.scripture_ref_id, sr.book_raw, sr.book_norm, sr.book_key,
                  sr.raw_json, s.section_key, s.raw_json AS section_raw_json,
                  v.collection
        FROM alphabetical_scripture_refs sr
        JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        JOIN alphabetical_volumes v ON v.volume_id = s.volume_id"""
    ).fetchall()
    po_old_english_sections = {
        str(row["section_key"])
        for row in rows
        if row["collection"] == "PO"
        and re.search(
            r"\b(?:iii|iv|3|4)\s+kings\b",
            " ".join(
                normalize_book_alias(row[field])
                for field in ("book_raw", "book_norm")
            ),
        )
    }
    repairs = []
    for row in rows:
        section_raw = {}
        try:
            section_raw = json.loads(str(row["section_raw_json"] or "{}"))
        except json.JSONDecodeError:
            pass
        local_profile = (
            section_raw.get("scripture_numbering_profile")
            if isinstance(section_raw, dict)
            else None
        )
        tradition = contextual_book_tradition(
            row["collection"],
            row["book_raw"],
            row["book_norm"],
            local_profile=local_profile,
            section_uses_old_english=(
                str(row["section_key"]) in po_old_english_sections
            ),
        )
        key = canonical_book_key(
            row["book_raw"],
            tradition=tradition,
        ) or canonical_book_key(
            row["book_norm"],
            tradition=tradition,
        )
        raw_json = {}
        try:
            raw_json = json.loads(str(row["raw_json"] or "{}"))
        except json.JSONDecodeError:
            pass
        historical_key = (
            historical_noncanonical_book_key(row["book_raw"])
            or historical_noncanonical_book_key(row["book_norm"])
        )
        if key is None and historical_key and isinstance(raw_json, dict):
            raw_json.setdefault("canonical_status", "historical_noncanonical")
            raw_json.setdefault("historical_book_key", historical_key)
        if key != row["book_key"] or (
            historical_key and json.dumps(raw_json, ensure_ascii=False) != row["raw_json"]
        ):
            repairs.append(
                (
                    key,
                    json.dumps(raw_json, ensure_ascii=False, sort_keys=True),
                    int(row["scripture_ref_id"]),
                )
            )
    con.executemany(
        """UPDATE alphabetical_scripture_refs
        SET book_key = ?, raw_json = ?
        WHERE scripture_ref_id = ?""",
        repairs,
    )


def _migrate_quality_schema(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "alphabetical_volume_quality"):
        return
    columns = _table_columns(con, "alphabetical_volume_quality")
    if "status" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN status TEXT NOT NULL DEFAULT 'partial'
            CHECK (status IN ('valid', 'partial', 'needs_reextract'))"""
        )
    if "unresolved_material_ref_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN unresolved_material_ref_count INTEGER NOT NULL DEFAULT 0
            CHECK (unresolved_material_ref_count >= 0)"""
        )
    if "locator_partial" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN locator_partial INTEGER NOT NULL DEFAULT 0
            CHECK (locator_partial IN (0, 1))"""
        )
    if "forbidden_section_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN forbidden_section_count INTEGER NOT NULL DEFAULT 0
            CHECK (forbidden_section_count >= 0)"""
        )
    if "unknown_scripture_book_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN unknown_scripture_book_count INTEGER NOT NULL DEFAULT 0
            CHECK (unknown_scripture_book_count >= 0)"""
        )
    if "unverified_target_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN unverified_target_count INTEGER NOT NULL DEFAULT 0
            CHECK (unverified_target_count >= 0)"""
        )
    if "entry_without_source_span_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN entry_without_source_span_count INTEGER NOT NULL DEFAULT 0
            CHECK (entry_without_source_span_count >= 0)"""
        )
    if "legacy_inferred_taxonomy_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN legacy_inferred_taxonomy_count INTEGER NOT NULL DEFAULT 0
            CHECK (legacy_inferred_taxonomy_count >= 0)"""
        )
    if "malformed_cited_location_count" not in columns:
        con.execute(
            """ALTER TABLE alphabetical_volume_quality
            ADD COLUMN malformed_cited_location_count INTEGER NOT NULL DEFAULT 0
            CHECK (malformed_cited_location_count >= 0)"""
        )


def _translation_columns(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("PRAGMA table_info(alphabetical_translations)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_translation_schema(con: sqlite3.Connection) -> None:
    table_exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alphabetical_translations'"
    ).fetchone()
    if table_exists is None:
        return

    columns = _translation_columns(con)
    required = {
        "id",
        "source_text",
        "language",
        "source_kind",
        "section_kind",
        "sample_context",
        "translated_text",
        "model_name",
        "created_at",
        "updated_at",
    }
    if required.issubset(columns):
        return

    con.execute("DROP INDEX IF EXISTS idx_alpha_translations_source_language")
    con.execute("DROP INDEX IF EXISTS idx_alpha_translations_source_language_kind")
    con.execute("DROP INDEX IF EXISTS idx_alpha_translations_language")
    con.execute("DROP INDEX IF EXISTS idx_alpha_translations_model_name")
    con.execute("DROP INDEX IF EXISTS idx_alpha_translations_source_kind")
    con.execute("ALTER TABLE alphabetical_translations RENAME TO alphabetical_translations_legacy")
    con.execute(
        """
        CREATE TABLE alphabetical_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT NOT NULL,
            language TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            section_kind TEXT NOT NULL DEFAULT '',
            sample_context TEXT,
            translated_text TEXT NOT NULL,
            model_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT INTO alphabetical_translations (
            id, source_text, language, source_kind, section_kind, sample_context,
            translated_text, model_name, created_at, updated_at
        )
        SELECT
            id,
            source_text,
            language,
            'legacy' AS source_kind,
            '' AS section_kind,
            NULL AS sample_context,
            translated_text,
            model_name,
            created_at,
            updated_at
        FROM alphabetical_translations_legacy
        """
    )
    con.execute("DROP TABLE alphabetical_translations_legacy")
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_translations_source_language_kind
            ON alphabetical_translations(source_text, language, source_kind, section_kind)
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_alpha_translations_language ON alphabetical_translations(language)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_alpha_translations_model_name ON alphabetical_translations(model_name)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_alpha_translations_source_kind ON alphabetical_translations(source_kind, section_kind)"
    )


def canonicalize_translation_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def clear_volume(con: sqlite3.Connection, volume_id: str) -> None:
    con.execute("DELETE FROM alphabetical_volumes WHERE volume_id = ?", (volume_id,))


def refresh_volume_quality(con: sqlite3.Connection, volume_id: str) -> dict[str, int]:
    row = con.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM alphabetical_entries e
                WHERE e.section_key IN (
                    SELECT section_key
                    FROM alphabetical_sections
                    WHERE volume_id = ?
                )
                  AND e.entry_kind IN ('scripture_citation', 'scripture_pericope')
            ) AS scripture_entry_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_scripture_refs sr
                JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
            ) AS scripture_ref_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
            ) AS material_ref_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
                  AND r.scripture_ref_order IS NOT NULL
            ) AS linked_material_ref_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
                  AND r.scripture_ref_order IS NULL
                  AND EXISTS (
                      SELECT 1
                      FROM alphabetical_scripture_refs sr
                      WHERE sr.entry_key = r.entry_key
                  )
            ) AS unlinked_scripture_material_ref_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                LEFT JOIN alphabetical_scripture_refs sr
                  ON sr.entry_key = r.entry_key
                 AND sr.ref_order = r.scripture_ref_order
                WHERE s.volume_id = ?
                  AND r.scripture_ref_order IS NOT NULL
                  AND sr.scripture_ref_id IS NULL
            ) AS dangling_scripture_link_count,
            (
                SELECT COUNT(*)
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
                  AND r.target_file IS NULL
            ) AS unresolved_material_ref_count
        """,
        (volume_id,) * 7,
    ).fetchone()
    metrics = {key: int(row[key] or 0) for key in row.keys()}
    forbidden_section_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_sections
        WHERE volume_id = ?
          AND section_kind IN ('ordo_rerum', 'editorial_closure')""",
        (volume_id,),
    ).fetchone()
    metrics["forbidden_section_count"] = int(
        forbidden_section_row["count"] or 0
    )
    unknown_scripture_book_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_scripture_refs sr
        JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
          AND sr.book_key IS NULL
          AND NOT (
              json_valid(sr.raw_json)
              AND json_extract(
                  sr.raw_json, '$.canonical_status'
              ) = 'historical_noncanonical'
          )""",
        (volume_id,),
    ).fetchone()
    metrics["unknown_scripture_book_count"] = int(
        unknown_scripture_book_row["count"] or 0
    )
    unverified_target_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_refs r
        JOIN alphabetical_entries e ON e.entry_key = r.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
          AND r.target_file IS NOT NULL
          AND NOT (
              CASE
                  WHEN json_valid(r.raw_json) THEN
                      json_extract(
                          r.raw_json,
                          '$.compact_locator.status'
                      ) = 'resolved'
                      AND EXISTS (
                          SELECT 1
                          FROM json_each(
                              r.raw_json,
                              '$.compact_locator.evidence'
                          ) evidence
                          WHERE lower(
                              json_extract(evidence.value, '$.kind')
                          ) IN (
                              'cited_page_match',
                              'direct_editorial_page',
                              'editorial_header_match',
                              'editorial_page_match',
                              'header_pair',
                              'neighbor_fit',
                              'neighbor_sequence',
                              'page_number_match',
                              'pagination_sequence'
                          )
                      )
                  ELSE 0
              END
          )""",
        (volume_id,),
    ).fetchone()
    metrics["unverified_target_count"] = int(
        unverified_target_row["count"] or 0
    )
    entry_without_span_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_entries e
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM alphabetical_entry_source_spans ess
              WHERE ess.entry_key = e.entry_key
          )""",
        (volume_id,),
    ).fetchone()
    metrics["entry_without_source_span_count"] = int(
        entry_without_span_row["count"] or 0
    )
    inferred_taxonomy_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_sections
        WHERE volume_id = ? AND taxonomy_source = 'legacy_inferred'""",
        (volume_id,),
    ).fetchone()
    metrics["legacy_inferred_taxonomy_count"] = int(
        inferred_taxonomy_row["count"] or 0
    )
    malformed_location_row = con.execute(
        """SELECT COUNT(*) AS count
        FROM alphabetical_refs r
        JOIN alphabetical_entries e ON e.entry_key = r.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
          AND r.ref_kind NOT IN (
              'target_locator', 'parallel_locator', 'unresolved', 'scripture'
          )
          AND r.cited_location_parse_status = 'unparsed'""",
        (volume_id,),
    ).fetchone()
    metrics["malformed_cited_location_count"] = int(
        malformed_location_row["count"] or 0
    )
    coverage: dict[str, object] = {}
    run_row = con.execute(
        """SELECT raw_json
        FROM alphabetical_runs
        WHERE volume_id = ? AND status = 'imported'
        ORDER BY run_id DESC
        LIMIT 1""",
        (volume_id,),
    ).fetchone()
    if run_row is not None:
        try:
            run_payload = json.loads(str(run_row["raw_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            run_payload = {}
        if isinstance(run_payload, dict) and isinstance(
            run_payload.get("coverage"), dict
        ):
            coverage = run_payload["coverage"]
    entries_status = str(coverage.get("entries_status") or "").strip()
    locator_partial = (
        str(coverage.get("locator_status") or "").strip() == "partial"
        or entries_status.startswith("partial")
    )
    metrics["locator_partial"] = int(locator_partial)
    metrics["coverage_unrecoverable_ocr"] = int(
        entries_status == "unrecoverable_ocr"
    )
    if (
        metrics["dangling_scripture_link_count"]
        or metrics["coverage_unrecoverable_ocr"]
        or metrics["forbidden_section_count"]
        or metrics["unknown_scripture_book_count"]
        or metrics["malformed_cited_location_count"]
    ):
        status = "needs_reextract"
    elif (
        metrics["unlinked_scripture_material_ref_count"]
        or metrics["unresolved_material_ref_count"]
        or metrics["locator_partial"]
        or metrics["unverified_target_count"]
    ):
        status = "partial"
    else:
        status = "valid"
    computed_at = now_iso()
    con.execute(
        """INSERT INTO alphabetical_volume_quality (
            volume_id, db_schema_version, status, scripture_entry_count, scripture_ref_count,
            material_ref_count, linked_material_ref_count,
            unlinked_scripture_material_ref_count, dangling_scripture_link_count,
            unresolved_material_ref_count, locator_partial,
            forbidden_section_count, unknown_scripture_book_count,
            unverified_target_count, computed_at, raw_json
            , entry_without_source_span_count,
            legacy_inferred_taxonomy_count,
            malformed_cited_location_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
            db_schema_version = excluded.db_schema_version,
            status = excluded.status,
            scripture_entry_count = excluded.scripture_entry_count,
            scripture_ref_count = excluded.scripture_ref_count,
            material_ref_count = excluded.material_ref_count,
            linked_material_ref_count = excluded.linked_material_ref_count,
            unlinked_scripture_material_ref_count = excluded.unlinked_scripture_material_ref_count,
            dangling_scripture_link_count = excluded.dangling_scripture_link_count,
            unresolved_material_ref_count = excluded.unresolved_material_ref_count,
            locator_partial = excluded.locator_partial,
            forbidden_section_count = excluded.forbidden_section_count,
            unknown_scripture_book_count = excluded.unknown_scripture_book_count,
            unverified_target_count = excluded.unverified_target_count,
            entry_without_source_span_count = excluded.entry_without_source_span_count,
            legacy_inferred_taxonomy_count = excluded.legacy_inferred_taxonomy_count,
            malformed_cited_location_count = excluded.malformed_cited_location_count,
            computed_at = excluded.computed_at,
            raw_json = excluded.raw_json""",
        (
            volume_id,
            ALPHABETICAL_DB_SCHEMA_VERSION,
            status,
            metrics["scripture_entry_count"],
            metrics["scripture_ref_count"],
            metrics["material_ref_count"],
            metrics["linked_material_ref_count"],
            metrics["unlinked_scripture_material_ref_count"],
            metrics["dangling_scripture_link_count"],
            metrics["unresolved_material_ref_count"],
            metrics["locator_partial"],
            metrics["forbidden_section_count"],
            metrics["unknown_scripture_book_count"],
            metrics["unverified_target_count"],
            computed_at,
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            metrics["entry_without_source_span_count"],
            metrics["legacy_inferred_taxonomy_count"],
            metrics["malformed_cited_location_count"],
        ),
    )
    return metrics


def get_volume_quality(
    db_path: Path | str,
    volume_id: str,
) -> dict[str, str | int] | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with connect_db(path) as con:
        init_schema(con)
        select_quality_sql = """
            SELECT db_schema_version, status, scripture_entry_count,
                   scripture_ref_count, material_ref_count,
                   linked_material_ref_count,
                   unlinked_scripture_material_ref_count,
                   dangling_scripture_link_count,
                   unresolved_material_ref_count, locator_partial,
                   forbidden_section_count, unknown_scripture_book_count,
                   unverified_target_count,
                   entry_without_source_span_count,
                   legacy_inferred_taxonomy_count,
                   malformed_cited_location_count, computed_at
            FROM alphabetical_volume_quality
            WHERE volume_id = ?
        """
        row = con.execute(select_quality_sql, (volume_id,)).fetchone()
        if row is None:
            volume_exists = con.execute(
                "SELECT 1 FROM alphabetical_volumes WHERE volume_id = ?",
                (volume_id,),
            ).fetchone()
            if volume_exists is None:
                return None
            refresh_volume_quality(con, volume_id)
            con.commit()
            row = con.execute(select_quality_sql, (volume_id,)).fetchone()
        return {key: row[key] for key in row.keys()}


def upsert_volume(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    collection: str,
    source_root: str,
    volume_label: str | None,
    notes: str | None,
) -> None:
    now = now_iso()
    con.execute(
        """INSERT INTO alphabetical_volumes (
            volume_id, collection, source_root, volume_label, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
            collection = excluded.collection,
            source_root = excluded.source_root,
            volume_label = excluded.volume_label,
            notes = excluded.notes,
            updated_at = excluded.updated_at""",
        (volume_id, collection, source_root, volume_label, notes, now, now),
    )


def volume_already_imported(db_path: Path | str, volume_id: str) -> bool:
    path = Path(db_path)
    if not path.exists():
        return False
    with connect_db(path) as con:
        init_schema(con)
        try:
            row = con.execute(
                "SELECT 1 FROM alphabetical_runs WHERE volume_id = ? AND status = 'imported' LIMIT 1",
                (volume_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            init_schema(con)
            row = con.execute(
                "SELECT 1 FROM alphabetical_runs WHERE volume_id = ? AND status = 'imported' LIMIT 1",
                (volume_id,),
            ).fetchone()
        return row is not None


def _append_context(bucket: list[str], value: str | None, *, limit: int = 3) -> None:
    text = canonicalize_translation_text(value)
    if not text or text in bucket or len(bucket) >= limit:
        return
    bucket.append(text)


def collect_volume_translation_candidates(
    con: sqlite3.Connection,
    volume_id: str,
) -> list[dict[str, str | list[str]]]:
    rows = con.execute(
        """
        SELECT 'section_heading' AS source_kind,
               s.heading_raw AS source_text,
               s.section_kind AS section_kind,
               s.heading_raw AS section_heading,
               NULL AS context_raw,
               NULL AS fallback_raw
        FROM alphabetical_sections s
        WHERE s.volume_id = ?

        UNION ALL

        SELECT 'node_label' AS source_kind,
               n.label_raw AS source_text,
               s.section_kind AS section_kind,
               s.heading_raw AS section_heading,
               NULL AS context_raw,
               n.label_raw AS fallback_raw
        FROM alphabetical_nodes n
        JOIN alphabetical_sections s ON s.section_key = n.section_key
        WHERE s.volume_id = ?

        UNION ALL

        SELECT 'entry_title' AS source_kind,
               COALESCE(NULLIF(TRIM(e.lemma_display), ''), NULLIF(TRIM(e.lemma_raw), ''), e.entry_raw) AS source_text,
               s.section_kind AS section_kind,
               s.heading_raw AS section_heading,
               e.context_raw AS context_raw,
               e.entry_raw AS fallback_raw
        FROM alphabetical_entries e
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
        """,
        (volume_id, volume_id, volume_id),
    ).fetchall()

    grouped: OrderedDict[str, dict[str, str | list[str]]] = OrderedDict()
    for row in rows:
        source_text = canonicalize_translation_text(row["source_text"])
        if not source_text:
            continue
        candidate = grouped.setdefault(
            f"{row['source_kind']}::{row['section_kind'] or ''}::{source_text}",
            {
                "source_text": source_text,
                "source_kind": row["source_kind"],
                "section_kind": row["section_kind"] or "",
                "contexts": [],
            },
        )
        contexts = candidate["contexts"]
        assert isinstance(contexts, list)
        for value in (row["section_heading"], row["section_kind"], row["context_raw"], row["fallback_raw"]):
            text = canonicalize_translation_text(value)
            if not text or text == source_text:
                continue
            _append_context(contexts, text)
    return list(grouped.values())


def collect_pending_volume_translations(
    con: sqlite3.Connection,
    volume_id: str,
    languages: list[str],
) -> list[dict[str, str | list[str]]]:
    normalized_languages = [canonicalize_translation_text(lang).lower() for lang in languages if canonicalize_translation_text(lang)]
    if not normalized_languages:
        return []

    candidates = collect_volume_translation_candidates(con, volume_id)
    if not candidates:
        return []

    identity_keys = [
        (
            str(candidate["source_text"]),
            str(candidate["source_kind"]),
            str(candidate["section_kind"]),
        )
        for candidate in candidates
    ]
    where_parts: list[str] = []
    params: list[str] = []
    for source_text, source_kind, section_kind in identity_keys:
        where_parts.append("(source_text = ? AND source_kind = ? AND section_kind = ?)")
        params.extend([source_text, source_kind, section_kind])
    placeholders_languages = ",".join("?" for _ in normalized_languages)
    rows = con.execute(
        f"""
        SELECT source_text, language, source_kind, section_kind
        FROM alphabetical_translations
        WHERE ({' OR '.join(where_parts)})
          AND language IN ({placeholders_languages})
        """,
        tuple(params) + tuple(normalized_languages),
    ).fetchall()
    existing: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        key = (row["source_text"], row["source_kind"], row["section_kind"] or "")
        existing.setdefault(key, set()).add(row["language"])

    pending: list[dict[str, str | list[str]]] = []
    for candidate in candidates:
        source_text = candidate["source_text"]
        assert isinstance(source_text, str)
        source_kind = str(candidate["source_kind"])
        section_kind = str(candidate["section_kind"])
        existing_languages = existing.get((source_text, source_kind, section_kind), set())
        missing_languages = [lang for lang in normalized_languages if lang not in existing_languages]
        if not missing_languages:
            continue
        pending.append(
            {
                "source_text": source_text,
                "source_kind": source_kind,
                "section_kind": section_kind,
                "contexts": candidate["contexts"],
                "missing_languages": missing_languages,
            }
        )
    return pending


def upsert_translation_rows(
    con: sqlite3.Connection,
    *,
    source_text: str,
    source_kind: str,
    section_kind: str,
    sample_context: str | None,
    translations: dict[str, str],
    model_name: str,
) -> int:
    now = now_iso()
    source_key = canonicalize_translation_text(source_text)
    source_kind_key = canonicalize_translation_text(source_kind)
    section_kind_key = canonicalize_translation_text(section_kind)
    sample_context_key = canonicalize_translation_text(sample_context)
    if not source_key or not source_kind_key:
        return 0
    inserted = 0
    for language, translated_text in translations.items():
        translated_value = canonicalize_translation_text(translated_text)
        if not translated_value:
            continue
        language_key = canonicalize_translation_text(language).lower()
        if not language_key:
            continue
        con.execute(
            """
            INSERT INTO alphabetical_translations (
                source_text, language, source_kind, section_kind, sample_context,
                translated_text, model_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_text, language, source_kind, section_kind) DO UPDATE SET
                sample_context = COALESCE(alphabetical_translations.sample_context, excluded.sample_context),
                translated_text = excluded.translated_text,
                model_name = excluded.model_name,
                updated_at = excluded.updated_at
            """,
            (
                source_key,
                language_key,
                source_kind_key,
                section_kind_key,
                sample_context_key or None,
                translated_value,
                model_name,
                now,
                now,
            ),
        )
        inserted += 1
    con.commit()
    return inserted
