"""
Unit tests for tools/indexing/pl030_jerome_index_locator.py
Sprint 48: PL030 Saint Jerome Opera Omnia Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl030_jerome_index_locator import (
    PL030JeromeIndexLocator,
    PL030_MAP,
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

    # Insert test items for PL030
    for item_id, targets in PL030_MAP.items():
        conn.execute('''
            INSERT INTO items (id, volume, section, label, ref_raw, state)
            VALUES (?, 'PL030', 'INDEX GENERALIS', 'Test Label', 'Test Ref', 'source_only')
        ''', (item_id,))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    assert len(PL030_MAP) == 15
    # Verify all 15 items have target pages, volumes, and descriptions
    for item_id, targets in PL030_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol in ('PL022', 'PL023', 'PL024', 'PL025', 'PL026')
            assert 50 <= page <= 700
            assert 'pl030_' in reason
            assert 'Vallarsi' in desc


def test_resolve_item():
    locator = PL030JeromeIndexLocator()

    # 1. Test Jejunium Quadragesimale (616 et seq -> PL022 p. 461)
    jej_id = 'group:4554e2890b958b5a9d2437d18ccb3cb58e8c99f9bf74a4c0ba75965a03e45c59'
    res_jej = locator.resolve_item(jej_id, 'Jejunium Quadragesimale quale esse debeat', '616 et seq')
    assert len(res_jej) == 1
    assert res_jej[0]['target_volume'] == 'PL022'
    assert res_jej[0]['physical_page'] == 461
    assert res_jej[0]['target_file'].endswith('-461.txt')

    # 2. Test pere pretiosus est (ibid -> PL022 p. 653)
    pip_id = 'group:89604a8a63ae66b844fe75ddfaed91dbdbc2e7ee82ad7f4c8459488f2383e13d'
    res_pip = locator.resolve_item(pip_id, 'pere pretiosus est', 'ibid')
    assert len(res_pip) == 1
    assert res_pip[0]['target_volume'] == 'PL022'
    assert res_pip[0]['physical_page'] == 653
    assert res_pip[0]['target_file'].endswith('-653.txt')

    # 3. Test Romani de Aenea Asiano (VII, 437 -> PL026 p. 186)
    rom_id = '810cfb76790e86488d15d00276f035bdaa31126a7646999d90ff313f9ad2aa46'
    res_rom = locator.resolve_item(rom_id, 'Romani de Ænea Asiano generali', 'VII, 437')
    assert len(res_rom) == 1
    assert res_rom[0]['target_volume'] == 'PL026'
    assert res_rom[0]['physical_page'] == 186
    assert res_rom[0]['target_file'].endswith('-186.txt')

    # 4. Test Terebinthi mercatus (IV, 1065 -> PL024 p. 459)
    ter_id = 'b364f3bfd3a2b2ac32985993f35ef1660196953d85bee35bebe2161275baef88'
    res_ter = locator.resolve_item(ter_id, 'Terebinthi mercatus Judæis execrabilis', 'IV, 1065')
    assert len(res_ter) == 1
    assert res_ter[0]['target_volume'] == 'PL024'
    assert res_ter[0]['physical_page'] == 459
    assert res_ter[0]['target_file'].endswith('-459.txt')

    # 5. Test Thamar Palmyra (V, 601 -> PL025 p. 245)
    tha_id = 'f823cb12790dfd5d92e54ac54d51dad88483d317b49664738461432ede099bbe'
    res_tha = locator.resolve_item(tha_id, 'Thamar hodie Palmyra nuncupatur', 'V, 601')
    assert len(res_tha) == 1
    assert res_tha[0]['target_volume'] == 'PL025'
    assert res_tha[0]['physical_page'] == 245
    assert res_tha[0]['target_file'].endswith('-245.txt')


def test_evaluate_all(memory_db):
    locator = PL030JeromeIndexLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 15
    for promo in promotions:
        assert len(promo['candidates']) >= 1
        for cand in promo['candidates']:
            assert cand['target_volume'] in ('PL022', 'PL023', 'PL024', 'PL025', 'PL026')
            assert cand['candidate_payload']['certified'] is True
