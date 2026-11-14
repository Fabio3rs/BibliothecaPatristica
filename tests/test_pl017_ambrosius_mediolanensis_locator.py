"""
Tests for PL017 S. Ambrosius Mediolanensis Locator (Sprint 98)
"""

import os
import pytest
from tools.indexing.pl017_ambrosius_mediolanensis_locator import (
    PL017_ITEMS_RAW,
    resolve_pl017_items,
    find_physical_file,
)


def test_pl017_items_raw_count():
    assert len(PL017_ITEMS_RAW) == 4


def test_find_physical_file():
    p249 = find_physical_file('PL017', 249)
    assert p249 is not None
    assert os.path.exists(p249)
    assert '249' in p249

    p065 = find_physical_file('PL017', 65)
    assert p065 is not None
    assert os.path.exists(p065)
    assert '065' in p065 or '65' in p065

    p255 = find_physical_file('PL017', 255)
    assert p255 is not None
    assert os.path.exists(p255)
    assert '255' in p255


def test_resolve_pl017_items():
    resolved = resolve_pl017_items()
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


def test_gervasius_and_protasius_resolution():
    resolved = resolve_pl017_items()
    gerv_id = 'group:3da20071ae7d5711a87e9064b9a9c554d0f52c090040bff19bc8c7a61b2d1460'
    assert gerv_id in resolved
    cands = resolved[gerv_id]['candidates']
    assert cands[0]['volume'] == 'PL017'
    assert cands[0]['physical_page'] == 249
    assert any(c['physical_page'] == 631 for c in cands)


def test_referuntur_ibid_page_split_resolution():
    resolved = resolve_pl017_items()
    ref_id = 'group:88ff11d46a32f55a8ca31c1b1b0e9fe8e555e8307c812b3b2a9aa5f20f24aec4'
    assert ref_id in resolved
    cands = resolved[ref_id]['candidates']
    assert cands[0]['volume'] == 'PL017'
    assert cands[0]['physical_page'] == 255
    assert any(c['physical_page'] == 637 for c in cands)
    assert any(c['physical_page'] == 638 for c in cands)
