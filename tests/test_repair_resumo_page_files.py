from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.repair_resumo_page_files import apply_plan, build_plan


def _create_table(con: sqlite3.Connection, table: str) -> None:
    con.execute(
        f"""
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY,
            documento TEXT NOT NULL,
            pagina_num INTEGER NOT NULL,
            pagina_file TEXT NOT NULL
        )
        """
    )


def test_repairs_all_page_file_tables_by_logical_page(tmp_path: Path) -> None:
    corpus_root = tmp_path / "teste"
    text_dir = corpus_root / "PG001" / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "PG001-001.txt").write_text("um", encoding="utf-8")
    (text_dir / "PG001-002.txt").write_text("dois", encoding="utf-8")

    db_path = tmp_path / "resumos.db"
    con = sqlite3.connect(db_path)
    for table in ("resumos", "resumo_generations", "resumo_context_anchors"):
        _create_table(con, table)
        con.execute(
            f"INSERT INTO {table} VALUES (1, 'PG001', 1, 'uuid-0001.txt')"
        )
        con.execute(
            f"INSERT INTO {table} VALUES (2, 'PG001', 2, 'PG001-002.txt')"
        )
    con.commit()
    con.close()

    changes, unresolved = build_plan(db_path=db_path, corpus_root=corpus_root)

    assert not unresolved
    assert len(changes) == 3
    manifest = apply_plan(
        db_path=db_path,
        corpus_root=corpus_root,
        changes=changes,
        unresolved=unresolved,
        manifest_root=tmp_path / "manifests",
    )
    assert manifest.is_file()

    con = sqlite3.connect(db_path)
    for table in ("resumos", "resumo_generations", "resumo_context_anchors"):
        values = con.execute(
            f"SELECT pagina_file FROM {table} ORDER BY id"
        ).fetchall()
        assert values == [("PG001-001.txt",), ("PG001-002.txt",)]
    con.close()

    changes, unresolved = build_plan(db_path=db_path, corpus_root=corpus_root)
    assert changes == []
    assert unresolved == []


def test_refuses_to_apply_when_page_is_missing(tmp_path: Path) -> None:
    corpus_root = tmp_path / "teste"
    (corpus_root / "PG001" / "text").mkdir(parents=True)
    db_path = tmp_path / "resumos.db"
    con = sqlite3.connect(db_path)
    for table in ("resumos", "resumo_generations", "resumo_context_anchors"):
        _create_table(con, table)
        con.execute(
            f"INSERT INTO {table} VALUES (1, 'PG001', 3, 'old-003.txt')"
        )
    con.commit()
    con.close()

    changes, unresolved = build_plan(db_path=db_path, corpus_root=corpus_root)

    assert changes == []
    assert len(unresolved) == 3
