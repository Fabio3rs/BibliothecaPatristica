from __future__ import annotations

from io import StringIO
import json
import sqlite3

from keywords_serial import verify_documents


def _resumos_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE resumos (
            id INTEGER PRIMARY KEY,
            documento TEXT NOT NULL,
            pagina_num INTEGER NOT NULL,
            pagina_texto TEXT NOT NULL DEFAULT '',
            resumo_pagina TEXT NOT NULL DEFAULT '',
            resumo_global TEXT NOT NULL DEFAULT '',
            keywords_json TEXT NOT NULL DEFAULT '',
            keywords_source TEXT NOT NULL DEFAULT '',
            keywords_modelo TEXT NOT NULL DEFAULT ''
        )
        """
    )
    con.executemany(
        """
        INSERT INTO resumos
            (documento, pagina_num, keywords_json, keywords_source, keywords_modelo)
        VALUES ('PGTEST', ?, ?, 'resumo_pagina', 'model')
        """,
        [
            (1, json.dumps({"keywords": ["a", "b", "c", "d", "e"]})),
            (2, json.dumps({"keywords": []})),
        ],
    )
    con.commit()
    return con


def test_verify_documents_returns_summary_and_jsonl_without_writes() -> None:
    con = _resumos_db()
    before = con.total_changes
    report = StringIO()

    summary = verify_documents(
        con,
        ["PGTEST"],
        report_output=report,
    )

    assert con.total_changes == before
    assert summary == {"pages": 2, "issue_pages": 1, "fixes": 0, "reruns": 0}
    rows = [json.loads(line) for line in report.getvalue().splitlines()]
    assert rows[0]["pagina_num"] == 2
    assert "empty_keywords" in rows[0]["issues"]
    assert "missing_keywords_field" not in rows[0]["issues"]
