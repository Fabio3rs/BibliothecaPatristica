"""
Unit tests for tools/indexing/pg069_cyril_glaphyra_locator.py
Sprint 49: PG069 Saint Cyril Glaphyra in Pentateuchum Locator
"""

import sqlite3
import pytest
from tools.indexing.pg069_cyril_glaphyra_locator import (
    PG069CyrilGlaphyraLocator,
    PG069_MAP,
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

    # Insert test items for PG069
    for item_id, targets in PG069_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PG069', 'INDEX ANALYTICUS', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PG069_MAP) == 15
    # Verify all 15 items have target pages, volumes, and descriptions
    for item_id, targets in PG069_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol == 'PG069'
            assert 10 <= page <= 400
            assert 'pg069_' in reason
            assert 'Aubert' in desc


def test_resolve_item():
    locator = PG069CyrilGlaphyraLocator()

    # 1. Test Creatio mundi (4 et seq -> PG069 p. 16)
    crea_id = 'group:d026ed0557f927615e8c121b78ec8c4b7304d813c67768254b20060ca7067472'
    res_crea = locator.resolve_item(crea_id, 'Creatio mundi describitur', '4 et seq')
    assert len(res_crea) == 1
    assert res_crea[0]['target_volume'] == 'PG069'
    assert res_crea[0]['physical_page'] == 16
    assert res_crea[0]['target_file'].endswith('-016.txt')

    # 2. Test Diluvium (30 et seq -> PG069 p. 44)
    dil_id = 'group:1bc5f13b1257003ca2da767e6d8299fb55ba3db78b953861af4beaaffa20e33c'
    res_dil = locator.resolve_item(dil_id, 'Diluvium. De diluvio', '30 et seq')
    assert len(res_dil) == 1
    assert res_dil[0]['target_volume'] == 'PG069'
    assert res_dil[0]['physical_page'] == 44
    assert res_dil[0]['target_file'].endswith('-044.txt')

    # 3. Test Abraham Isaac (82 et seqq -> PG069 p. 85)
    abr_id = 'group:6d7386396c39d59357a2f18c67e3f1864e952054b833d551e90234a30be1b182'
    res_abr = locator.resolve_item(abr_id, 'Abraham quomodo paratus fuerit immolare filium suum Isaac', '82 et seqq')
    assert len(res_abr) == 1
    assert res_abr[0]['target_volume'] == 'PG069'
    assert res_abr[0]['physical_page'] == 85
    assert res_abr[0]['target_file'].endswith('-085.txt')

    # 4. Test Agni paschalis (265 et seq -> PG069 p. 235)
    agn_id = 'group:57ed427e600c2fb3c957c17df730b8cb8fd2f7234c2b47cd427d294e19713ecc'
    res_agn = locator.resolve_item(agn_id, 'De manducatione agni paschalis', '265 et seq')
    assert len(res_agn) == 1
    assert res_agn[0]['target_volume'] == 'PG069'
    assert res_agn[0]['physical_page'] == 235
    assert res_agn[0]['target_file'].endswith('-235.txt')

    # 5. Test Exploratores (387 et seq -> PG069 p. 367)
    exp_id = 'group:b7a14444a5f7ec461ece1906611732099894b3589985c495c4682dd561bd2bf6'
    res_exp = locator.resolve_item(exp_id, 'Exploratores terræ promissionis quænam explorabant', '387 et seq')
    assert len(res_exp) == 1
    assert res_exp[0]['target_volume'] == 'PG069'
    assert res_exp[0]['physical_page'] == 367
    assert res_exp[0]['target_file'].endswith('-367.txt')


def test_evaluate_all(memory_db):
    locator = PG069CyrilGlaphyraLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 15
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] == 'PG069'
            assert cand['candidate_payload']['certified'] is True
