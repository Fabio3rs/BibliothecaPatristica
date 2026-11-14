"""
Tests for PL085 Liturgia Mozarabica Locator (Sprint 124)
"""

import os
import pytest
from tools.indexing.pl085_liturgia_mozarabica_locator import (
    TARGET_ITEMS,
    get_pl085_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_defunctorum_commemoratio_resolution():
    item_id = "e1730440f26e72e35fc562c4d719fab46594de3c10354a1bf177e32769fc36f5"
    cands = get_pl085_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 520
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Missa pro omnibus fidelibus defunctis" in content or "1029" in content
    assert "Sacrificium" in content
    r2 = cands[1]
    assert r2["physical"] == 533
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "MENSIS NOVEMBRIS" in content2


def test_maximae_gratiae_resolution():
    item_id = "group:be452283b9555f92280846c3458a542259a5d3df847dc9f1e1da9b23241c3349"
    cands = get_pl085_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 146
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "MISSALE MIXTUM" in content
    assert "Custodientes ordinem" in content
    r2 = cands[1]
    assert r2["physical"] == 148
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "MISSAL" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
