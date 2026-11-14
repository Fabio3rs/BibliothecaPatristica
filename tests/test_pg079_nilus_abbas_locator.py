"""
Tests for PG079 S. Nilus Abbas Locator (Sprint 112)
"""

import os
import pytest
from tools.indexing.pg079_nilus_abbas_locator import (
    TARGET_ITEMS,
    get_pg079_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_avarorum_animae_resolution():
    item_id = "group:72237bdb70808d51dd969015b6571a9f0daa4030721695ff92f69f3793d16db6"
    cands = get_pg079_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 439
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "avarorum" in content
    assert "separantur a corpore" in content


def test_lazari_et_divitis_resolution():
    item_id = "group:c769b1dc2fd20970a97435d435d32713b94047bf3c880848930ace5fdbae426b"
    cands = get_pg079_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 435
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Lazarum" in content
    assert "atrium" in content

    r2 = cands[1]
    assert r2["physical"] == 449
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Lazarum" in content2
    assert "divite" in content2


def test_capitur_a_barbaris_resolution():
    item_id = "group:c9782f6ede1e5a7999f06aba62712bc7647242ccbd4b5d6a6e16766dc5509c24"
    cands = get_pg079_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 348
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Barbaros" in content
    assert "xabant trahebantque" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
