from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import sys
import unicodedata


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def test_save_line_version_normalizes_before_hashing(tmp_path):
    db_path = tmp_path / "ocr.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            text_content TEXT,
            text_hash TEXT,
            source_score REAL,
            is_current INTEGER DEFAULT 1,
            meta_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    infer.save_line_version(
        str(db_path),
        "run",
        1,
        "tesseract",
        "migne",
        "Cafe\u0301 litterarum",
    )

    conn = sqlite3.connect(db_path)
    text, text_hash = conn.execute(
        "SELECT text_content, text_hash FROM line_versions"
    ).fetchone()
    conn.close()

    assert text == unicodedata.normalize("NFC", "Cafe\u0301 litterarum")
    assert text_hash == hashlib.sha256(text.encode()).hexdigest()
