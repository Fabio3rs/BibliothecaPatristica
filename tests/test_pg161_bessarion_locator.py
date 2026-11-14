"""
Tests for PG161 Bessarion & Bandini Locator (Sprint 109)
"""

import os
import pytest
from tools.indexing.pg161_bessarion_locator import (
    TARGET_ITEMS,
    get_pg161_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_studia_monachatus_resolution():
    item_id = "region-entry:ae71a7693ba5f7130071065a85ac0a3b302cd2268b275100c496e2afb9b46d35"
    cands = get_pg161_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 10
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Trapezunte" in content
    assert "monasticum D. Basilii institutum" in content


def test_calderinus_resolution():
    item_id = "region-entry:f349587cc573eb8aed3e787460e5511bedb46ac2e7dfec28b9700d6ba8aba012"
    cands = get_pg161_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 16
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Domitius Calderinus" in content
    assert "contubernales" in content

    r2 = cands[1]
    assert r2["physical"] == 38
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Domitium Calderinum" in content2


def test_palaeologus_resolution():
    item_id = "region-entry:14dcecf2dd8286f6523889c5e1abd0cf7ebccd9892a899a66f8c92fccfa9c145"
    cands = get_pg161_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 12
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Græcus imperator" in content
    assert "reverentiam exhibuit" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
