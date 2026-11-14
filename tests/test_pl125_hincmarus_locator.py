"""
Tests for PL125 Hincmarus Rhemensis Locator (Sprint 123)
"""

import os
import pytest
from tools.indexing.pl125_hincmarus_locator import (
    TARGET_ITEMS,
    get_pl125_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_usque_ad_trinitatis_resolution():
    item_id = "3ade40dc9f2c806c41d27de88dfcf1ba584277177d0b97906928741403d3e800"
    cands = get_pl125_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 257
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE UNA ET NON TRINA DEITATE" in content
    assert "505" in content
    r2 = cands[1]
    assert r2["physical"] == 268
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Quia Deus Spiritus est" in content2


def test_nec_conjugem_theutberga_resolution():
    item_id = "group:eed65253422815624c03175f07c3669a95196570ba996b6e77480db80f61f572"
    cands = get_pl125_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 348
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "626" in content
    assert "conjugium legaliter initum" in content
    r2 = cands[1]
    assert r2["physical"] == 349
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "INTERROGATIO XII" in content2
    assert "virgo valeat permanere" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
