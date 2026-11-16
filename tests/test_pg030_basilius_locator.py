"""
Tests for PG030 S. Basilius Magnus Locator (Sprint 142)
"""

import os
import pytest
from tools.indexing.pg030_basilius_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:fdc36602d6164ddc12d6996b3f84a9ab75422fd3cbb49b81b397999bb71554a3"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Resurrectionis mysterium" in entry["label"]
    assert entry["ref_raw"] == "VII, 8"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:fdc36602d6164ddc12d6996b3f84a9ab75422fd3cbb49b81b397999bb71554a3"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG029 physical p. 528 (cols 235-236, Homilia in Ps. VII v. 8)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "Et exsurge, Domine Deus meus" in content1
    assert "resurrectionis mysterium" in content1

    # Destination 2: PG030 physical p. 591 (parallel index entry, col 1188)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Exsurge, Domine Deus" in content2
    assert "mysterium resurrectionis indicant" in content2

    # Destination 3: PG030 physical p. 592 (primary index witness, col 1189)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Resurrectionis mysterium indicare videtur" in content3
    assert "Exsurge, Domine Deus, 100" in content3


def test_locate_all():
    res = locate_all()
    assert len(res) == 1
