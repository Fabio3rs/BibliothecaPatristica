"""
Tests for PL143 Hermannus Contractus Locator (Sprint 122)
"""

import os
import pytest
from tools.indexing.pl143_hermannus_locator import (
    TARGET_ITEMS,
    get_pl143_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_bericherus_petershausen_resolution():
    item_id = "group:a288a08563d66a5c2f6e59fb64a6dfce729a13a9d12736f2abb4c67d40b3b6f5"
    cands = get_pl143_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 166
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Petershusa" in content
    assert "Bericherus" in content
    r2 = cands[1]
    assert r2["physical"] == 170
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Pericherus" in content2
    assert "Petrishusa" in content2


def test_marquardus_brigantini_resolution():
    item_id = "group:15d4f8ec326997ca7786f5cb28b2f3b2a665b68414d116948a8741ca7359ea21"
    cands = get_pl143_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 150
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Brigant" in content
    assert "Udalricus" in content
    r2 = cands[1]
    assert r2["physical"] == 154
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "comites Brigantii" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
