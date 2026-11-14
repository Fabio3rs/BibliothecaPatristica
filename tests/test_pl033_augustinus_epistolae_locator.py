"""
Tests for Sprint 68: PL033 S. Augustini Epistolae Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pl033_augustinus_epistolae_locator import (
    PL033_ITEMS_RAW,
    resolve_pl033_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint68,
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

    for item_id, (group_id, dests) in PL033_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PL033', 'INDEX RERUM ET VERBORUM', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PL033_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PL033_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pl033_items_structure():
    assert len(PL033_ITEMS_RAW) == 9
    for item_id, (group_id, dests) in PL033_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol == 'PL033'
            assert 1 <= page <= 600
            assert 'col' in col_desc
            assert reason.startswith('pl033_augustinus_')


def test_pl033_resolution():
    res = resolve_pl033_item('group:0877d3732e71caf44a2182884929be13a9345aac18bb19ec44115dae543fcde4')
    assert res is not None
    group_id, dests = res
    assert group_id == 'group:0877d3732e71caf44a2182884929be13a9345aac18bb19ec44115dae543fcde4'
    assert len(dests) == 1
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PL033'
    assert page == 37
    assert os.path.exists(fpath)


def test_pl033_all_physical_files_exist():
    for item_id, (group_id, dests) in PL033_ITEMS_RAW.items():
        res = resolve_pl033_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pl033_dry_run(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint68(apply=False, db_path=db_path, summary_path=summary_path)
    assert report['volume'] == 'PL033'
    assert report['sprint'] == 68
    assert report['total_source_only_initial'] == 9
    assert report['resolved_count'] == 9
    assert report['candidates_created'] == 0


def test_pl033_apply(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint68(apply=True, db_path=db_path, summary_path=summary_path)
    assert report['resolved_count'] == 9
    assert report['candidates_created'] == 9
    assert report['applied'] is True

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    assert cur.fetchone()[0] == 9
    conn.close()
