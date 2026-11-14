"""
Unit tests for tools/indexing/pg086_02_leontius_locator.py
Sprint 46: PG086.02 Leontius of Byzantium & Evagrius Scholasticus Locator
"""

import sqlite3
import pytest
from tools.indexing.pg086_02_leontius_locator import (
    PG08602LeontiusLocator,
    PG086_02_MAP,
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

    # Insert test items for PG086.02
    for item_id, targets in PG086_02_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PG086.02', 'INDEX AUCTORUM', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PG086_02_MAP) == 17
    # Verify all 17 items have target pages and descriptions
    for item_id, targets in PG086_02_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol in ('PG086.01', 'PG086.02')
            assert 400 <= page <= 700
            assert 'pg086_02_' in reason


def test_resolve_item():
    locator = PG08602LeontiusLocator()
    # Test item with multiple targets (C'Politana Patrum 150 & Ephesina)
    multi_id = 'c1ae7ce8fdd8657a9c3d9e7353d143d56ebada58c021360ef043d5ef67d08aeb'
    res = locator.resolve_item(multi_id, 'C’Politana Patrum 150: 1219. Ephesina', '1222')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PG086.01'
    assert res[0]['physical_page'] == 616
    assert res[1]['target_volume'] == 'PG086.01'
    assert res[1]['physical_page'] == 617

    # Test Evagrius benchmark pagination (Armatus cognatus Verinae)
    armatus_id = '8d3c5a2b0c6f2e9648162b251abcc55c15664a19e68939d7dfdad142d31a567a'
    res_arm = locator.resolve_item(armatus_id, 'Armatus cognatus Verinæ August. a Zenone Aug. interficitur', '358')
    assert len(res_arm) == 1
    assert res_arm[0]['target_volume'] == 'PG086.02'
    assert res_arm[0]['physical_page'] == 452
    assert res_arm[0]['target_file'].endswith('-452.txt')


def test_evaluate_all(memory_db):
    locator = PG08602LeontiusLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 17
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] in ('PG086.01', 'PG086.02')
            assert cand['candidate_payload']['certified'] is True
