"""
Unit tests for tools/indexing/pl060_dracontius_arevalo_locator.py
Sprint 56: PL060 Blossius Aemilius Dracontius Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl060_dracontius_arevalo_locator import (
    PL060DracontiusArevaloLocator,
    PL060_ITEMS_RAW,
    PL060_MAP,
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

    # Insert test items for PL060 (raw IDs)
    for item_id, (group_id, targets) in PL060_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
            VALUES (?, ?, 'PL060', 'INDEX ONOMASTICUS', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id, group_id))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL060_ITEMS_RAW) == 13

    for item_id, (group_id, targets) in PL060_ITEMS_RAW.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL060'
            assert 300 <= page <= 550
            assert 'pl060_' in reason


def test_resolve_item():
    locator = PL060DracontiusArevaloLocator()

    # 1. Test Cincinnamum / Cinnamomum (Arevalo 161 -> PL060 p. 375)
    item_id = 'group:a7bfdb6743f4e2dcce31cccf0aa3603650292fc2379157c6c46e6bb97a5b3e4a'
    res = locator.resolve_item(item_id, 'Cincinnamum, cinnamomum...', '161 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL060'
    assert res[0]['physical_page'] == 375

    # 2. Test In igne apparuit (Arevalo 291 -> PL060 p. 432)
    item_id = 'group:ab08118785f8b41d0b7ca6a5893b23bfaefeb2dea3c7e6cbbbc6b3b425861a0c'
    res = locator.resolve_item(item_id, 'In igne apparuit', '291 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL060'
    assert res[0]['physical_page'] == 432

    # 3. Test Poetarum licentia (Arevalo 105 -> PL060 p. 350)
    item_id = 'group:19f76a082acb7a8b41441caad249fc0f43926c17c62fbfeca3e50263a91c44a3'
    res = locator.resolve_item(item_id, 'Poetarum Christianorum licentia...', '105 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL060'
    assert res[0]['physical_page'] == 350

    # 4. Test Mutius Scevola (Arevalo 336 -> PL060 p. 452)
    item_id = 'group:be939f6fa795a5937fe3b0fa4bf7adda8661180a0325658562949dfe40c61975'
    res = locator.resolve_item(item_id, 'Multius Scevola', '336 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL060'
    assert res[0]['physical_page'] == 452

    # 5. Test Linguae vulgares (Arevalo 340 -> PL060 p. 454)
    item_id = 'group:7bd28279214cd51ae7b40c4b2f44332bb7023444fd1c371402a98e0ffd9436cd'
    res = locator.resolve_item(item_id, 'Linguæ vulgares retinent plures phrases...', '340 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL060'
    assert res[0]['physical_page'] == 454


def test_evaluate_all(memory_db):
    locator = PL060DracontiusArevaloLocator()
    promos = locator.evaluate_all(memory_db)
    assert len(promos) == 13
    for promo in promos:
        assert len(promo['candidates']) >= 1
