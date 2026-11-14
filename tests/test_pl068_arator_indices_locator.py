"""
Tests for PL068 Arator Subdiaconus Locator (Sprint 104)
"""

import os
import pytest
from tools.indexing.pl068_arator_indices_locator import (
    TARGET_ITEMS,
    get_pl068_candidates,
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


def test_cratea_resolution():
    item_id = "04113eb007a18b3f843c62866b26b587acdfde67d6dd7a895f6d09f72123c397"
    cands = get_pl068_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 75 (cols 133-134)
    assert cands[0]["physical"] == 75
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE ACTIBUS APOSTOLORUM LIB. I" in content


def test_domina_urbs_resolution():
    item_id = "71019fe9e87b19f28980a62c43231caa7a98588eb503d0c702048a2dd2ceaa73"
    cands = get_pl068_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 77 (cols 137-138)
    assert cands[0]["physical"] == 77
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE ACTIBUS APOSTOLORUM" in content


def test_s_adheret_resolution():
    item_id = "group:35af321736f90a2f013e5ded50760529a976c80e65d1038c9d641c09f84fd5c8"
    cands = get_pl068_candidates(item_id)
    assert len(cands) >= 4
    # Check that cols 87, 94, 96 are covered
    phys_pages = [c["physical"] for c in cands]
    assert 52 in phys_pages
    assert 55 in phys_pages
    assert 56 in phys_pages
    assert 565 in phys_pages
