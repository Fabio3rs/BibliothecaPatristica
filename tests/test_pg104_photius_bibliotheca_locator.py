"""
Tests for Sprint 77: PG104 Photius Bibliotheca Locator
"""

import json
import os
import sqlite3
import pytest
from tools.indexing.pg104_photius_bibliotheca_locator import (
    PG104_ITEMS_RAW,
    resolve_pg104_item,
    get_physical_file,
    build_candidate_json,
    execute_sprint77,
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

    for item_id, (group_id, dests) in PG104_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, source, state, metadata_json)
            VALUES (?, ?, 'PG104', 'INDEX ANALYTICUS', 'Test Label', 'Test Ref', 'dummy_source.txt', 'source_only', '{}')
        ''', (item_id, group_id))

    conn.commit()
    conn.close()

    summary_data = {
        'total_items': len(PG104_ITEMS_RAW),
        'items_with_destinations': 0,
        'items_source_only': len(PG104_ITEMS_RAW),
        'candidate_storage_records': 0
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f)

    return db_file, summary_file


def test_pg104_items_structure():
    assert len(PG104_ITEMS_RAW) == 7
    for item_id, (group_id, dests) in PG104_ITEMS_RAW.items():
        assert group_id
        assert len(dests) >= 1
        for vol, page, col_desc, reason in dests:
            assert vol in ('PG103', 'PG104')
            assert 1 <= page <= 850
            assert 'Photii' in col_desc
            assert reason.startswith('pg104_')


def test_pg104_resolution():
    res = resolve_pg104_item('opening:4037c6e2bb459ab5f426eb8fe60da8a5ca18246d2cddd0f61ad984ac6853e18f')
    assert res is not None
    group_id, dests = res
    assert group_id == 'opening:4037c6e2bb459ab5f426eb8fe60da8a5ca18246d2cddd0f61ad984ac6853e18f'
    assert len(dests) == 1
    vol, page, col_desc, reason, fpath = dests[0]
    assert vol == 'PG103'
    assert page == 186
    assert os.path.exists(fpath)


def test_pg104_all_physical_files_exist():
    for item_id, (group_id, dests) in PG104_ITEMS_RAW.items():
        res = resolve_pg104_item(item_id)
        assert res is not None
        _, resolved_dests = res
        for vol, page, col_desc, reason, fpath in resolved_dests:
            assert os.path.exists(fpath), f"File missing for {vol} p.{page}: {fpath}"


def test_pg104_dry_run(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint77(apply=False, db_path=db_path, summary_path=summary_path)
    assert report['volume'] == 'PG104'
    assert report['sprint'] == 77
    assert report['total_source_only_initial'] == 7
    assert report['resolved_count'] == 7
    assert report['candidates_created'] == 0


def test_pg104_apply(mock_db):
    db_path, summary_path = mock_db
    report = execute_sprint77(apply=True, db_path=db_path, summary_path=summary_path)
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
