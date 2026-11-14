"""
Tests for PL057 S. Maximus Taurinensis Locator (Sprint 97)
"""

import os
import pytest
from tools.indexing.pl057_maximus_taurinensis_locator import (
    PL057_ITEMS_RAW,
    resolve_pl057_items,
    find_physical_file,
)


def test_pl057_items_raw_count():
    assert len(PL057_ITEMS_RAW) == 4


def test_find_physical_file():
    p377 = find_physical_file('PL057', 377)
    assert p377 is not None
    assert os.path.exists(p377)
    assert '377' in p377

    p227 = find_physical_file('PL057', 227)
    assert p227 is not None
    assert os.path.exists(p227)
    assert '227' in p227

    p284 = find_physical_file('PL057', 284)
    assert p284 is not None
    assert os.path.exists(p284)
    assert '284' in p284

    p323 = find_physical_file('PL057', 323)
    assert p323 is not None
    assert os.path.exists(p323)
    assert '323' in p323


def test_resolve_pl057_items():
    resolved = resolve_pl057_items()
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


def test_pudicitia_castitas_resolution():
    resolved = resolve_pl057_items()
    pud_id = 'group:10063b50d6506bf896a9ec858567f488044e4249445cbc55af9a135d9e3526d2'
    assert pud_id in resolved
    cands = resolved[pud_id]['candidates']
    assert cands[0]['volume'] == 'PL057'
    assert cands[0]['physical_page'] == 377
    assert any(c['physical_page'] == 487 for c in cands)


def test_navis_petri_resolution():
    resolved = resolve_pl057_items()
    navis_id = 'group:46edc0994c120360e25230e67772b1d952333b1bec6527b6104550aa13d60be8'
    assert navis_id in resolved
    cands = resolved[navis_id]['candidates']
    assert cands[0]['volume'] == 'PL057'
    assert cands[0]['physical_page'] == 323
