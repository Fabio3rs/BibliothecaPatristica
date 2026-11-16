"""
Tests for PG131 Anna Comnena Locator (Sprint 152)
"""

import os
import pytest
from tools.indexing.pg131_comnena_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:7c3106c8905049f58d009ef0a0a3c3fbc062af0882b002d834d0427978df2977"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Prælium Alexii cum Scythis ad Megalopolim" in entry["label"]
    assert entry["ref_raw"] == "195 et seq"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:7c3106c8905049f58d009ef0a0a3c3fbc062af0882b002d834d0427978df2977"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG131 physical p. 293 (Alexiadis lib. VII, Possinus p. 195)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "ALEXIADIS LIB. VII" in content1
    assert "Megalopolis" in content1

    # Destination 2: PG131 physical p. 294 (Alexiadis lib. VII cont, Possinus p. 196 / et seq)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "ANNÆ COMNENÆ" in content2

    # Destination 3: PG131 physical p. 679 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Prælium Alexii cum Scythis ad Megalopolim quo Alexius victus est, 195 et seq" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
