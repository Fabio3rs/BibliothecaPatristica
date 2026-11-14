"""
Unit tests for tools/indexing/pl066_benedict_rule_locator.py
Sprint 44: PL066 Saint Benedict Rule Commentary Locator
"""

import sqlite3
import pytest
from tools.indexing.pl066_benedict_rule_locator import (
    PL066BenedictRuleLocator,
    PL066_MAP,
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

    # Insert test items for PL066
    for item_id, targets in PL066_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PL066', 'INDEX GENERALIS', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL066_MAP) == 17
    # Verify all 17 items have target pages and descriptions
    for item_id, targets in PL066_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL066'
            assert 100 <= page <= 500
            assert 'Martène' in desc
            assert 'pl066_' in reason


def test_resolve_item():
    locator = PL066BenedictRuleLocator()
    # Test item with multiple targets (Ministeria monasterii)
    multi_id = 'group:3fb93fbfde498133779fba71206a386a4a5dac2249aedfa3bc163c20d9938985'
    res = locator.resolve_item(multi_id, 'Ministeria monasterii per monachos exercenda', '438 et seq., 625')
    assert len(res) == 2
    assert res[0]['physical_page'] == 289
    assert res[1]['physical_page'] == 364
    assert res[0]['target_file'].endswith('-289.txt')
    assert res[1]['target_file'].endswith('-364.txt')

    # Test single target item (Obedientia quomodo primus humilitatis gradus)
    single_id = '7bd16417e34aa59ed5ebd5783f3fe4b8b46bd1cc89bfdca8e21c6518eccfa796'
    res_single = locator.resolve_item(single_id, 'Obedientia quomodo primus humilitatis gradus', '1708')
    assert len(res_single) == 1
    assert res_single[0]['physical_page'] == 181
    assert res_single[0]['target_file'].endswith('-181.txt')


def test_evaluate_all(memory_db):
    locator = PL066BenedictRuleLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 17
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] == 'PL066'
            assert cand['candidate_payload']['certified'] is True
