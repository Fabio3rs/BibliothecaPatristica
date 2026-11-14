"""
Unit tests for tools/indexing/pl003_tertullian_locator.py
Sprint 45: PL003 Tertullian General Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl003_tertullian_locator import (
    PL003TertullianLocator,
    PL003_MAP,
)


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(':memory:')
    conn.execute('''
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
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

    # Insert test items for PL003
    for item_id, targets in PL003_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PL003', 'INDEX GENERALIS', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL003_MAP) == 17
    # Verify all 17 items have target pages and descriptions
    for item_id, targets in PL003_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol in ('PL001', 'PL002')
            assert 100 <= page <= 1100
            assert 'pl003_' in reason


def test_resolve_item():
    locator = PL003TertullianLocator()
    # Test item with multiple targets (Servos in exsequiis)
    multi_id = '153bf66e98e262c43a3e632e919f3d0be683c4f87042f7f9f64545cc46d215c3'
    res = locator.resolve_item(multi_id, 'Servos, captivos vel malo genio mercatos...', 'II, 1048 B. Vid')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PL001'
    assert res[0]['physical_page'] == 344
    assert res[1]['target_volume'] == 'PL002'
    assert res[1]['physical_page'] == 533

    # Test single target item with digit shift (Enoch apud Judam)
    enoch_id = 'group:20f47008f8077e86c5477dcc7b9d1f60173272757228ae8533b4baab7ad8c1be'
    res_enoch = locator.resolve_item(enoch_id, 'Apud Judam apostolum testimonium possidet', 'I, 1508 B.')
    assert len(res_enoch) == 1
    assert res_enoch[0]['target_volume'] == 'PL001'
    assert res_enoch[0]['physical_page'] == 675
    assert res_enoch[0]['target_file'].endswith('-675.txt')


def test_evaluate_all(memory_db):
    locator = PL003TertullianLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 17
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] in ('PL001', 'PL002')
            assert cand['candidate_payload']['certified'] is True
