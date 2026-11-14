"""
Tests for PG078 S. Isidorus Pelusiota Locator (Sprint 113)
"""

import os
import pytest
from tools.indexing.pg078_isidorus_pelusiota_locator import (
    TARGET_ITEMS,
    get_pg078_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_mercurius_resolution():
    item_id = "2d09f60969fe977946f202e4a17f8e84106cb21e5dbeb3652699861ad307de21"
    cands = get_pg078_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 771
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Mercurius" in content
    assert "quovis ligno" in content


def test_studia_optimae_naturae_resolution():
    item_id = "ce27302668aa00721ecb42f15b8d72e9f47626a466b4a138bd3d01ce2a6bdd8b"
    cands = get_pg078_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 771
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "juncta studia summos perficiunt" in content


def test_venia_tribuenda_resolution():
    item_id = "0353e44d598e70ea9801185bbfb9c09f02c5653168eaabe300ca7bbacb3fba15"
    cands = get_pg078_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 261
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "veniam tribuere, qui benignitate meliores" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
