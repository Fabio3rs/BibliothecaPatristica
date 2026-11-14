"""
Unit tests for tools/indexing/pl045_augustine_index_locator.py
Sprint 51: PL045 Saint Augustine Tomus Decimus Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl045_augustine_index_locator import (
    PL045AugustineIndexLocator,
    PL045_MAP,
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

    # Insert test items for PL045
    for key, targets in PL045_MAP.items():
        if key.startswith('region-entry:'):
            conn.execute('''
                INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
                VALUES (?, ?, 'PL045', 'INDEX RERUM QUÆ IN HOC DECIMO VOLUMINE CONTINENTUR', 'Test Label', 'Test Ref', 'source_only')
            ''', (key, f'region-group:{key.split(":")[-1]}'))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    distinct_ids = {k for k in PL045_MAP if k.startswith('region-entry:')}
    assert len(distinct_ids) == 14

    for item_id, targets in PL045_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL044'
            assert page > 0
            assert 'pl045_' in reason
            assert 'Tomus X' in desc


def test_resolve_item():
    locator = PL045AugustineIndexLocator()

    # 1. Test De Peccatorum Meritis (109-110 -> PL044 p. 59)
    item_id = 'region-entry:e3cfaf9442b936d908b1959129e20c0442af40a58be1897675ad8caac507c609'
    res = locator.resolve_item(item_id, 'S. AURELII AUGUSTINI... DE PECCATORUM MERITIS...', '109-110')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL044'
    assert res[0]['physical_page'] == 59
    assert res[0]['target_file'].endswith('-059.txt') or res[0]['target_file'].endswith('-59.txt')

    # 2. Test Perfecta justitia (162 -> PL044 p. 85)
    item_id = 'region-entry:7f51c16933744d23145b7a8db4fa0fc3c2bd5f22b0f3ac79a9713317eed8c1f7'
    res = locator.resolve_item(item_id, 'XII. Perfecta justitia humana imperfecta est', '162')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL044'
    assert res[0]['physical_page'] == 85
    assert res[0]['target_file'].endswith('-085.txt') or res[0]['target_file'].endswith('-85.txt')

    # 3. Test Caro, carnis affectus (ibid -> col 432, PL044 p. 220)
    item_id = 'region-entry:dcd22454e13ee02cb19ac535e2db39d2630548e07d2a2d2c84920cbf7a10b141'
    res = locator.resolve_item(item_id, 'XXXI. Caro, carnis affectus', 'ibid.')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL044'
    assert res[0]['physical_page'] == 220
    assert res[0]['target_file'].endswith('-220.txt')

    # 4. Test Voluptate peccat (558 -> PL044 p. 283)
    item_id = 'region-entry:0ab8ae4853722b593487665c88580f86285e0dc5e0429f72ec4b48e73340d8f0'
    res = locator.resolve_item(item_id, 'IX. Voluptate peccat qui solo timore non peccat', '558')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL044'
    assert res[0]['physical_page'] == 283
    assert res[0]['target_file'].endswith('-283.txt')

    # 5. Test Admonitio contra duas epistolas (547-548 -> PL044 p. 278)
    item_id = 'region-entry:66121f576220f19659fed593869987b48c5e5f45662aedca267a647a7a73fc35'
    res = locator.resolve_item(item_id, 'ADMONITIO IN OPUS CONTRA DUAS EPIST. PELAG', '547-548')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL044'
    assert res[0]['physical_page'] == 278
    assert res[0]['target_file'].endswith('-278.txt')


def test_evaluate_and_apply(memory_db):
    locator = PL045AugustineIndexLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 14

    for p in promotions:
        assert len(p['candidates']) >= 1
        cand = p['candidates'][0]
        assert cand['target_volume'] == 'PL044'
        assert cand['physical_page'] > 0
        assert cand['candidate_payload']['certified'] is True
