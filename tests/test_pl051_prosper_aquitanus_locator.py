"""
Tests for Sprint 91: PL051 S. Prosper Aquitanus Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pl051_prosper_aquitanus_locator import (
    PL051_ITEMS_RAW,
    resolve_pl051_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint91,
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

    for item_id, (group_id, dests) in PL051_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PL051', 'INDEX RERUM', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PL051_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PL051_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pl051_items_structure():
    assert len(PL051_ITEMS_RAW) == 5
    for item_id, (group_id, dests) in PL051_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol == 'PL051'
            assert 1 <= page <= 600
            assert reason.startswith('pl051_')


def test_pl051_resolution():
    res = resolve_pl051_item('e5105ef01c8b6b28f55bf28fcfcae1eeb8bf30b569a37fb08fd23d63f4c6a011')
    assert res is not None
    group_id, dests = res
    assert group_id == '519599032874dfb62b5facb7d006fd493b8902cac6b72d3fc02ec741f5bd750a'
    assert len(dests) == 1
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PL051'
    assert page == 60
    assert os.path.exists(fpath)


def test_pl051_all_physical_files_exist():
    for item_id, (group_id, dests) in PL051_ITEMS_RAW.items():
        res = resolve_pl051_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pl051_dry_run(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint91(apply=False, db_path=db_file, summary_path=summary_file)
    assert rep['resolved_count'] == 5
    assert rep['candidates_created'] == 0


def test_pl051_apply(mock_db):
    db_file, summary_file = mock_db
    rep = execute_sprint91(apply=True, db_path=db_file, summary_path=summary_file)
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
