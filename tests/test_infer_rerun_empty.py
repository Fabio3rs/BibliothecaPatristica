from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            line_image BLOB,
            tesseract_text TEXT,
            qwen_text TEXT,
            reviewed_text TEXT,
            agreement_score REAL,
            status TEXT,
            updated_at TEXT
        )
        """
    )
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    rows = [
        (1, png_bytes, "", "abc", None, 0.0, "inferred"),
        (2, png_bytes, None, "abc", None, 0.0, "inferred"),
        (3, png_bytes, "texto", "abc", None, 0.0, "inferred"),
        (4, png_bytes, "", "", None, 0.0, "inferred"),
        (5, png_bytes, "", "abc", None, 0.0, "approved"),
    ]
    con.executemany(
        "INSERT INTO lines(id, line_image, tesseract_text, qwen_text, reviewed_text, agreement_score, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def test_rerun_tesseract_empty_only_filters_blank_rows(tmp_path, capsys):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    infer.rerun_tesseract_lines(
        str(db_path),
        lang="lat",
        dry_run=True,
        empty_only=True,
    )

    out = capsys.readouterr().out
    assert "empty_only=True" in out
    assert "IDs elegíveis: 1, 2" in out
    assert "IDs elegíveis: 1, 2, 3" not in out
