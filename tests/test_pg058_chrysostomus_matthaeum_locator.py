"""
Tests for PG058 S. Joannes Chrysostomus Locator (Sprint 93)
"""

import os
import pytest
from tools.indexing.pg058_chrysostomus_matthaeum_locator import (
    PG058_ITEMS_RAW,
    resolve_pg058_items,
    find_physical_file,
)


def test_pg058_items_raw_count():
    assert len(PG058_ITEMS_RAW) == 5


def test_find_physical_file():
    p320 = find_physical_file('PG058', 320)
    assert p320 is not None
    assert os.path.exists(p320)
    assert '320' in p320

    p121 = find_physical_file('PG057', 121)
    assert p121 is not None
    assert os.path.exists(p121)
    assert '121' in p121


def test_resolve_pg058_items():
    resolved = resolve_pg058_items()
    assert len(resolved) == 5

    for item_id, data in resolved.items():
        assert data['state'] == 'with_destinations'
        candidates = data['candidates']
        assert len(candidates) >= 2
        for cand in candidates:
            assert os.path.exists(cand['file'])
            assert cand['score'] > 0
            assert cand['navigable'] is True
            assert 'description' in cand


def test_plutarch_comparative_item_resolution():
    resolved = resolve_pg058_items()
    item_id = 'region-entry:af5788cceaf230676d1280b09ba7b67e67a737308eaf571aecd7ccdc7d03c582'
    assert item_id in resolved
    data = resolved[item_id]
    cands = data['candidates']
    # Primarius is PG057 p. 121 [154]
    assert cands[0]['volume'] == 'PG057'
    assert cands[0]['physical_page'] == 121
    # Index source is PG058 p. 402
    assert any(c['volume'] == 'PG058' and c['physical_page'] == 402 for c in cands)


def test_bracket_page_856_857_resolution():
    resolved = resolve_pg058_items()
    item_856 = 'region-entry:dae26f49676b9027d4af883e8d9107c78d85208ec89e4a978fc98a84253f3dcc'
    assert item_856 in resolved
    cands_856 = resolved[item_856]['candidates']
    assert cands_856[0]['volume'] == 'PG058'
    assert cands_856[0]['physical_page'] == 320
