"""
Tests for PG098 S. Germanus Locator (Sprint 149)
"""

import os
import pytest
from tools.indexing.pg098_germanus_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:9e30f8cab57fac531db912401c09b2f1d5ff1cc01f859e8de9142b661c5cc452"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "b; exauditur"
    assert entry["ref_raw"] == "ibid"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:9e30f8cab57fac531db912401c09b2f1d5ff1cc01f859e8de9142b661c5cc452"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG098 physical p. 161 (cols 313-314)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "IN PRÆSENTATIONEM SS. DEIPARÆ" in content1
    assert "314" in content1
    assert "celerrime exaudis" in content1

    # Destination 2: PG098 physical p. 162 (cols 315-316)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. GERMANI PATRIARCHÆ" in content2
    assert "315" in content2

    # Destination 3: PG098 physical p. 754 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "ditur, ibid. c, d" in content3
    assert "sterilitatis opprobrium" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
