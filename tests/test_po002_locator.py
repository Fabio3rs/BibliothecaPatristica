"""
Unit tests for tools/indexing/po002_locator.py
Sprint 54: PO002 Persian Martyrs Greek Index Locator
"""

import sqlite3
import pytest
from tools.indexing.po002_locator import (
    PO002Locator,
    PO002_MAP,
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

    # Insert test items for PO002 (raw IDs)
    for key, targets in PO002_MAP.items():
        if not key.startswith('group:'):
            conn.execute('''
                INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
                VALUES (?, ?, 'PO002', 'INDEX DES NOMS PROPRES', 'Test Label', 'Test Ref', 'source_only')
            ''', (key, f'group_{key[:8]}'))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    distinct_ids = {k for k in PO002_MAP if not k.startswith('group:')}
    assert len(distinct_ids) == 13

    for item_id, targets in PO002_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PO002'
            assert 413 <= page <= 572
            assert 'po002_' in reason


def test_resolve_item():
    locator = PO002Locator()

    # 1. Test Adelpher cross-reference (printed 450, 452 -> PO002 phys p. 460 & p. 462)
    item_id = 'region-entry:0abeb4cb477d151227c47a0af465e438c6a823a6a3e272e01f2122c3d9e0ac95'
    res = locator.resolve_item(item_id, '᾿Αδελφέρ', 'voir Ἀδερφέρ.')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PO002'
    assert res[0]['physical_page'] == 460
    assert res[1]['target_volume'] == 'PO002'
    assert res[1]['physical_page'] == 462

    # 2. Test Zarhus note to Zerath (printed 505 -> PO002 phys p. 515)
    item_id = 'region-entry:0a6307c0e9e5be8fd2e862467d46d6e3db9a34452cb62214ed08b1531b2612d0'
    res = locator.resolve_item(item_id, 'A : ܙܪܚܘܫ Zarhus; Bᵃ II', '380 :')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PO002'
    assert res[0]['physical_page'] == 515

    # 3. Test Iasa kome (printed 421 & 437 -> PO002 phys p. 431 & p. 447)
    item_id = 'region-entry:11aaef48ac0ccd406d28f477cebccf5650f4a0f28cb26f26456253adac1a2da4'
    res = locator.resolve_item(item_id, 'Ἰασὰ κώμη', '421, Ἴασσα : 437.')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PO002'
    assert res[0]['physical_page'] == 431
    assert res[1]['target_volume'] == 'PO002'
    assert res[1]['physical_page'] == 447

    # 4. Test Thelam kome (printed 451 & 452 -> PO002 phys p. 461 & p. 462)
    item_id = 'region-entry:95c61b350245b329a001322e549b47396b6017b790f1cee9ccfcffc8d048742b'
    res = locator.resolve_item(item_id, 'Θέλαμ κώμη', '451, 18 ; Θέλαμος : 452, 31')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PO002'
    assert res[0]['physical_page'] == 461
    assert res[1]['target_volume'] == 'PO002'
    assert res[1]['physical_page'] == 462

    # 5. Test Itkker mone / Bisaker (printed 453 -> PO002 phys p. 463)
    item_id = 'region-entry:dfba96c23cce7c02f3322713e67c7a9df976e0f688b623167049829c26ea1fec'
    res = locator.resolve_item(item_id, 'ἰτκκὲρ μονέ', '453, ; Βισακερθά : 63')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PO002'
    assert res[0]['physical_page'] == 463


def test_evaluate_all(memory_db):
    locator = PO002Locator()
    promos = locator.evaluate_all(memory_db)
    assert len(promos) == 13
    for promo in promos:
        assert len(promo['candidates']) >= 1
