from __future__ import annotations

import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import re

DEFAULT_DB = Path("data/alphabetical_indices.db")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

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
    section_start_file TEXT,
    editorial_anchor_file TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (ref_order >= 1),
    CHECK (
        page_ref_raw IS NOT NULL
        OR target_file IS NOT NULL
        OR range_start_raw IS NOT NULL
        OR range_end_raw IS NOT NULL
    ),
    CHECK (
        target_file_probability IS NULL
        OR (target_file_probability >= 0.0 AND target_file_probability <= 1.0)
    ),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_entry_key
    ON alphabetical_refs(entry_key);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_page_ref_int
    ON alphabetical_refs(page_ref_int);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_kind
    ON alphabetical_refs(ref_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_refs_entry_order
    ON alphabetical_refs(entry_key, ref_order);

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_scripture_entry_order_role
    ON alphabetical_scripture_refs(entry_key, ref_order, ref_role);

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
    con.executescript(SCHEMA_SQL)
    con.commit()


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
