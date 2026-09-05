from __future__ import annotations

import sqlite3
from pathlib import Path


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def connect_db(path: Path) -> sqlite3.Connection:
    ensure_parent_dir(path)
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # Avoid sandbox temp-file issues during larger FTS inserts.
    con.execute("PRAGMA temp_store = MEMORY")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_catalog_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS volumes (
            volume_id TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            volume_number INTEGER NOT NULL,
            volume_suffix TEXT NOT NULL DEFAULT '',
            language_hint_json TEXT,
            metadata_json TEXT,
            needs_research INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS works (
            work_id TEXT PRIMARY KEY,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            title TEXT,
            author TEXT,
            language TEXT,
            page_ids_json TEXT,
            confidence REAL,
            raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_works_volume_id ON works(volume_id);

        CREATE TABLE IF NOT EXISTS chunk_outputs (
            chunk_id TEXT PRIMARY KEY,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            work_id TEXT REFERENCES works(work_id) ON DELETE SET NULL,
            summary_pt TEXT,
            skip INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            page_ids_json TEXT,
            work_hint_json TEXT,
            raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_outputs_volume_id ON chunk_outputs(volume_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_outputs_work_id ON chunk_outputs(work_id);

        CREATE TABLE IF NOT EXISTS citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL REFERENCES chunk_outputs(chunk_id) ON DELETE CASCADE,
            page_id TEXT,
            quote_raw TEXT,
            translation_pt TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_citations_chunk_id ON citations(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_citations_page_id ON citations(page_id);

        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL REFERENCES chunk_outputs(chunk_id) ON DELETE CASCADE,
            page_id TEXT,
            before_text TEXT,
            after_text TEXT,
            reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_edits_chunk_id ON edits(chunk_id);

        CREATE TABLE IF NOT EXISTS run_logs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            model TEXT,
            prompt_version TEXT,
            input_path TEXT,
            output_path TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            details_json TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_doc_profiles (
            volume_id TEXT PRIMARY KEY REFERENCES volumes(volume_id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_chunk_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            output_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(chunk_id, stage)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_chunk_notes_volume_id ON agent_chunk_notes(volume_id);
        CREATE INDEX IF NOT EXISTS idx_agent_chunk_notes_stage ON agent_chunk_notes(stage);

        CREATE TABLE IF NOT EXISTS agent_topic_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            source_topic TEXT NOT NULL,
            target_topic TEXT NOT NULL,
            relation TEXT,
            score REAL,
            evidence_json TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_topic_links_volume_id ON agent_topic_links(volume_id);

        CREATE TABLE IF NOT EXISTS agent_loop_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            volume_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            objective TEXT NOT NULL,
            hints TEXT,
            passes INTEGER NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error_text TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_loop_runs_volume_id ON agent_loop_runs(volume_id);
        CREATE INDEX IF NOT EXISTS idx_agent_loop_runs_status ON agent_loop_runs(status);

        CREATE TABLE IF NOT EXISTS agent_loop_checkpoints (
            volume_id TEXT NOT NULL,
            stage_idx INTEGER NOT NULL,
            pass_idx INTEGER NOT NULL,
            logs_json TEXT NOT NULL,
            compacted_summary TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (volume_id, stage_idx, pass_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_loop_checkpoints_volume ON agent_loop_checkpoints(volume_id);
        """
    )
    con.commit()


def init_text_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS volumes (
            volume_id TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            volume_number INTEGER NOT NULL,
            volume_suffix TEXT NOT NULL DEFAULT '',
            source_dir TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT UNIQUE NOT NULL,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            page_num INTEGER,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            text_raw TEXT NOT NULL,
            text_norm TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            ocr_result_id INTEGER,          -- FK lógica para ocr_versions.db → ocr_results.id
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pages_volume_id ON pages(volume_id);
        CREATE INDEX IF NOT EXISTS idx_pages_page_num ON pages(volume_id, page_num);

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT UNIQUE NOT NULL,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
            chunk_seq INTEGER NOT NULL,
            text_norm TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_volume_id ON chunks(volume_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_seq ON chunks(volume_id, chunk_seq);

        CREATE TABLE IF NOT EXISTS chunk_pages (
            chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
            page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
            page_order INTEGER NOT NULL,
            PRIMARY KEY (chunk_id, page_id, page_order)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_pages_page_id ON chunk_pages(page_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
        USING fts5(text_norm, content='pages', content_rowid='id', tokenize='unicode61');

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(text_norm, content='chunks', content_rowid='id', tokenize='unicode61');

        CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
            INSERT INTO pages_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
            INSERT INTO pages_fts(pages_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
            INSERT INTO pages_fts(pages_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
            INSERT INTO pages_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
            INSERT INTO chunks_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
        END;
        """
    )
    # Migração: adicionar coluna ocr_result_id em DBs existentes que não a tenham
    existing_cols = {
        row[1]
        for row in con.execute("PRAGMA table_info(pages)").fetchall()
    }
    if "ocr_result_id" not in existing_cols:
        con.execute("ALTER TABLE pages ADD COLUMN ocr_result_id INTEGER")
    con.commit()


def init_databases(catalog_db: Path, text_db: Path, reset: bool) -> None:
    if reset:
        if catalog_db.exists():
            catalog_db.unlink()
        if text_db.exists():
            text_db.unlink()

    with connect_db(catalog_db) as con:
        init_catalog_schema(con)
    with connect_db(text_db) as con:
        init_text_schema(con)
