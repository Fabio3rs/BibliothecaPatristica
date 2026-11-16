"""
Tests for PG072 S. Cyrillus Alexandrinus Locator (Sprint 146)
"""

import os
import pytest
from tools.indexing.pg072_cyrillus_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:78b7523248d6a196bca95149db9ac2fcbf39be5ae7917b0d31a4282dafff8a47"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "Circumcisio et baptismus"
    assert entry["ref_raw"] == "130 seq"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:78b7523248d6a196bca95149db9ac2fcbf39be5ae7917b0d31a4282dafff8a47"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG072 physical p. 253 (Comment. in Lucam, Mai p. 130)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "COMMENT. IN LUCAM" in content1
    assert "130" in content1
    assert "sancti baptismatis" in content1

    # Destination 2: PG072 physical p. 254 (Comment. in Lucam cont, Mai p. 131 / seq)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. CYRILLI ALEXANDRINI" in content2
    assert "circumcisio tria effecisse" in content2

    # Destination 3: PG072 physical p. 490 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Circumcisio et baptismus, 130 seq" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
