from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unicodedata


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review


def test_submit_review_normalizes_nfc(tmp_path, monkeypatch):
    db_path = tmp_path / "ocr.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE lines (
               id INTEGER PRIMARY KEY,
               reviewed_text TEXT,
               status TEXT,
               updated_at TEXT
           )"""
    )
    conn.execute("INSERT INTO lines(id, status) VALUES (1, 'inferred')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(review, "DB_PATH", str(db_path))

    response = review.app.test_client().post(
        "/submit",
        data={
            "line_id": "1",
            "reviewed_text": "Cafe\u0301 litterarum",
            "action": "corrected",
            "return_to": "/",
        },
    )

    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    text, status = conn.execute(
        "SELECT reviewed_text, status FROM lines WHERE id = 1"
    ).fetchone()
    conn.close()
    assert text == unicodedata.normalize("NFC", "Cafe\u0301 litterarum")
    assert status == "corrected"
