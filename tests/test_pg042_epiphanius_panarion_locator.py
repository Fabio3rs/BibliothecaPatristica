"""
Tests for PG042 S. Epiphanius Constantiensis Locator (Sprint 94)
"""

import os
import pytest
from tools.indexing.pg042_epiphanius_panarion_locator import (
    PG042_ITEMS_RAW,
    resolve_pg042_items,
    find_physical_file,
)


def test_pg042_items_raw_count():
    assert len(PG042_ITEMS_RAW) == 5


def test_find_physical_file():
    p581 = find_physical_file('PG041', 581)
    assert p581 is not None
    assert os.path.exists(p581)
    assert '581' in p581

    p775 = find_physical_file('PG042', 775)
    assert p775 is not None
    assert os.path.exists(p775)
    assert '775' in p775


def test_resolve_pg042_items():
    resolved = resolve_pg042_items()
    assert len(resolved) == 5

    for item_id, data in resolved.items():
        assert data['state'] == 'with_destinations'
        candidates = data['candidates']
        assert len(candidates) >= 3
        for cand in candidates:
            assert os.path.exists(cand['file'])
            assert cand['score'] > 0
            assert cand['navigable'] is True
            assert 'description' in cand


def test_epiphanius_passio_and_resurrectio():
    resolved = resolve_pg042_items()
    passio_id = '45c11871e08ed770de0f20046ae5b3d3c52ae30cd94d4ee6bf770cb544bffc26'
    assert passio_id in resolved
    cands = resolved[passio_id]['candidates']
    assert cands[0]['volume'] == 'PG041'
    assert cands[0]['physical_page'] == 581
    assert any(c['volume'] == 'PG042' and c['physical_page'] == 775 for c in cands)


def test_epiphanius_christi_vitae_summa():
    resolved = resolve_pg042_items()
    vitae_id = '203dfcd91fbb1fd03aa3f3cdae56bad755ef3327e2684756f11ecaee781a2656'
    assert vitae_id in resolved
    cands = resolved[vitae_id]['candidates']
    assert cands[0]['volume'] == 'PG041'
    assert cands[0]['physical_page'] == 590
    assert any(c['volume'] == 'PG042' and c['physical_page'] == 776 for c in cands)
