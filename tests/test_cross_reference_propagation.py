"""Unit tests for cross-reference remission propagation (Latin & French / PO)."""
import json
from pathlib import Path
import sqlite3
import tempfile
import zlib

import pytest

from tools.indexing.apply_cross_reference_propagation import (
    normalize_lookup_key,
    resolve_cross_references,
)


def test_normalize_lookup_key():
    assert normalize_lookup_key("Walafridus Strabus") == "walafridus strabus"
    assert normalize_lookup_key("Ἰσδανδούχ") == "ισδανδουχ"
    assert normalize_lookup_key("S. Hæretici") == "s haeretici"
    assert normalize_lookup_key("Cœlum") == "coelum"
    assert normalize_lookup_key("   (vide   Petrus!)  ") == "vide petrus"


def create_mock_catalog(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            volume TEXT,
            label TEXT,
            ref_raw TEXT,
            state TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE candidate_storage (
            item_id TEXT,
            file TEXT,
            rank INTEGER,
            score REAL,
            navigable INTEGER,
            candidate_json BLOB,
            PRIMARY KEY (item_id, file)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE residual_embedding_queue (
            item_id TEXT PRIMARY KEY,
            status TEXT,
            candidate_files_json TEXT
        )
        """
    )

    blob = zlib.compress(b'{"test": true}')

    # Target 1 (PL): Joannes Scotus Erigena (resolved)
    cur.execute("INSERT INTO items VALUES ('pl_target', 'PL218', 'Joannes Scotus Erigena', 'CXXII, 1076', 'with_destinations')")
    cur.execute("INSERT INTO candidate_storage VALUES ('pl_target', '/path/pl122.txt', 1, 95.0, 1, ?)", (blob,))

    # Remission 1 (PL): Joan. Scotus -> vide Scotus Erigena. (pending)
    cur.execute("INSERT INTO items VALUES ('pl_rem', 'PL218', 'Joan. Scotus', 'vide Scotus Erigena.', 'source_only')")
    cur.execute("INSERT INTO residual_embedding_queue VALUES ('pl_rem', 'pending', '[]')")

    # Target 2 (PO): Θερβοῦς vierge martyre (resolved)
    cur.execute("INSERT INTO items VALUES ('po_target', 'PO002', 'Θερβοῦς vierge martyre', '439, 16', 'with_destinations')")
    cur.execute("INSERT INTO candidate_storage VALUES ('po_target', '/path/po002_439.txt', 1, 90.0, 1, ?)", (blob,))

    # Remission 2 (PO French): Φερβοῦς -> voir Θερβοῦς. (pending)
    cur.execute("INSERT INTO items VALUES ('po_rem', 'PO002', 'Φερβοῦς', 'voir Θερβοῦς.', 'source_only')")
    cur.execute("INSERT INTO residual_embedding_queue VALUES ('po_rem', 'pending', '[]')")

    # Unresolvable Remission: vide NonExistent.
    cur.execute("INSERT INTO items VALUES ('unres', 'PL218', 'Orphan', 'vide NonExistent.', 'source_only')")

    conn.commit()
    conn.close()


def test_resolve_cross_references():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "search.sqlite"
        summary_path = tmp_path / "catalog_summary.json"
        with open(summary_path, "w") as f:
            json.dump({"total_items": 5, "items_with_destinations": 2}, f)

        create_mock_catalog(db_path)

        # 1. Test Dry Run
        res_dry = resolve_cross_references(db_path, apply=False)
        assert res_dry["status"] == "success"
        assert res_dry["dry_run"] is True
        assert res_dry["promoted_items_count"] == 2
        assert res_dry["unresolved_count"] == 1
        assert res_dry["applied_count"] == 0

        # Verify untouched
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'pl_rem'")
        assert cur.fetchone()[0] == "source_only"
        conn.close()

        # 2. Test Apply
        res_apply = resolve_cross_references(db_path, apply=True)
        assert res_apply["status"] == "success"
        assert res_apply["dry_run"] is False
        assert res_apply["applied_count"] == 2

        # Verify database updates
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'pl_rem'")
        assert cur.fetchone()[0] == "with_destinations"
        cur.execute("SELECT file, score FROM candidate_storage WHERE item_id = 'pl_rem'")
        cand = cur.fetchone()
        assert cand[0] == "/path/pl122.txt"

        cur.execute("SELECT state FROM items WHERE id = 'po_rem'")
        assert cur.fetchone()[0] == "with_destinations"
        cur.execute("SELECT file FROM candidate_storage WHERE item_id = 'po_rem'")
        cand_po = cur.fetchone()
        assert cand_po[0] == "/path/po002_439.txt"

        # Residual queue updated
        cur.execute("SELECT status FROM residual_embedding_queue WHERE item_id = 'pl_rem'")
        assert cur.fetchone()[0] == "confirmed"

        # Orphan remains source_only
        cur.execute("SELECT state FROM items WHERE id = 'unres'")
        assert cur.fetchone()[0] == "source_only"

        conn.close()
