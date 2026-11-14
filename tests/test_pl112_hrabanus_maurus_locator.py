"""
Unit tests for tools/indexing/pl112_hrabanus_maurus_locator.py
Sprint 50: PL112 Rabano Mauro Opera Omnia Index Locator
"""

import sqlite3
import pytest
from tools.indexing.pl112_hrabanus_maurus_locator import (
    PL112HrabanusMaurusLocator,
    PL112_MAP,
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

    # Insert test items for PL112 (using raw SHA256 IDs)
    for key, targets in PL112_MAP.items():
        if not key.startswith('group:'):
            conn.execute('''
                INSERT INTO items (id, group_id, volume, section, label, ref_raw, state)
                VALUES (?, ?, 'PL112', 'INDEX RERUM ET VERBORUM', 'Test Label', 'Test Ref', 'source_only')
            ''', (key, f'group_{key[:8]}'))

    conn.commit()
    yield conn
    conn.close()


def test_map_completeness():
    # 14 distinct entries, each with id and group:group_id keys (total 28 keys)
    distinct_ids = {k for k in PL112_MAP if not k.startswith('group:')}
    assert len(distinct_ids) == 14

    for item_id, targets in PL112_MAP.items():
        assert len(targets) >= 1
        for vol, page, desc, reason in targets:
            assert vol in ('PL107', 'PL108', 'PL109', 'PL110', 'PL111', 'PL112')
            assert page > 0
            assert 'pl112_' in reason
            assert 'Tomus' in desc


def test_resolve_item():
    locator = PL112HrabanusMaurusLocator()

    # 1. Test Oculorum sensus (V, 939 -> PL111 p. 472)
    item_id = '0b19274eae24d898699a7982e02aec3f908f52fd906075293b6428bd29b5aebf'
    res = locator.resolve_item(item_id, 'Oculorum sensus reliquis præstantior', 'V, 939')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL111'
    assert res[0]['physical_page'] == 472
    assert res[0]['target_file'].endswith('-472.txt') or res[0]['target_file'].endswith('-0472.txt')

    # 2. Test Odor Christi (VI, 169 -> PL112 p. 89)
    item_id = '1e241bff6613ee0d55c734419b2650342ae965f4ce90b72309df96b94a35488b'
    res = locator.resolve_item(item_id, 'Odor Christi quis', 'VI, 169')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL112'
    assert res[0]['physical_page'] == 89
    assert res[0]['target_file'].endswith('-089.txt') or res[0]['target_file'].endswith('-89.txt')

    # 3. Test Ostium Christus (V, 1367 -> PL111 p. 686)
    item_id = 'b3e21ceb825adccf125bedd72c5077e87fc8ff38bbc1ee550d397fb78cdd6877'
    res = locator.resolve_item(item_id, 'Ostium qui Christus dictus', 'V, 1367')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL111'
    assert res[0]['physical_page'] == 686
    assert res[0]['target_file'].endswith('-686.txt') or res[0]['target_file'].endswith('-0686.txt')

    # 4. Test Parricidarum gravitas (VI, 1410 -> PL112 p. 709)
    item_id = '8fe16ca5d8c6c76ee98f40b7d841eb31bf8ac3f3acde167666222249dccffce8'
    res = locator.resolve_item(item_id, 'Parricidarum gravitas et pœnitentia in conc. Mogunt.', 'VI, 1410')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL112'
    assert res[0]['physical_page'] == 709
    assert res[0]['target_file'].endswith('-709.txt') or res[0]['target_file'].endswith('-0709.txt')

    # 5. Test Poma nova et veterata (II, 997 -> PL108 p. 508)
    item_id = 'd452c41bb416df7431ceffe781d7673f8b8fe0a2a6725af5f3932422c9b65e86'
    res = locator.resolve_item(item_id, 'Poma nova et veterata comedentes qui', 'II, 997')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL108'
    assert res[0]['physical_page'] == 508
    assert res[0]['target_file'].endswith('-508.txt') or res[0]['target_file'].endswith('-0508.txt')

    # 6. Test Gedeon explorat castra (II, 1165 -> PL108 p. 592)
    item_id = 'd47a4b0c7593366592526107598926e94023881d846d3459075a7c97340d0eb3'
    res = locator.resolve_item(item_id, 'Explorat hostium castra cum Phara famulo', 'II, 1165')
    assert len(res) == 1
    assert res[0]['target_volume'] == 'PL108'
    assert res[0]['physical_page'] == 592
    assert res[0]['target_file'].endswith('-592.txt') or res[0]['target_file'].endswith('-0592.txt')


def test_evaluate_and_apply(memory_db):
    locator = PL112HrabanusMaurusLocator()
    promotions = locator.evaluate_all(memory_db)
    assert len(promotions) == 14

    for p in promotions:
        assert len(p['candidates']) >= 1
        cand = p['candidates'][0]
        assert cand['target_volume'] in ('PL108', 'PL111', 'PL112')
        assert cand['physical_page'] > 0
        assert cand['candidate_payload']['certified'] is True
