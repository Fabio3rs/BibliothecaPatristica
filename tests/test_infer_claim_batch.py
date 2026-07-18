from __future__ import annotations

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
            image_path TEXT,
            bbox TEXT,
            line_image BLOB,
            tesseract_text TEXT,
            agreement_score REAL,
            status TEXT,
            updated_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            created_at TEXT,
            updated_at TEXT,
            run_id TEXT
        )
        """
    )
    rows = [
        (1, "img1.png", "{}", b"a", "t1", 0.9, "pending"),
        (2, "img2.png", "{}", b"b", "t2", 0.1, "pending"),
        (3, "img3.png", "{}", b"c", "t3", 0.2, "pending"),
        (4, "img4.png", "{}", b"d", "t4", 0.05, "pending"),
        (5, "img5.png", "{}", b"e", "t5", 0.3, "inferred"),
    ]
    con.executemany(
        "INSERT INTO lines(id, image_path, bbox, line_image, tesseract_text, agreement_score, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    versions = [
        (1, 2, "openai", "gpt-5-mini", None, None, "run-a"),
        (2, 3, "openai", "gpt-4.1-mini", None, None, "run-b"),
        (3, 4, "ollama", "qwen3.5:9b", None, None, "run-c"),
    ]
    con.executemany(
        "INSERT INTO line_versions(id, line_id, provider, model, created_at, updated_at, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        versions,
    )
    con.commit()
    con.close()


def _pending_statuses(path: Path) -> dict[int, str]:
    con = sqlite3.connect(path)
    rows = con.execute("SELECT id, status FROM lines ORDER BY id").fetchall()
    con.close()
    return {row[0]: row[1] for row in rows}


def test_claim_batch_without_preference_keeps_score_order(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(str(db_path), batch_size=3)

    assert [row[0] for row in batch] == [4, 2, 3]
    assert _pending_statuses(db_path) == {
        1: "pending",
        2: "processing",
        3: "processing",
        4: "processing",
        5: "inferred",
    }


def test_claim_batch_prefers_rows_missing_exact_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(
        str(db_path),
        batch_size=4,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini@3"),
    )

    assert [row[0] for row in batch] == [4, 3, 5, 1]


def test_claim_batch_treats_other_model_as_missing_for_target_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(
        str(db_path),
        batch_size=2,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
    )

    assert [row[0] for row in batch] == [4, 3]


def test_claim_batch_does_not_reclaim_processing_rows_with_backend_preference(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute("UPDATE lines SET status = 'processing' WHERE id = 5")
    con.commit()
    con.close()

    batch = infer.claim_batch(
        str(db_path),
        batch_size=4,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
    )

    assert 5 not in [row[0] for row in batch]


def test_parse_backend_identity_ignores_weight(tmp_path):
    backend = infer.parse_backend_identity("openai:gpt-5-mini@7")

    assert backend.provider == "openai"
    assert backend.model == "gpt-5-mini"
    assert backend.weight == 1.0
