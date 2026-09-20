from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import cleanup_defective_resumos_v2 as cleanup


SCHEMA = """
CREATE TABLE resumos(id INTEGER PRIMARY KEY, documento TEXT);
CREATE TABLE resumo_runs(id INTEGER PRIMARY KEY, documento TEXT);
CREATE TABLE resumo_generations(
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES resumo_runs(id) ON DELETE CASCADE,
    documento TEXT,
    previous_generation_id INTEGER REFERENCES resumo_generations(id)
);
CREATE TABLE resumo_translations(
    id INTEGER PRIMARY KEY,
    generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE
);
CREATE TABLE resumo_generation_clusters(
    cluster_run_id INTEGER,
    generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE,
    documento TEXT
);
CREATE TABLE resumo_review_queue(
    id INTEGER PRIMARY KEY,
    generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE,
    documento TEXT
);
CREATE TABLE resumo_context_anchors(id INTEGER PRIMARY KEY, documento TEXT);
"""


def _fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO resumo_runs VALUES (?, ?)",
            ((1, "PG024"), (2, "PL020")),
        )
        con.executemany(
            "INSERT INTO resumo_generations VALUES (?, ?, ?, ?)",
            (
                (10, 1, "PG024", None),
                (11, 1, "PG024", 10),
                (20, 2, "PL020", 11),
            ),
        )
        con.execute("INSERT INTO resumo_translations VALUES (1, 11)")
        con.executemany(
            "INSERT INTO resumo_generation_clusters VALUES (?, ?, ?)",
            ((100, 11, "PG024"), (100, 20, "PL020")),
        )
        con.executemany(
            "INSERT INTO resumo_review_queue VALUES (?, ?, ?)",
            ((1, 11, "PG024"), (2, 20, "PL020")),
        )
        con.executemany(
            "INSERT INTO resumo_context_anchors(documento) VALUES (?)",
            (("PG024",), ("PL020",)),
        )
        con.executemany(
            "INSERT INTO resumos(documento) VALUES (?)",
            (("PG024",), ("PL020",)),
        )


def test_purge_removes_only_selected_v2_state(tmp_path: Path) -> None:
    path = tmp_path / "patristica_resumos.db"
    _fixture_db(path)

    con = cleanup._connect_writable(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        changed, after = cleanup.purge(con)
        con.commit()
    finally:
        con.close()

    assert changed["resumo_generations"] == 2
    assert changed["resumo_runs"] == 1
    assert changed["previous_generation_links_cleared"] == 2
    assert after["resumo_generations"] == 0
    assert after["resumo_review_queue"] == 0
    assert after["legacy_resumos_preserved"] == 1

    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT documento, previous_generation_id FROM resumo_generations"
        ).fetchall() == [("PL020", None)]
        assert con.execute("SELECT documento FROM resumo_runs").fetchall() == [
            ("PL020",)
        ]
        assert con.execute("SELECT documento FROM resumos ORDER BY id").fetchall() == [
            ("PG024",),
            ("PL020",),
        ]
        assert con.execute(
            "SELECT documento FROM resumo_generation_clusters"
        ).fetchall() == [("PL020",)]


def test_purge_rejects_cross_document_run(tmp_path: Path) -> None:
    path = tmp_path / "patristica_resumos.db"
    _fixture_db(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO resumo_generations VALUES (?, ?, ?, ?)",
            (30, 1, "PL020", None),
        )

    con = cleanup._connect_writable(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="cruzam documentos"):
            cleanup.purge(con)
        con.rollback()
    finally:
        con.close()

    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM resumo_generations").fetchone()[0] == 4
