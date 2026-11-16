"""
Tests for PG091 S. Maximus Confessor Locator (Sprint 148)
"""

import os
import pytest
from tools.indexing.pg091_maximus_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "region-entry:d5b4eeb530568120affe5b5aeacf5bfde16d9eb85cc25fbd85dfdfaf7b87d9c5"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "ἐκτινάσσω"
    assert entry["ref_raw"] == "296 a. et sæpius."
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "region-entry:d5b4eeb530568120affe5b5aeacf5bfde16d9eb85cc25fbd85dfdfaf7b87d9c5"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG091 physical p. 334 (cols 599-600)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "S. MAXIMI CONFESSORIS" in content1
    assert "ἐκτινάσσοντες" in content1

    # Destination 2: PG091 physical p. 245 (cols 453-454)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "EPISTOLÆ" in content2
    assert "ἐκτινάσσων" in content2

    # Destination 3: PG091 physical p. 824 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "ἐκτινάσσω, 296 a. et sæpius." in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
