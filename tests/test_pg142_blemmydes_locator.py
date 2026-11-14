"""
Tests for PG142 Nicephorus Blemmydes Locator (Sprint 110)
"""

import os
import pytest
from tools.indexing.pg142_blemmydes_locator import (
    TARGET_ITEMS,
    get_pg142_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_ubique_tropici_resolution():
    item_id = "3532f64e80da7c56b1ded372e77d5e05cc012e4c37346fcb3ae32d05a9ebc3d7"
    cands = get_pg142_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 649
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "tropicos" in content
    assert "æquinoctialem" in content


def test_iridis_circumferentia_resolution():
    item_id = "group:518b40b4796f543b8fb51bfc73d6cb2f0544adfdb0d660bbe6bae5ee954338a3"
    cands = get_pg142_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 607
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "prima igitur iridis circumferentia" in content
    assert "prima circumferentia est maxima" in content


def test_lunaris_incrementi_resolution():
    item_id = "97afe80933c6473d1d0701fbe87ee7202d7097e1e5c36c51cbf24a1a4efc9eeb"
    cands = get_pg142_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 659
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "incrementa et decrementa" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
