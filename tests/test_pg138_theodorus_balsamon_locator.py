"""
Tests for Sprint 84: PG138 Theodorus Balsamon Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pg138_theodorus_balsamon_locator import (
    PG138_ITEMS_RAW,
    resolve_pg138_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint84,
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

    for item_id, (group_id, dests) in PG138_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PG138', 'INDEX ANALYTICUS', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PG138_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PG138_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pg138_items_structure():
    assert len(PG138_ITEMS_RAW) == 6
    for item_id, (group_id, dests) in PG138_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol in ['PG137', 'PG138']
            assert 1 <= page <= 850
            assert reason.startswith('pg138_')


def test_pg138_resolution():
    res = resolve_pg138_item('dfc0eae1c7d1f457b8ff5142f83b9441c5d91a9ce5af2736c30c09b4df11973b')
    assert res is not None
    group_id, dests = res
    assert group_id == '4efc8a0e74991c735f03f52841f38cf558bf33e664ea41cf1a0302f778fe2cb5'
    assert len(dests) == 2
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PG137'
    assert page == 414
    assert os.path.exists(fpath)


def test_pg138_all_physical_files_exist():
    for item_id, (group_id, dests) in PG138_ITEMS_RAW.items():
        res = resolve_pg138_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pg138_dry_run(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint84(apply=False, db_path=db_file, summary_path=summary_file)
    assert rep['resolved_count'] == 6
    assert rep['candidates_created'] == 0


def test_pg138_apply(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint84(apply=True, db_path=db_file, summary_path=summary_file)
    assert rep['resolved_count'] == 6
    assert rep['candidates_created'] >= 6
    assert rep['total_with_destinations_now'] == 6
    assert rep['total_source_only_now'] == 0

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    assert cur.fetchone()[0] >= 6
    conn.close()
