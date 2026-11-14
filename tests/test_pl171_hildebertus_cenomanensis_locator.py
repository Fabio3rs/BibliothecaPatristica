"""
Tests for PL171 Hildebertus Cenomanensis Locator (Sprint 103)
"""

import os
import pytest
from tools.indexing.pl171_hildebertus_cenomanensis_locator import (
    TARGET_ITEMS,
    get_pl171_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_candidate_files_exist():
    for item_id, info in TARGET_ITEMS.items():
        assert len(info["destinations"]) >= 3
        for dest in info["destinations"]:
            assert os.path.exists(dest["file"]), f"File does not exist: {dest['file']}"
            assert dest["physical"] > 0
            assert dest["score"] >= 70.0
            assert "witness" in dest
            assert "snippet" in dest


def test_galfridi_epitaphium_resolution():
    item_id = "f2519cd9acd9a4f0794b551c5a0d4b80af3912ed381a5d3ff5948759df92b82d"
    cands = get_pl171_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 702 (Carmina Miscellanea XLI)
    assert cands[0]["physical"] == 702
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Epitaphium Galfridi comitis" in content
    assert "Galfridus comes" in content


def test_paschalis_ritus_parvis_magna_resolution():
    item_id = "c94f6999b561dc54010e2e091b15d1d2264b609ceb8b0e30468ce7baa871d58e"
    cands = get_pl171_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 284 (Sermo in Die Paschae)
    assert cands[0]["physical"] == 284
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "SERMONES DE TEMPORE. — IN DIE PASCHÆ" in content
    assert "Synagoga" in content or "Aegyptum" in content


def test_vita_roberti_marbodum_resolution():
    item_id = "group:0cbf11a52be0c74ac7b1c2357a18ffbb86c6895179c2b31d372babf6bbd67c1c"
    cands = get_pl171_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 754 (Vita S. Roberti)
    assert cands[0]["physical"] == 754
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "VITA S. ROBERTI ABB. CASÆ DEI" in content
