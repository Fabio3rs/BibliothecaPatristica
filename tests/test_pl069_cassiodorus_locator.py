"""
Tests for PL069 Cassiodorus Locator (Sprint 118)
"""

import os
import pytest
from tools.indexing.pl069_cassiodorus_locator import (
    TARGET_ITEMS,
    get_pl069_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_romani_imperatores_resolution():
    item_id = "group:ab3e35f4df8d8af702e1f0fe9c9ae1d6d88b77d13f20d0c866a1163cd8477ac2"
    cands = get_pl069_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 619
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "IMPERATORES ROMANI" in content
    assert "385" in content


def test_pacem_a_justiniano_resolution():
    item_id = "group:caae8435c1cc401ed5f288d4268b769a865cc0bb6f8d193325dcba8ab8d5fc31"
    cands = get_pl069_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 412
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "THEODAHADUS REX" in content
    assert "Pacem ab eo petit" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
