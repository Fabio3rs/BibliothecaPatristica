"""
Unit tests for tools/indexing/pl099_paulinus_aquileia_locator.py
Sprint 55: PL099 Saint Paulinus of Aquileia Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl099_paulinus_aquileia_locator import (
    PL099PaulinusAquileiaLocator,
    PL099_ITEMS_RAW,
    PL099_MAP,
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

    # Insert test items for PL099 (raw IDs)
    for item_id, (group_id, targets) in PL099_ITEMS_RAW.items():
        conn.execute('''
            INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
            VALUES (?, ?, 'PL099', 'INDEX IN S. PAULINUM', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id, group_id))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL099_ITEMS_RAW) == 13

    for item_id, (group_id, targets) in PL099_ITEMS_RAW.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL099'
            assert 1 <= page <= 660
            assert 'pl099_' in reason


def test_resolve_item():
    locator = PL099PaulinusAquileiaLocator()

    # 1. Test Symbolum proprium (Madrisius 234 -> PL099 p. 320 & p. 321)
    item_id = 'ae743443c06283302c849327cba6679af40bd562bf95c1b3fc9de7b5d42434f9'
    res = locator.resolve_item(item_id, 'Aquileiensis Ecclesia habebat symbolum fidei proprium', '234 et seq')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 320
    assert res[1]['target_volume'] == 'PL099'
    assert res[1]['physical_page'] == 321

    # 2. Test Maxentius Carolo Magno templum (Madrisius 260 -> PL099 p. 339 & p. 340)
    item_id = 'group:1125a879d3b92ff8bbd097052d7bcc2e549f411b62023409a3576219362b1bf5'
    res = locator.resolve_item(item_id, 'Aquileiense templum...', '260 seq')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 339
    assert res[1]['target_volume'] == 'PL099'
    assert res[1]['physical_page'] == 340

    # 3. Test Basnagus animadversio (Madrisius 228 -> PL099 p. 316)
    item_id = 'group:19d5617e31fc9ce2ce041a309c04c85c88391e5308caae35be8d7552dbfff020'
    res = locator.resolve_item(item_id, 'Basnagus (Jacobus)...', '228 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 316

    # 4. Test Bertrandus Patriarcha (Madrisius 269 -> PL099 p. 348)
    item_id = 'group:70eab67c188ca89122f4b57d51a806177a6420d554524077a38de26e584dc3d9'
    res = locator.resolve_item(item_id, 'Resarcuntur a B. Bertrando...', '269 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 348

    # 5. Test Raimundus Canonicis Utinensibus (Madrisius 266 -> PL099 p. 345)
    item_id = 'group:7f095ce48042c8d77091374ee14f97670db70ef4d3c9a27ae3c5fcced0222c1b'
    res = locator.resolve_item(item_id, 'Canonici Utinenses an. 1278...', '266 seq')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 345

    # 6. Test Scripturae mutilatae a Felice (Madrisius 110 -> PL099 p. 181 & p. 183)
    item_id = 'group:4a6f8d3c5204c2ebc8391374f2f13d8341d747a6b5382b2735c80d0926d6e99b'
    res = locator.resolve_item(item_id, 'Scripturæ mutilatæ a Felice', '110 et seqq')
    assert len(res) == 2
    assert res[0]['target_volume'] == 'PL099'
    assert res[0]['physical_page'] == 181
    assert res[1]['target_volume'] == 'PL099'
    assert res[1]['physical_page'] == 183


def test_evaluate_all(memory_db):
    locator = PL099PaulinusAquileiaLocator()
    promos = locator.evaluate_all(memory_db)
    assert len(promos) == 13
    for promo in promos:
        assert len(promo['candidates']) >= 1
