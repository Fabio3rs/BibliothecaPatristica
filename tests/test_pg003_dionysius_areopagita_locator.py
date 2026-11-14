"""
Tests for PG003 S. Dionysius Areopagita Locator (Sprint 116)
"""

import os
import pytest
from tools.indexing.pg003_dionysius_areopagita_locator import (
    TARGET_ITEMS,
    get_pg003_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_corpus_deiparae_resolution():
    item_id = "group:1e234757439400672adae09187e6dc72747347d912a37ac8d02693abe57695f1"
    cands = get_pg003_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 345
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "obdormitionis" in content
    assert "Deiparae" in content
    assert "Hierotheus" in content

    r2 = cands[1]
    assert r2["physical"] == 340
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Hierotheum" in content2
    assert "Petro et Jacobo" in content2


def test_modestia_dionysii_resolution():
    item_id = "group:b4e9dc97354b0b6fac6ad6afdf40b5654c47f4c58872bce49fd0e6b41cf7f188"
    cands = get_pg003_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 30
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DIONYSIUS AREOPAGITA" in content
    assert "60" in content


def test_scotum_dissimilitudo_resolution():
    item_id = "group:5b01d13630bffa5e4a8fa684a63cd0a3a8abfa85af138904676bb8bf9aad0a87"
    cands = get_pg003_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 527
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "577" in content
    assert "dissimilitudo" in content
    assert "Scotus" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
