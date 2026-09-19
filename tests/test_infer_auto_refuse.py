from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            page_id TEXT,
            volume TEXT,
            qwen_text TEXT,
            reviewed_text TEXT,
            agreement_score REAL,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            text_content TEXT,
            is_current INTEGER,
            updated_at TEXT
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO lines
            (id, page_id, volume, qwen_text, reviewed_text, agreement_score, status)
        VALUES (?, ?, 'PG001', ?, ?, ?, ?)
        """,
        (
            (1, "PG001-001", "texto salvo", None, 0.4, "inferred"),
            (2, "PG001-002", "texto com dúvida", None, 0.7, "inferred"),
            (3, "PG001-003", "consenso preservado", None, 0.8, "inferred"),
            (4, "PG001-004", "revisado", "texto humano", 0.0, "corrected"),
            (5, "PG001-005", "versão atual", None, 0.1, "inferred"),
        ),
    )
    conn.executemany(
        """
        INSERT INTO line_versions
            (id, line_id, provider, model, text_content, is_current, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (1, 1, "openai", "gpt-a", "aaaaaa", 1, "2026-01-01"),
            (2, 1, "ollama", "qwen", "zzzzzz", 1, "2026-01-01"),
            (3, 2, "openai", "gpt-a", "[?][?][?] leitura", 1, "2026-01-01"),
            (4, 2, "ollama", "qwen", "leitura confiável", 1, "2026-01-01"),
            (5, 3, "openai", "gpt-a", "texto em comum", 1, "2026-01-01"),
            (6, 3, "ollama", "qwen", "texto em comum", 1, "2026-01-01"),
            (7, 3, "tesseract", "migne", "outro texto", 1, "2026-01-01"),
            (8, 4, "openai", "gpt-a", "aaaaaa", 1, "2026-01-01"),
            (9, 4, "ollama", "qwen", "zzzzzz", 1, "2026-01-01"),
            (10, 5, "openai", "gpt-a", "aaaaaa", 0, "2026-01-01"),
            (11, 5, "openai", "gpt-a", "versão atual", 1, "2026-01-02"),
            (12, 5, "ollama", "qwen", "versão atual", 1, "2026-01-01"),
        ),
    )
    conn.commit()
    conn.close()


def statuses(path: Path) -> dict[int, str]:
    conn = sqlite3.connect(path)
    result = dict(conn.execute("SELECT id, status FROM lines ORDER BY id"))
    conn.close()
    return result


def test_auto_refuse_dry_run_uses_all_current_engines_and_writes_audit(tmp_path: Path):
    db_path = tmp_path / "ocr.db"
    report_path = tmp_path / "dry-run.jsonl"
    make_db(db_path)

    candidates, changed, reasons = infer.auto_refuse_lines(
        str(db_path),
        str(report_path),
        dry_run=True,
    )

    assert candidates == 2
    assert changed == 0
    assert reasons == {
        "all_engines_disagree": 1,
        "strong_unknown_markers": 1,
    }
    assert statuses(db_path) == {
        1: "inferred",
        2: "inferred",
        3: "inferred",
        4: "corrected",
        5: "inferred",
    }
    records = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    by_id = {record["line_id"]: record for record in records}
    assert set(by_id) == {1, 2}
    assert by_id[1]["action"] == "would_reject"
    assert by_id[1]["max_consensus"] == 0.0
    assert by_id[1]["most_similar_pair"]["left_engine"] == "ollama:qwen"
    assert by_id[1]["most_similar_pair"]["right_engine"] == "openai:gpt-a"
    assert by_id[2]["reasons"] == ["strong_unknown_markers"]


def test_auto_refuse_apply_only_changes_inferred_status(tmp_path: Path):
    db_path = tmp_path / "ocr.db"
    report_path = tmp_path / "apply.jsonl"
    make_db(db_path)

    candidates, changed, _ = infer.auto_refuse_lines(
        str(db_path),
        str(report_path),
        dry_run=False,
    )

    assert candidates == 2
    assert changed == 2
    assert statuses(db_path) == {
        1: "rejected",
        2: "rejected",
        3: "inferred",
        4: "corrected",
        5: "inferred",
    }
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT qwen_text, agreement_score, reviewed_text FROM lines WHERE id = 1"
    ).fetchone()
    version_count = conn.execute("SELECT COUNT(*) FROM line_versions WHERE line_id = 1").fetchone()[0]
    conn.close()
    assert row == ("texto salvo", 0.4, None)
    assert version_count == 2
    assert json.loads(report_path.read_text(encoding="utf-8").splitlines()[0])["action"] == "rejected"


def test_auto_refuse_requires_explicit_report_overwrite(tmp_path: Path):
    db_path = tmp_path / "ocr.db"
    report_path = tmp_path / "report.jsonl"
    make_db(db_path)
    report_path.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="overwrite-auto-refuse-report"):
        infer.auto_refuse_lines(str(db_path), str(report_path), dry_run=True)
