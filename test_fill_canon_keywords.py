import json
import sqlite3

from tools.fill_canon_keywords import rebuild_canonical_names


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY,
            keyword_norm TEXT NOT NULL UNIQUE,
            keyword_original TEXT NOT NULL,
            hdbscan_group_id INTEGER,
            is_scripture_citation INTEGER DEFAULT 0
        );
        CREATE TABLE keyword_clusters (
            keyword_id INTEGER PRIMARY KEY,
            membership_probability REAL
        );
        CREATE TABLE keyword_occurrence (
            id INTEGER PRIMARY KEY,
            keyword_id INTEGER NOT NULL,
            pagina_id INTEGER
        );
        CREATE TABLE cluster_canonical_names (
            group_id INTEGER,
            nome_canonico TEXT,
            nome_canonico_original TEXT,
            score_ancora REAL
        );
        INSERT INTO cluster_canonical_names VALUES
            (10, 'rotulo antigo', 'Rótulo antigo', 1.0);

        INSERT INTO keywords VALUES
            (1, 'trinitas', 'Trinitas', 10, 0),
            (2, 'trindade', 'Trindade', 10, 0),
            (3, 'cristologia', 'Cristologia', 20, 0),
            (4, 'jo 3,16', 'Jo 3,16', 30, 1);
        INSERT INTO keyword_clusters VALUES
            (1, 1.0), (2, 1.0), (3, 0.8), (4, 1.0);
        """
    )
    conn.executemany(
        "INSERT INTO keyword_occurrence(keyword_id, pagina_id) VALUES (?, ?)",
        [(1, page) for page in range(1, 8)]
        + [(2, page) for page in range(20, 23)]
        + [(3, 40)],
    )
    conn.commit()
    return conn


def test_rebuild_replaces_stale_table_and_applies_text_override(tmp_path):
    conn = make_db()
    backup = tmp_path / "canonical-before.json"

    selected = rebuild_canonical_names(
        conn,
        overrides=[{"anchor_norm": "trindade", "canonical_norm": "trindade"}],
        backup_out=backup,
    )

    assert set(selected) == {10, 20}
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT group_id, nome_canonico_original "
            "FROM cluster_canonical_names ORDER BY group_id"
        )
    ] == [(10, "Trindade"), (20, "Cristologia")]
    assert json.loads(backup.read_text(encoding="utf-8"))["rows"][0][
        "nome_canonico"
    ] == "rotulo antigo"


def test_rebuild_dry_run_does_not_replace_table():
    conn = make_db()

    rebuild_canonical_names(conn, overrides=[], dry_run=True)

    assert conn.execute(
        "SELECT nome_canonico FROM cluster_canonical_names"
    ).fetchone()[0] == "rotulo antigo"
