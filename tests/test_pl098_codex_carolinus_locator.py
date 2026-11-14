"""
Tests for Sprint 74: PL098 Codex Carolinus Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pl098_codex_carolinus_locator import (
    PL098_ITEMS_RAW,
    resolve_pl098_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint74,
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

    for item_id, (group_id, dests) in PL098_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PL098', 'INDEX RERUM ET VERBORUM', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PL098_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PL098_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pl098_items_structure():
    assert len(PL098_ITEMS_RAW) == 7
    for item_id, (group_id, dests) in PL098_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol == 'PL098'
            assert 1 <= page <= 600
            assert 'PL098' in col_desc
            assert reason.startswith('pl098_')


def test_pl098_resolution():
    res = resolve_pl098_item('region-entry:a3e4b01ff4510886e57c8c16aeb6b2c47e8dcb41da59dc86ba5520ea732a1057')
    assert res is not None
    group_id, dests = res
    assert group_id == 'region-entry:a3e4b01ff4510886e57c8c16aeb6b2c47e8dcb41da59dc86ba5520ea732a1057'
    assert len(dests) == 2
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PL098'
    assert page == 9
    assert os.path.exists(fpath)


def test_pl098_all_physical_files_exist():
    for item_id, (group_id, dests) in PL098_ITEMS_RAW.items():
        res = resolve_pl098_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pl098_dry_run(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint74(apply=False, db_path=db_path, summary_path=summary_path)
    assert report['volume'] == 'PL098'
    assert report['sprint'] == 74
    assert report['total_source_only_initial'] == 7
    assert report['resolved_count'] == 7
    assert report['candidates_created'] == 0


def test_pl098_apply(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint74(apply=True, db_path=db_path, summary_path=summary_path)
    assert report['resolved_count'] == 7
    assert report['candidates_created'] >= 7
    assert report['applied'] is True

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    assert cur.fetchone()[0] == 7
    conn.close()
