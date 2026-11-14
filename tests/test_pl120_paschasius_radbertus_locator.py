"""
Unit tests for PL120 Paschasius Radbertus locator (Sprint 63)
"""

import os
import sqlite3
import pytest
from tools.indexing.pl120_paschasius_radbertus_locator import (
    PL120_ITEMS_RAW,
    resolve_pl120_item,
    execute_sprint63,
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

    for item_id, (group_id, dests) in PL120_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PL120', 'INDEX GENERALIS', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()
    return db_file, summary_file


def test_pl120_items_count():
    assert len(PL120_ITEMS_RAW) == 10


def test_pl120_resolution_and_physical_files():
    for item_id in PL120_ITEMS_RAW:
        res = resolve_pl120_item(item_id)
        assert res is not None, f"Failed to resolve {item_id}"
        group_id, destinations = res
        assert len(destinations) >= 1
        for vol, page, col_desc, reason, fpath in destinations:
            assert vol == 'PL120'
            assert page > 0
            assert os.path.exists(fpath), f"Physical file does not exist: {fpath}"
            assert reason.startswith('pl120_')


def test_pl120_dry_run(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint63(apply=False, db_path=db_path, summary_path=summary_path)
    assert report['volume'] == 'PL120'
    assert report['sprint'] == 63
    assert report['total_source_only_initial'] == 10
    assert report['resolved_count'] == 10
    assert report['candidates_created'] == 0


def test_pl120_apply(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint63(apply=True, db_path=db_path, summary_path=summary_path)
    assert report['resolved_count'] == 10
    assert report['candidates_created'] >= 10
    assert report['applied'] is True

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    assert cur.fetchone()[0] == 10
    conn.close()
