"""
Tests for PL054 S. Leo Magnus Locator (Sprint 106)
"""

import os
import pytest
from tools.indexing.pl054_leo_magnus_locator import (
    TARGET_ITEMS,
    get_pl054_candidates,
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


def test_ob_eos_qui_roma_fugerunt_resolution():
    item_id = "group:81ef84561fd637b445603fc617878f7c92dc0ec437c48e1006ed6e6f92e6bc13"
    cands = get_pl054_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 316 (Epistola VII ad episcopos per Italiam)
    assert cands[0]["physical"] == 316
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "AD EPISCOPOS PER ITALIAM" in content
    assert "Manichæ" in content


def test_leo_a_marciano_pascha_resolution():
    item_id = "group:f9c7403c665beaebce86dc166b942532d68ccfae1b71bbbc4f1b04287c3b272c"
    cands = get_pl054_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 624 (De Epistolis Deperditis § 73-77)
    assert cands[0]["physical"] == 624
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE EPISTOLIS DEPERDITIS" in content
    assert "Aegypt" in content


def test_sermones_male_attributi_resolution():
    item_id = "group:6851b2a48733d118fa6b8c6657e850aae8170b5d347351863c8b18982f638c2e"
    cands = get_pl054_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 245 (Sermones S. Leoni Magno Attributi)
    assert cands[0]["physical"] == 245
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "SERMONES S. LEONI MAGNO ATTRIBUTI" in content
