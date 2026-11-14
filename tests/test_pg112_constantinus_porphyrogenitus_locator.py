"""
Tests for PG112 Constantinus Porphyrogenitus Locator (Sprint 100)
"""

import os
import pytest
from tools.indexing.pg112_constantinus_porphyrogenitus_locator import (
    PG112_ITEMS_RAW,
    resolve_pg112_items,
    find_physical_file,
)


def test_pg112_items_raw_count():
    assert len(PG112_ITEMS_RAW) == 4


def test_find_physical_file():
    p642 = find_physical_file('PG112', 642)
    assert p642 is not None
    assert os.path.exists(p642)
    assert '642' in p642

    p582 = find_physical_file('PG112', 582)
    assert p582 is not None
    assert os.path.exists(p582)
    assert '582' in p582

    p419 = find_physical_file('PG112', 419)
    assert p419 is not None
    assert os.path.exists(p419)
    assert '419' in p419


def test_resolve_pg112_items():
    resolved = resolve_pg112_items()
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


def test_inscriptiones_ad_exteros_resolution():
    resolved = resolve_pg112_items()
    insc_id = 'group:78624dafbb7975e242446dd080589e0327d77496537e5de2c18d4baeb21ba680'
    assert insc_id in resolved
    cands = resolved[insc_id]['candidates']
    assert cands[0]['volume'] == 'PG112'
    assert cands[0]['physical_page'] == 642
    assert any(c['physical_page'] == 736 for c in cands)


def test_castra_and_prohedrus_resolution():
    resolved = resolve_pg112_items()
    castra_id = 'group:bfd19c1e5c8c3d2004f12ce9a0118264d8fac58d0bad3ac8a8cb5aacf54d2c6e'
    assert castra_id in resolved
    cands_castra = resolved[castra_id]['candidates']
    assert cands_castra[0]['physical_page'] == 419

    proh_id = 'group:a518aae256fb40340c726531d6e976dd5f01a470af224dc2afbe72d5cbf2378b'
    assert proh_id in resolved
    cands_proh = resolved[proh_id]['candidates']
    assert cands_proh[0]['physical_page'] == 418
