"""
Tests for PL026 S. Hieronymus Locator (Sprint 107)
"""

import os
import pytest
from tools.indexing.pl026_hieronymus_locator import (
    TARGET_ITEMS,
    get_pl026_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_causa_admirationis_resolution():
    item_id = "8ab0a72cd75eba741b16f4e4bf6cc30d69964dc3eac4799bdb55806331516570"
    cands = get_pl026_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 77
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "in quibus 156 fertur" in content
    assert "ferventis olei dolium" in content


def test_marcus_quis_resolution():
    item_id = "433a7019eebec7e3ef90b40de48d8382f051619c81a4ea18a9e8e524411650ba"
    cands = get_pl026_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 14
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Secundus Marcus, interpres apostoli Petri" in content
    assert "Alexandrinæ ecclesiæ primus episcopus" in content


def test_matthaeus_edidit_hebraeo_resolution():
    item_id = "b5b174ce7f8e4d38cc1a9c43e227e91531117a91b2a08f3347ce247dfce55c1d"
    cands = get_pl026_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 14
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Primus omnium Matthæus est Publicanus, cognomento Levi" in content
    assert "Evangelium in Judæa Hebræo sermone edidit" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
