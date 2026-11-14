"""
Tests for PL175 Hugo de S. Victore Locator (Sprint 96)
"""

import os
import pytest
from tools.indexing.pl175_hugo_de_s_victore_locator import (
    PL175_ITEMS_RAW,
    resolve_pl175_items,
    find_physical_file,
)


def test_pl175_items_raw_count():
    assert len(PL175_ITEMS_RAW) == 4


def test_find_physical_file():
    p443 = find_physical_file('PL175', 443)
    assert p443 is not None
    assert os.path.exists(p443)
    assert '443' in p443

    p358 = find_physical_file('PL175', 358)
    assert p358 is not None
    assert os.path.exists(p358)
    assert '358' in p358

    p216 = find_physical_file('PL175', 216)
    assert p216 is not None
    assert os.path.exists(p216)
    assert '216' in p216


def test_resolve_pl175_items():
    resolved = resolve_pl175_items()
    assert len(resolved) == 4

    for item_id, data in resolved.items():
        assert data['state'] == 'with_destinations'
        candidates = data['candidates']
        assert len(candidates) >= 2
        for cand in candidates:
            assert os.path.exists(cand['file'])
            assert cand['score'] > 0
            assert cand['navigable'] is True
            assert 'description' in cand


def test_vidua_sareptana_resolution():
    resolved = resolve_pl175_items()
    vidua_id = 'group:38b299ab7d746b38b980105e85f973aec1380bf07656a5c7a6b8dcd231593ce7'
    assert vidua_id in resolved
    cands = resolved[vidua_id]['candidates']
    assert cands[0]['volume'] == 'PL175'
    assert cands[0]['physical_page'] == 443


def test_threnorum_interpretatio_resolution():
    resolved = resolve_pl175_items()
    thren_id = 'group:0375ff9074475a86f150c713cf16c7463e4c5952efdbe831ea2d48a1e8f6bef1'
    assert thren_id in resolved
    cands = resolved[thren_id]['candidates']
    assert cands[0]['volume'] == 'PL175'
    assert cands[0]['physical_page'] == 216
