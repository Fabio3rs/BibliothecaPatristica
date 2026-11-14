"""
Tests for PL024 S. Hieronymus Locator (Sprint 108)
"""

import os
import pytest
from tools.indexing.pl024_hieronymus_locator import (
    TARGET_ITEMS,
    get_pl024_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_corpora_reconduntur_resolution():
    item_id = "9918ea876af48662c81d3f3f0a9ddffef34093154668533261250d45b09f4045"
    cands = get_pl024_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 448
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "corpora credentium templa esse Dei" in content
    assert "1040" in content


def test_nabuchodonosor_columpa_resolution():
    item_id = "f1816be7db5746b7313a1da5182087fc4eb801373ebee13c3b639548f3097b4a"
    cands = get_pl024_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 441
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "lumbam Domini intelligi Nabuchodonosor" in content
    assert "1024" in content


def test_rufinus_notatur_resolution():
    item_id = "395ed448763c825895d1716e277b2065e383e6646968acb4a3f075e3fff04295"
    cands = get_pl024_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 448
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "sectator calumniæ Grun" in content
    assert "1040" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
