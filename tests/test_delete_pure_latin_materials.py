import importlib.util
from pathlib import Path
import sqlite3
import sys


SCRIPT = Path(__file__).parents[1] / "scripts" / "delete_pure_latin_materials.py"
SPEC = importlib.util.spec_from_file_location("delete_pure_latin_materials", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def create_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            sample_size INTEGER,
            volumes TEXT,
            updated_at TEXT
        );
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            session_id INTEGER REFERENCES sessions(id),
            page_id TEXT,
            image_path TEXT,
            volume TEXT,
            reviewed_text TEXT,
            qwen_text TEXT,
            tesseract_text TEXT,
            created_at TEXT
        );
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE
        );
        INSERT INTO sessions VALUES (1, 5, '["PG001"]', NULL);
        """
    )
    rows = [
        (1, "PG001-001", "Latin text"),
        (2, "PG001-001", "omnia vincit amor"),
        (3, "PG001-002", "λόγος"),
        (4, "PG001-003", "λόγος et verbum"),
        (5, "PG001-004", "ܡܠܬܐ et verbum"),
    ]
    conn.executemany(
        """
        INSERT INTO lines
            (id, session_id, page_id, image_path, volume, tesseract_text, created_at)
        VALUES (?, 1, ?, NULL, 'PG001', ?, '2026-08-07 12:00:00')
        """,
        rows,
    )
    conn.executemany(
        "INSERT INTO line_versions(id, line_id) VALUES (?, ?)",
        [(line_id, line_id) for line_id, _, _ in rows],
    )
    conn.commit()
    return conn


def test_unicode_letter_counts_distinguishes_scripts():
    assert MODULE.unicode_letter_counts("verbum") == (6, 0, 0)
    assert MODULE.unicode_letter_counts("λόγος") == (0, 5, 0)
    assert MODULE.unicode_letter_counts("λόγος et") == (2, 5, 0)
    assert MODULE.unicode_letter_counts("ܡܠܬܐ et") == (2, 0, 4)


def test_scan_and_delete_only_strictly_latin_lines(tmp_path):
    conn = create_db(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    result = MODULE.scan_lines(conn, "2026-08-07")

    assert result.target_ids == [1, 2]
    assert result.totals["latin_only"] == 2
    assert result.totals["latin_greek_mixed"] == 1
    assert result.totals["greek_without_latin"] == 1
    assert result.totals["other_script"] == 1

    assert MODULE.delete_targets(
        conn, result.target_ids, result.session_ids, batch_size=1
    ) == 2
    assert conn.execute("SELECT group_concat(id) FROM lines").fetchone()[0] == "3,4,5"
    assert conn.execute("SELECT group_concat(line_id) FROM line_versions").fetchone()[0] == "3,4,5"
    session = conn.execute(
        "SELECT sample_size, volumes FROM sessions WHERE id = 1"
    ).fetchone()
    assert tuple(session) == (3, '["PG001"]')
    conn.close()


def test_cutoff_excludes_older_latin_lines(tmp_path):
    conn = create_db(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    conn.execute("UPDATE lines SET created_at = '2026-08-06' WHERE page_id = 'PG001-001'")
    conn.commit()

    result = MODULE.scan_lines(conn, "2026-08-07")

    assert result.target_ids == []
    assert result.totals["latin_greek_mixed"] == 1
    assert result.totals["greek_without_latin"] == 1
    assert result.totals["other_script"] == 1
    conn.close()


def test_suspend_delete_and_rebuild_fts_with_stable_rowids(tmp_path):
    conn = create_db(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    assert MODULE.rebuild_fts(conn) == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM lines_fts WHERE rowid = line_id"
    ).fetchone()[0] == 5

    assert MODULE.suspend_fts_updates(conn) is True
    result = MODULE.scan_lines(conn, "2026-08-07")
    assert MODULE.delete_targets(
        conn, result.target_ids, result.session_ids, batch_size=1
    ) == 2

    assert conn.execute("SELECT COUNT(*) FROM lines_fts").fetchone()[0] == 5
    assert MODULE.rebuild_fts(conn) == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM lines_fts WHERE rowid = line_id"
    ).fetchone()[0] == 3

    conn.execute("UPDATE lines SET qwen_text = 'λόγος alterado' WHERE id = 3")
    updated = conn.execute(
        "SELECT rowid, line_id, search_text FROM lines_fts WHERE rowid = 3"
    ).fetchone()
    assert tuple(updated[:2]) == (3, 3)
    assert "λόγος alterado" in updated["search_text"]
    conn.execute("DELETE FROM lines WHERE id = 3")
    assert conn.execute(
        "SELECT COUNT(*) FROM lines_fts WHERE rowid = 3"
    ).fetchone()[0] == 0
    conn.close()
