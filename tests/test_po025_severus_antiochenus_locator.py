"""
Unit tests for PO025 Severus Antiochenus locator (Sprint 66)
"""

import os
import sqlite3
import pytest
from tools.indexing.po025_severus_antiochenus_locator import (
    PO025_ITEMS_RAW,
    resolve_po025_item,
    execute_sprint66,
)


@pytest.fixture
def mock_db(tmp_path):
    db_file = str(tmp_path / "test_search.sqlite")
    summary_file = str(tmp_path / "test_summary.json")

    conn = sqlite3.connect(db_file)
    conn.execute('''
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            group_id TEXT,
            volume TEXT,
            section TEXT,
            label TEXT,
            ref_raw TEXT,
            source TEXT,
            state TEXT,
            metadata_json TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE candidate_storage (
            item_id TEXT,
            file TEXT,
            rank INTEGER,
            score REAL,
            navigable INTEGER,
            candidate_json BLOB,
            PRIMARY KEY (item_id, file)
        )
    ''')

    for item_id, (group_id, dests) in PO025_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PO025', 'INDEX GENERALIS', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()
    return db_file, summary_file


def test_po025_items_count():
    assert len(PO025_ITEMS_RAW) == 9


def test_po025_resolution_and_physical_files():
    for item_id in PO025_ITEMS_RAW:
        res = resolve_po025_item(item_id)
        assert res is not None, f"Failed to resolve {item_id}"
        group_id, destinations = res
        assert len(destinations) >= 1
        for vol, page, col_desc, reason, fpath in destinations:
            assert vol == 'PO025'
            assert page > 0
            assert os.path.exists(fpath), f"Physical file does not exist: {fpath}"
            assert reason.startswith('po025_')


def test_po025_dry_run(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint66(apply=False, db_path=db_path, summary_path=summary_path)
    assert report['volume'] == 'PO025'
    assert report['sprint'] == 66
    assert report['total_source_only_initial'] == 9
    assert report['resolved_count'] == 9
    assert report['candidates_created'] == 0


def test_po025_apply(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint66(apply=True, db_path=db_path, summary_path=summary_path)
    assert report['resolved_count'] == 9
    assert report['candidates_created'] >= 9
    assert report['applied'] is True

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    assert cur.fetchone()[0] == 9
    conn.close()
