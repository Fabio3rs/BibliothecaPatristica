"""
Tests for PG075 S. Cyrillus Alexandrinus Locator (Sprint 114)
"""

import os
import pytest
from tools.indexing.pg075_cyrillus_alexandrinus_locator import (
    TARGET_ITEMS,
    get_pg075_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_melior_administratio_resolution():
    item_id = "group:1cb96a2e2b90cc5cb295af3330e69701ff9bbea3dadc8a1bb6cc05626fccfdad"
    cands = get_pg075_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 180
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "melior sit Dei Verbi administratio" in content
    assert "tanto et administratorem" in content


def test_thomas_resolution():
    item_id = "group:a9be1ec32c1f580a4a551f8bd1dc7a70895881309298642c7de3ebb23705eb8d"
    cands = get_pg075_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 286
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "discipulus Thomas" in content
    assert "Dominus meus et Deus meus" in content

    r2 = cands[1]
    assert r2["physical"] == 285
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "331" in content2


def test_perfectum_esse_resolution():
    item_id = "group:f20ce3af1bbeea6b10215e14f0b4841f7d7f21f2127945bc41a5645c52819613"
    cands = get_pg075_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 277
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "THESAURUS" in content
    assert "supra omnes" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
