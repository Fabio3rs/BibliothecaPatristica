"""
Unit tests for tools/indexing/pg084_theodoret_scripture_locator.py
Sprint 53: PG084 Theodoret of Cyrrhus Scripture Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pg084_theodoret_scripture_locator import (
    PG084TheodoretScriptureLocator,
    PG084_MAP,
)


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(':memory:')
    conn.execute('''
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            group_id TEXT,
            volume TEXT,
            section TEXT,
            label TEXT,
            ref_raw TEXT,
            state TEXT
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

    # Insert test items for PG084 (raw IDs)
    for key, targets in PG084_MAP.items():
        if not key.startswith('group:'):
            conn.execute('''
                INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
                VALUES (?, ?, 'PG084', 'INDEX LOCORUM BIBLICORUM', 'Test Label', 'Test Ref', 'source_only')
            ''', (key, f'group_{key[:8]}'))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    distinct_ids = {k for k in PG084_MAP if not k.startswith('group:')}
    assert len(distinct_ids) == 14

    for item_id, targets in PG084_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol in {'PG080', 'PG081', 'PG082', 'PG083', 'PG084'}
            assert page > 0
            assert 'pg084_' in reason


def test_resolve_item():
    locator = PG084TheodoretScriptureLocator()

    # 1. Test In Michaeam 1605 (II, 1605 -> PG081 p. 867)
    item_id = 'ef11e8fb2d5508515199fc7c1c40c1ea177c5bab84eb00181e52321ecae163c8'
    res = locator.resolve_item(item_id, 'II', '1605')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PG081'
    assert res[0]['physical_page'] == 867
    assert res[0]['target_file'].endswith('-0867.txt') or res[0]['target_file'].endswith('-867.txt')

    # 2. Test In Psal. XXXII & In Jerem. XVI (I. 1093. II | 1368 -> PG080 p. 562 & PG081 p. 728)
    item_id = '47ae67b1d0fe24f76f05a957a5a5159c996bdaf3b496f97d0945ee6bad9f332f'
    res = locator.resolve_item(item_id, 'I. 1093. II', '1368')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PG080'
    assert res[0]['physical_page'] == 562
    assert res[1]['target_volume'] == 'PG081'
    assert res[1]['physical_page'] == 728

    # 3. Test In Danielem II (II | 1500 -> PG081 p. 808)
    item_id = '0281bb644892b8d12c365c29dba32af0960048dffedacd9cad228f3e226700ce'
    res = locator.resolve_item(item_id, 'II', '1500')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PG081'
    assert res[0]['physical_page'] == 808

    # 4. Test In Hebr. I (IV | 1307 -> PG083 p. 658)
    item_id = '0057d99788093dc1a1e0503f752df718e805566f12bb31c93a055fc5a2a07c13'
    res = locator.resolve_item(item_id, 'IV', '1307')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PG083'
    assert res[0]['physical_page'] == 658

    # 5. Test Dialogus III & In Hebr. (IV, 273 ... -> PG083 p. 141 & p. 658)
    item_id = '5056440b666168a4bf3dac629406120d4a1cb67eaca9b5fcf3039e242055f5d5'
    res = locator.resolve_item(item_id, 'IV, 273 (ad ἀπάθειαν λόγου)', '1307')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PG083'
    assert res[0]['physical_page'] == 141
    assert res[1]['target_volume'] == 'PG083'
    assert res[1]['physical_page'] == 658


def test_evaluate_all(memory_db):
    locator = PG084TheodoretScriptureLocator()
    promos = locator.evaluate_all(memory_db)
    assert len(promos) == 14
    for promo in promos:
        assert len(promo['candidates']) >= 1
