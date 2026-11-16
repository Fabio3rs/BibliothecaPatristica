"""
Tests for PG100 S. Nicephorus Locator (Sprint 150)
"""

import os
import pytest
from tools.indexing.pg100_nicephorus_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:0ed17a609268e1c5b5fa04ed45f2ea6867646ecb9b87e3692401009549a3d8c9"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Idololatriam a Christianismo deletam" in entry["label"]
    assert entry["ref_raw"] == "181 seqq"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:0ed17a609268e1c5b5fa04ed45f2ea6867646ecb9b87e3692401009549a3d8c9"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG100 physical p. 311 (Apologeticus pro sacris imaginibus, Mai p. 181)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "APOLOGETICUS PRO SACRIS IMAGINIBUS" in content1
    assert "181" in content1
    assert "impietatis præcipitium" in content1

    # Destination 2: PG100 physical p. 312 (Apologeticus pro sacris imaginibus cont, Mai p. 182 / seqq)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. NICEPHORI PATRIARCHÆ" in content2
    assert "falsitatis refutationem" in content2

    # Destination 3: PG100 physical p. 772 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Idololatriam a Christianismo deletam fuisse latissime demonstratur, 181 seqq" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
