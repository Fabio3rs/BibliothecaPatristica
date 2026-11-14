"""
Unit tests for tools/indexing/pl011_zeno_optatus_locator.py
Sprint 52: PL011 Saint Zeno and Saint Optatus Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl011_zeno_optatus_locator import (
    PL011ZenoOptatusLocator,
    PL011_MAP,
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

    # Insert test items for PL011 (raw IDs)
    for key, targets in PL011_MAP.items():
        if not key.startswith('group:'):
            conn.execute('''
                INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
                VALUES (?, ?, 'PL011', 'INDEX ANALYTICUS', 'Test Label', 'Test Ref', 'source_only')
            ''', (key, f'group_{key[:8]}'))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    distinct_ids = {k for k in PL011_MAP if not k.startswith('group:')}
    assert len(distinct_ids) == 14

    for item_id, targets in PL011_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL011'
            assert page > 0
            assert 'pl011_' in reason


def test_resolve_item():
    locator = PL011ZenoOptatusLocator()

    # 1. Test Invidium concinnare (132 -> PL011 p. 193)
    item_id = '1074b64de0da6deec95647c3ec72e7288f69ffdafc2831931a8c2f3a1ba955d4'
    res = locator.resolve_item(item_id, 'Invidium concinnare, 132 et adn', '31')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL011'
    assert res[0]['physical_page'] == 193
    assert res[0]['target_file'].endswith('-193.txt')

    # 2. Test Tractatus breves paschali (XLIII, 238 -> PL011 p. 241)
    item_id = 'e9c2d14ad9c36cd71bddee545d63d7660f66f46cf2ed756ea113743d49489f73'
    res = locator.resolve_item(item_id, 'Tractatus cur breves paschali tempore', 'XLIII, 238')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL011'
    assert res[0]['physical_page'] == 241
    assert res[0]['target_file'].endswith('-241.txt')

    # 3. Test Petilianus (Hist 19 -> PL011 p. 412)
    item_id = '7e7b544b4c4d09db24385cda797c446aed9c7f85325220d244393695dc7ab3dc'
    res = locator.resolve_item(item_id, 'PETILIANUS Constantiensis episcopus', '19')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL011'
    assert res[0]['physical_page'] == 412
    assert res[0]['target_file'].endswith('-412.txt')

    # 4. Test Geogr 31 (Superest in vivis -> PL011 p. 425)
    item_id = '06407dd2368859c97845091d412027b479316737d5251d57011d518e61997842'
    res = locator.resolve_item(item_id, 'Superest in vivis usque ad annum circiter 355, Geogr', '31')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL011'
    assert res[0]['physical_page'] == 425
    assert res[0]['target_file'].endswith('-425.txt')

    # 5. Test Hist Collat 348 (et Hist. Collat -> PL011 p. 733)
    item_id = '6205994a80d61a304157dcf5ce2a9abb307c3d52e65fcde873e2b8cfa9edf5d7'
    res = locator.resolve_item(item_id, 'et Hist. Collat', '348')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL011'
    assert res[0]['physical_page'] == 733
    assert res[0]['target_file'].endswith('-733.txt')


def test_evaluate_and_apply(memory_db):
    locator = PL011ZenoOptatusLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 14

    for p in promotions:
        assert len(p['candidates']) >= 1
        cand = p['candidates'][0]
        assert cand['target_volume'] == 'PL011'
        assert cand['physical_page'] > 0
        assert cand['candidate_payload']['certified'] is True
