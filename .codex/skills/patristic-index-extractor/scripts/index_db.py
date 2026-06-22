from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path('data/patristic_indices.db')

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS volumes (
    volume_id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    source_root TEXT NOT NULL,
    volume_label TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    work_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
    work_order INTEGER,
    author_raw TEXT,
    title_raw TEXT NOT NULL,
    title_norm TEXT,
    start_page INTEGER,
    end_page INTEGER,
    start_file TEXT,
    end_file TEXT,
    source_section_key TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_works_volume_id ON works(volume_id);
CREATE INDEX IF NOT EXISTS idx_works_order ON works(volume_id, work_order);

CREATE TABLE IF NOT EXISTS index_sections (
    section_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
    work_key TEXT REFERENCES works(work_key) ON DELETE SET NULL,
    scope_kind TEXT NOT NULL,
    index_kind TEXT NOT NULL,
    heading_raw TEXT NOT NULL,
    heading_norm TEXT,
    page_start INTEGER,
    page_end INTEGER,
    file_start TEXT,
    file_end TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sections_volume_id ON index_sections(volume_id);
CREATE INDEX IF NOT EXISTS idx_sections_work_key ON index_sections(work_key);
CREATE INDEX IF NOT EXISTS idx_sections_scope_kind ON index_sections(scope_kind);

CREATE TABLE IF NOT EXISTS index_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_key TEXT NOT NULL REFERENCES index_sections(section_key) ON DELETE CASCADE,
    entry_order INTEGER NOT NULL,
    entry_raw TEXT NOT NULL,
    target_raw TEXT,
    target_file TEXT,
    page_ref_raw TEXT,
    page_ref_int INTEGER,
    page_ref_col TEXT,
    note_raw TEXT,
    normalized_target TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_section_key ON index_entries(section_key);
CREATE INDEX IF NOT EXISTS idx_entries_page_ref_int ON index_entries(page_ref_int);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    notes TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_volume_id ON runs(volume_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA journal_mode = WAL')
    con.execute('PRAGMA synchronous = NORMAL')
    con.execute('PRAGMA busy_timeout = 30000')
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    con.commit()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    value = re.sub(r'-{2,}', '-', value)
    return value.strip('-') or 'item'


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def clear_volume(con: sqlite3.Connection, volume_id: str) -> None:
    con.execute(
        'DELETE FROM index_entries WHERE section_key IN (SELECT section_key FROM index_sections WHERE volume_id = ?)',
        (volume_id,),
    )
    con.execute('DELETE FROM index_sections WHERE volume_id = ?', (volume_id,))
    con.execute('DELETE FROM works WHERE volume_id = ?', (volume_id,))
    con.execute('DELETE FROM runs WHERE volume_id = ?', (volume_id,))
    con.execute('DELETE FROM volumes WHERE volume_id = ?', (volume_id,))


def upsert_volume(con: sqlite3.Connection, volume: dict[str, Any]) -> None:
    now = now_iso()
    con.execute(
        '''INSERT INTO volumes (
            volume_id, collection, source_root, volume_label, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
            collection = excluded.collection,
            source_root = excluded.source_root,
            volume_label = excluded.volume_label,
            notes = excluded.notes,
            updated_at = excluded.updated_at''',
        (
            volume['volume_id'],
            volume.get('collection', volume['volume_id'][:2]),
            volume.get('source_root', ''),
            volume.get('volume_label', volume['volume_id']),
            to_text(volume.get('notes')),
            now,
            now,
        ),
    )


def derive_work_key(volume_id: str, work: dict[str, Any]) -> str:
    if work.get('work_key'):
        return str(work['work_key'])
    if work.get('work_order') is not None:
        return f"{volume_id}:work:{int(work['work_order']):03d}"
    title = work.get('title_raw') or work.get('author_raw') or 'work'
    return f"{volume_id}:work:{slugify(str(title))}"


def derive_section_key(volume_id: str, section: dict[str, Any], ordinal: int) -> str:
    if section.get('section_key'):
        return str(section['section_key'])
    scope = section.get('scope_kind', 'section')
    kind = section.get('index_kind', 'INDEX')
    start = section.get('page_start')
    start_part = f"{int(start):04d}" if start is not None else '0000'
    return f"{volume_id}:{scope}:{slugify(str(kind))}:{start_part}:{ordinal:03d}"
