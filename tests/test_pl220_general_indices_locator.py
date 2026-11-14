"""
Tests for PL220 General Indices Locator (Sprint 95)
"""

import os
import pytest
from tools.indexing.pl220_general_indices_locator import (
    PL220_ITEMS_RAW,
    resolve_pl220_items,
    find_physical_file,
)


def test_pl220_items_raw_count():
    assert len(PL220_ITEMS_RAW) == 4


def test_find_physical_file():
    p402 = find_physical_file('PL179', 402)
    assert p402 is not None
    assert os.path.exists(p402)
    assert '402' in p402

    p019 = find_physical_file('PL163', 19)
    assert p019 is not None
    assert os.path.exists(p019)
    assert '019' in p019 or '19' in p019

    p758 = find_physical_file('PL120', 758)
    assert p758 is not None
    assert os.path.exists(p758)
    assert '758' in p758


def test_resolve_pl220_items():
    resolved = resolve_pl220_items()
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


def test_coelestini_and_calixti_resolution():
    resolved = resolve_pl220_items()
    coel_id = 'group:3c5a3646e9455f309a9c285de36bc903992c97f0ba6f49c16348c9990f83768e'
    assert coel_id in resolved
    cands_coel = resolved[coel_id]['candidates']
    assert cands_coel[0]['volume'] == 'PL179'
    assert cands_coel[0]['physical_page'] == 402

    calixt_id = 'group:6a40b94d089f2ac3f188fd6e2fe5d2401427e302098ac0dc3c700ed728ade962'
    assert calixt_id in resolved
    cands_cal = resolved[calixt_id]['candidates']
    assert cands_cal[0]['volume'] == 'PL163'


def test_adalhardus_erratum_resolution():
    resolved = resolve_pl220_items()
    adal_id = 'f584bde93e8cf358237bc001270cc1f0b121a15dea9d8405687179d31de81181'
    assert adal_id in resolved
    cands = resolved[adal_id]['candidates']
    # Corrected volume is PL120 (CXX)
    assert cands[0]['volume'] == 'PL120'
    assert cands[0]['physical_page'] == 758
