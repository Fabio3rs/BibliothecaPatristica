"""Unit tests for deterministic ibid predecessor propagation."""
import json
from pathlib import Path
import sqlite3
import tempfile
import zlib

import pytest

from tools.indexing.apply_ibid_deterministic_propagation import (
    extract_file_seq,
    propagate_ibid_destinations,
)


def test_extract_file_seq():
    assert extract_file_seq("/path/to/PG003-590.txt") == 590
    assert extract_file_seq("/path/to/fbf896b3-ed57-422e-9668-d79cc8e21f05-014.txt") == 14
    assert extract_file_seq("no_seq.txt") == 0


def create_mock_catalog_db(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            volume TEXT,
            source TEXT,
            section TEXT,
            label TEXT,
            ref_raw TEXT,
            state TEXT,
            metadata_json TEXT
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

    src_p1 = "/mock/vol1/vol1-010.txt"
    src_p2 = "/mock/vol1/vol1-011.txt"
    src_p_gap = "/mock/vol1/vol1-030.txt"

    # Item 1: Anchor on page 1 (resolved)
    cur.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("item1", "VOL1", src_p1, "SecA", "Headword Alpha", "100", "with_destinations",
         json.dumps({"source_span": [100, 110]})),
    )
    blob1 = zlib.compress(b'{"method": "test"}')
    cur.execute(
        "INSERT INTO candidate_storage VALUES (?, ?, ?, ?, ?, ?)",
        ("item1", "/mock/dest/dest-050.txt", 1, 95.0, 1, blob1),
    )

    # Item 2: Intra-page ibid on page 1 (pending)
    cur.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("item2", "VOL1", src_p1, "SecA", "Subentry Beta", "ibid.", "source_only",
         json.dumps({"source_span": [150, 160]})),
    )
    cur.execute(
        "INSERT INTO residual_embedding_queue VALUES (?, ?, ?)",
        ("item2", "pending", "[]"),
    )

    # Item 3: Consecutive cross-page ibid on page 2 (pending)
    cur.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("item3", "VOL1", src_p2, "SecA", "Subentry Gamma", "ib.", "source_only",
         json.dumps({"source_span": [10, 20]})),
    )

    # Item 4: Discontinuous page ibid on page 30 (should be skipped due to gap > 2)
    cur.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("item4", "VOL1", src_p_gap, "SecA", "Subentry Delta", "ibid", "source_only",
         json.dumps({"source_span": [10, 20]})),
    )

    # Item 5: Section boundary change on page 2
    cur.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("item5", "VOL1", src_p2, "SecB_Different", "Subentry Epsilon", "ibid", "source_only",
         json.dumps({"source_span": [500, 510]})),
    )

    conn.commit()
    conn.close()


def test_propagate_ibid_destinations():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "search.sqlite"
        summary_path = tmp_path / "catalog_summary.json"
        with open(summary_path, "w") as f:
            json.dump({"total_items": 5, "items_with_destinations": 1}, f)

        create_mock_catalog_db(db_path)

        # 1. Test Dry Run
        res_dry = propagate_ibid_destinations(db_path, max_file_gap=2, apply=False)
        assert res_dry["status"] == "success"
        assert res_dry["dry_run"] is True
        assert res_dry["intra_page_count"] == 1  # item2
        assert res_dry["consecutive_cross_page_count"] == 1  # item3
        assert res_dry["promoted_items_count"] == 2
        assert res_dry["applied_count"] == 0

        # Verify DB untouched in dry run
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'item2'")
        assert cur.fetchone()[0] == "source_only"
        conn.close()

        # 2. Test Apply
        res_apply = propagate_ibid_destinations(db_path, max_file_gap=2, apply=True)
        assert res_apply["status"] == "success"
        assert res_apply["dry_run"] is False
        assert res_apply["applied_count"] == 2

        # Verify DB updated
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'item2'")
        assert cur.fetchone()[0] == "with_destinations"
        cur.execute("SELECT file, score FROM candidate_storage WHERE item_id = 'item2'")
        cand = cur.fetchone()
        assert cand[0] == "/mock/dest/dest-050.txt"
        assert cand[1] == 95.0

        cur.execute("SELECT status FROM residual_embedding_queue WHERE item_id = 'item2'")
        assert cur.fetchone()[0] == "confirmed"

        # Item 4 should NOT be promoted due to gap
        cur.execute("SELECT state FROM items WHERE id = 'item4'")
        assert cur.fetchone()[0] == "source_only"

        # Item 5 should NOT be promoted due to section boundary
        cur.execute("SELECT state FROM items WHERE id = 'item5'")
        assert cur.fetchone()[0] == "source_only"

        conn.close()
