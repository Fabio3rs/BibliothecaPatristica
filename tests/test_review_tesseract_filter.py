from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            page_id TEXT,
            line_index INTEGER,
            volume TEXT,
            detected_lang TEXT,
            bbox TEXT,
            status TEXT,
            agreement_score REAL,
            line_image BLOB,
            image_path TEXT,
            tesseract_text TEXT,
            qwen_text TEXT,
            reviewed_text TEXT,
            updated_at TEXT
        )
        """
    )
    rows = [
        (1, "PAGE-001", 1, "V1", "lat", "{}", "inferred", 0.2, b"a", "img1.png", "", "q1", None),
        (2, "PAGE-002", 2, "V1", "lat", "{}", "inferred", 0.3, b"b", "img2.png", "texto", "q2", None),
        (3, "PAGE-003", 3, "V1", "lat", "{}", "approved", 0.4, b"c", "img3.png", "", "q3", None),
    ]
    con.executemany(
        "INSERT INTO lines(id, page_id, line_index, volume, detected_lang, bbox, status, agreement_score, line_image, image_path, tesseract_text, qwen_text, reviewed_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def _open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def test_review_filters_tesseract_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)
    monkeypatch.setattr(review, "get_conn", lambda path=str(db_path): _open_db(path))

    client = review.app.test_client()
    resp = client.get("/?status=all&tesseract=empty&volume=")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "tesseract vazio" in body
    assert "PAGE-001" in body
    assert "PAGE-002" not in body


def test_review_filters_tesseract_filled(tmp_path, monkeypatch):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)
    monkeypatch.setattr(review, "get_conn", lambda path=str(db_path): _open_db(path))

    client = review.app.test_client()
    resp = client.get("/?status=all&tesseract=filled&volume=")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "PAGE-002" in body
    assert "PAGE-001" not in body
