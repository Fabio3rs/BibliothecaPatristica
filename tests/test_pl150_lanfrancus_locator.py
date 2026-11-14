"""
Tests for PL150 B. Lanfrancus Locator (Sprint 121)
"""

import os
import pytest
from tools.indexing.pl150_lanfrancus_locator import (
    TARGET_ITEMS,
    get_pl150_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_episcopi_virtutes_resolution():
    item_id = "group:3fdec73a46ed88025d1c2936183d5d8e92d437597118e38b8d4cd5fe4060be79"
    cands = get_pl150_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 180
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "CAPUT III" in content
    assert "episcopatum desiderat" in content
    assert "187" in content
    r2 = cands[1]
    assert r2["physical"] == 279
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "ars est artium regimen animarum" in content2
    assert "334" in content2


def test_matrimonii_castimonia_resolution():
    item_id = "840da3b58325c774bc6ec2195399e83088dcc9c1908911209b9dedbfa146e02b"
    cands = get_pl150_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 92
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "CAPUT VII" in content
    assert "propter fornicationem autem unusquisque suam" in content
    r2 = cands[1]
    assert r2["physical"] == 93
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Ne laudata castitate, uxo" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
