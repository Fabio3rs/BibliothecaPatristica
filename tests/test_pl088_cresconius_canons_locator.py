"""
Unit tests for tools/indexing/pl088_cresconius_canons_locator.py
Sprint 57: PL088 Cresconius and Ferrandus Canons Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl088_cresconius_canons_locator import (
    PL088CresconiusCanonsLocator,
    PL088_ITEMS_RAW,
    PL088_MAP,
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

    # Insert test items for PL088 (raw IDs)
    for item_id, (group_id, targets) in PL088_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
            VALUES (?, ?, 'PL088', 'INDEX RERUM ET VERBORUM', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id, group_id))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL088_ITEMS_RAW) == 12

    for item_id, (group_id, targets) in PL088_ITEMS_RAW.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL088'
            assert 400 <= page <= 500
            assert 'pl088_' in reason


def test_resolve_item():
    locator = PL088CresconiusCanonsLocator()

    # 1. Test Adam omnes laesit (Cresconius tit. 289 -> PL088 p. 473)
    item_id = 'region-entry:61dd070da8392c012bec8239d8a1353521034e063f260ab0c809f8c3bf9e3ff7'
    res = locator.resolve_item(item_id, 'Adam omnes læsiti', '289 —')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL088'
    assert res[0]['physical_page'] == 473

    # 2. Test Excommunicatio temeraria (Cresconius tit. 287 -> PL088 p. 472)
    item_id = 'region-entry:1cc4ddfca1a7fa65cf1b2f8517ef58bbfaf9171bf0e70c35d9c58734300ed92a'
    res = locator.resolve_item(item_id, 'Excommunicatio temeraria. —', '287 —')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL088'
    assert res[0]['physical_page'] == 472

    # 3. Test Imperatores idola abolento (Cresconius tit. 261 -> PL088 p. 469)
    item_id = 'region-entry:217af51a623e5bd387212a1eb3eb7601e2d2adec208d64633e717e3c7b12550d'
    res = locator.resolve_item(item_id, 'Imperatores idolorum templa abolento. —', '261 —')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL088'
    assert res[0]['physical_page'] == 469

    # 4. Test Jejunium a Pascha ad Pentecosten (Cresconius tit. 243 -> PL088 p. 457)
    item_id = 'region-entry:a61c5d4fe0a76a32fd1668b34a863a4b7f904664bf129b91b6f770666e115d7a'
    res = locator.resolve_item(item_id, 'Jejunium a Pascha ad Pentecosten nullum', '243 —')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL088'
    assert res[0]['physical_page'] == 457

    # 5. Test Filii parentes spernunt (Cresconius tit. 134 -> PL088 p. 447)
    item_id = 'region-entry:f4cdad2c2b7e0389d7d0ec1b7bbe151a3c2c3d3eec11805aa887c56de8ebb372'
    res = locator.resolve_item(item_id, 'Filii si parentes suos spernunt Christianitatis obtentu', '— —')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL088'
    assert res[0]['physical_page'] == 447


def test_evaluate_all(memory_db):
    locator = PL088CresconiusCanonsLocator()
    promos = locator.evaluate_all(memory_db)
    assert len(promos) == 12
    for promo in promos:
        assert len(promo['candidates']) >= 1
