"""
Tests for PG140 Nicetas Choniates Locator (Sprint 99)
"""

import os
import pytest
from tools.indexing.pg140_nicetas_choniates_locator import (
    PG140_ITEMS_RAW,
    resolve_pg140_items,
    find_physical_file,
)


def test_pg140_items_raw_count():
    assert len(PG140_ITEMS_RAW) == 4


def test_find_physical_file():
    p391 = find_physical_file('PG139', 391)
    assert p391 is not None
    assert os.path.exists(p391)
    assert '391' in p391

    p412 = find_physical_file('PG139', 412)
    assert p412 is not None
    assert os.path.exists(p412)
    assert '412' in p412

    p795 = find_physical_file('PG140', 795)
    assert p795 is not None
    assert os.path.exists(p795)
    assert '795' in p795


def test_resolve_pg140_items():
    resolved = resolve_pg140_items()
    assert len(resolved) == 4

    for item_id, data in resolved.items():
        assert data['state'] == 'with_destinations'
        candidates = data['candidates']
        assert len(candidates) >= 3
        for cand in candidates:
            assert os.path.exists(cand['file'])
            assert cand['score'] > 0
            assert cand['navigable'] is True
            assert 'description' in cand


def test_balsamon_and_isaacius_angelus_resolution():
    resolved = resolve_pg140_items()
    bals_id = 'group:456b5d046aa8fd109b9cc36799464adb0d15e280cc524bb9fdc50107d0d56d74'
    assert bals_id in resolved
    cands = resolved[bals_id]['candidates']
    assert cands[0]['volume'] == 'PG139'
    assert cands[0]['physical_page'] == 391
    assert any(c['volume'] == 'PG140' and c['physical_page'] == 795 for c in cands)


def test_manuel_morte_praesagium_typo_resolution():
    resolved = resolve_pg140_items()
    praes_id = '5c7076160fc60ed264d52e9075c685187df2c3fff70329eea897c825e677943d'
    assert praes_id in resolved
    cands = resolved[praes_id]['candidates']
    assert cands[0]['volume'] == 'PG139'
    assert cands[0]['physical_page'] == 162
    assert any(c['volume'] == 'PG139' and c['physical_page'] == 292 for c in cands)
