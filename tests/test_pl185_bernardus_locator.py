"""
Tests for PL185 S. Bernardus Locator (Sprint 117)
"""

import os
import pytest
from tools.indexing.pl185_bernardus_locator import (
    TARGET_ITEMS,
    get_pl185_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_schoecelini_resolution():
    item_id = "group:5e85944206d667c36c50ba1f4f52f04db71d904140f254f5e49df4303cd662c0"
    cands = get_pl185_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 233
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Schocelino agri Trevirensis" in content
    assert "eremita" in content


def test_theobaldus_resolution():
    item_id = "group:977107b6cd15425fe611d7d2ccad2f5a5336a4c1ec9e1acafbb998fcf10a2dab"
    cands = get_pl185_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 154
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "comitis Theobaldi" in content
    assert "CAPUT VIII" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
