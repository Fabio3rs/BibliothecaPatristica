"""
Tests for Sprint 88: PL096 S. Julianus Toletanus Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pl096_julianus_toletanus_locator import (
    PL096_ITEMS_RAW,
    resolve_pl096_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint88,
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

    for item_id, (group_id, dests) in PL096_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PL096', 'INDEX CAPITULORUM', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PL096_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PL096_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pl096_items_structure():
    assert len(PL096_ITEMS_RAW) == 5
    for item_id, (group_id, dests) in PL096_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol == 'PL096'
            assert 1 <= page <= 650
            assert reason.startswith('pl096_')


def test_pl096_resolution():
    res = resolve_pl096_item('region-entry:7de0d93468261a5d90e376f93bd9b40f584bff8fb69b1e7e695a69ce2d5304d7')
    assert res is not None
    group_id, dests = res
    assert group_id == 'region-group:7de0d93468261a5d90e376f93bd9b40f584bff8fb69b1e7e695a69ce2d5304d7'
    assert len(dests) == 1
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PL096'
    assert page == 353
    assert os.path.exists(fpath)


def test_pl096_all_physical_files_exist():
    for item_id, (group_id, dests) in PL096_ITEMS_RAW.items():
        res = resolve_pl096_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pl096_dry_run(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint88(apply=False, db_path=db_file, summary_path=summary_file)
    assert rep['resolved_count'] == 5
    assert rep['candidates_created'] == 0


def test_pl096_apply(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint88(apply=True, db_path=db_file, summary_path=summary_file)
    assert rep['resolved_count'] == 5
    assert rep['candidates_created'] >= 5
    assert rep['total_with_destinations_now'] == 5
    assert rep['total_source_only_now'] == 0

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    assert cur.fetchone()[0] >= 5
    conn.close()
