from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import sys
import unicodedata


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.normalize_ocr_db_nfc import normalize_database


def make_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            reviewed_text TEXT,
            qwen_text TEXT,
            tesseract_text TEXT,
            updated_at TEXT
        );
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            text_content TEXT,
            text_hash TEXT,
            source_score REAL,
            is_current INTEGER,
            meta_json TEXT,
            updated_at TEXT
        );
        CREATE UNIQUE INDEX uq_line_versions_dedup
            ON line_versions(run_id, line_id, provider, model, text_hash);
        """
    )
    decomposed = "Cafe\u0301 litterarum"
    composed = unicodedata.normalize("NFC", decomposed)
    conn.execute(
        "INSERT INTO lines VALUES (1, ?, ?, NULL, NULL)",
        (decomposed, composed),
    )
    conn.execute(
        """INSERT INTO line_versions
           VALUES (1, 'run', 1, 'openai', 'gpt', ?, ?, 0.7, 1, 'old', NULL)""",
        (decomposed, hashlib.sha256(decomposed.encode()).hexdigest()),
    )
    conn.execute(
        """INSERT INTO line_versions
           VALUES (2, 'run', 1, 'openai', 'gpt', ?, ?, 0.9, 0, 'new', NULL)""",
        (composed, hashlib.sha256(composed.encode()).hexdigest()),
    )
    conn.execute(
        """INSERT INTO line_versions
           VALUES (3, 'other', 1, 'tesseract', 'migne', ?,
                   'hash-incorreto', 0.8, 1, NULL, NULL)""",
        (composed,),
    )
    conn.commit()
    conn.close()


def test_nfc_database_normalizer_is_dry_run_idempotent_and_merges_duplicates(
    tmp_path,
):
    db_path = tmp_path / "ocr.db"
    make_database(db_path)

    dry_run = normalize_database(db_path, apply=False, batch_size=1)
    assert dry_run["changes"]["lines.reviewed_text.changed"] == 1
    assert dry_run["changes"]["line_versions.text_content.changed"] == 1

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT reviewed_text FROM lines").fetchone()[0] == "Cafe\u0301 litterarum"
    conn.close()

    applied = normalize_database(db_path, apply=True, batch_size=1)
    assert applied["changes"]["line_versions.duplicates_merged"] == 1

    conn = sqlite3.connect(db_path)
    reviewed = conn.execute("SELECT reviewed_text FROM lines").fetchone()[0]
    versions = conn.execute(
        "SELECT text_content, text_hash, source_score, is_current FROM line_versions ORDER BY id"
    ).fetchall()
    conn.close()

    assert unicodedata.is_normalized("NFC", reviewed)
    assert len(versions) == 2
    for text, text_hash, _, _ in versions:
        assert unicodedata.is_normalized("NFC", text)
        assert text_hash == hashlib.sha256(text.encode()).hexdigest()
    merged = versions[0]
    assert merged[2] == 0.9
    assert merged[3] == 1

    repeated = normalize_database(db_path, apply=True, batch_size=2)
    assert repeated["changes"] == {}
