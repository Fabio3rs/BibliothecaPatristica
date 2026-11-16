"""
Tests for PG070 S. Cyrillus Alexandrinus Locator (Sprint 145)
"""

import os
import pytest
from tools.indexing.pg070_cyrillus_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:4be3a43f72657e3778556944ec6021c3c5b9578c1cea18954bb78db900e5ae97"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "Cæcitas"
    assert entry["ref_raw"] == "333 et seq"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:4be3a43f72657e3778556944ec6021c3c5b9578c1cea18954bb78db900e5ae97"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG070 physical p. 171 (cols 333-334)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "IN ISAIAM LIB. II. TOMUS I" in content1
    assert "impietatis provecti" in content1

    # Destination 2: PG070 physical p. 172 (cols 335-336 / et seq)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. CYRILLI ALEXANDRINI ARCHIEP" in content2
    assert "Judæorum regionem" in content2

    # Destination 3: PG070 physical p. 740 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Cæcitas, 333 et seq" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
