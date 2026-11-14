"""
Unit tests for tools/indexing/pl072_liturgia_gallicana_locator.py
Sprint 47: PL072 Dom Mabillon De Liturgia Gallicana Locator
"""

import sqlite3
import pytest
from tools.indexing.pl072_liturgia_gallicana_locator import (
    PL072LiturgiaGallicanaLocator,
    PL072_MAP,
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

    # Insert test items for PL072
    for item_id, targets in PL072_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PL072', 'INDEX RERUM ET VERBORUM', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL072_MAP) == 16
    # Verify all 16 items have target pages and descriptions
    for item_id, targets in PL072_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PL072'
            assert 50 <= page <= 250
            assert 'pl072_' in reason
            assert 'Mabillon' in desc


def test_resolve_item():
    locator = PL072LiturgiaGallicanaLocator()
    # Test Ambrosiana liturg (7 et seqq)
    amb_id = 'group:49a35a54140b36bca7b07faeb616db8c4977f9db92679ffeb28f29c65fea684a'
    res_amb = locator.resolve_item(amb_id, 'Ambrosiana seu Mediolanensis liturg', '7 et seqq')
    assert len(res_amb) == 1
    assert res_amb[0]['physical_page'] == 62
    assert res_amb[0]['target_file'].endswith('-062.txt')

    # Test Gothicum Ordo Missae (Collectio ad pacem 188 et seqq)
    pacem_id = 'group:3a614f9eb3fdf1e7f36422ac36e762d2b00358c7e597a2490d7e70543a8129d1'
    res_pacem = locator.resolve_item(pacem_id, 'Collectio ad pacem', '188 et seqq')
    assert len(res_pacem) == 1
    assert res_pacem[0]['physical_page'] == 118
    assert res_pacem[0]['target_file'].endswith('-118.txt')

    # Test Cantiani martyrs (Protus Cantianorum martyrum paedagog)
    protus_id = 'group:ba67c0b8ed1727546328fe0c194fb77711a6e3fa309077d1bf90b1e36e9d98e7'
    res_protus = locator.resolve_item(protus_id, 'Protus Cantianorum martyrum pædagog', '468 et seqq')
    assert len(res_protus) == 1
    assert res_protus[0]['physical_page'] == 225
    assert res_protus[0]['target_file'].endswith('-225.txt')


def test_evaluate_all(memory_db):
    locator = PL072LiturgiaGallicanaLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 16
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] == 'PL072'
            assert cand['candidate_payload']['certified'] is True
