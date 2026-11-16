"""
Tests for PG089 S. Anastasius Sinaita Locator (Sprint 147)
"""

import os
import pytest
from tools.indexing.pg089_anastasius_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:1aa852d128f5a6d35b3e060ddb9a2a33e8f49b5a9618305e08ffde7e85ecbd6c"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "Clericus. — De ordinatione Clerici"
    assert entry["ref_raw"] == "1230 seq"
    assert len(entry["destinations"]) == 4


def test_files_exist_and_content_matches():
    item_id = "group:1aa852d128f5a6d35b3e060ddb9a2a33e8f49b5a9618305e08ffde7e85ecbd6c"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG089 physical p. 915 (Homilia CXXIII De ordinatione clerici, cols 1817-1818)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "HOMILIA CXXIII" in content1
    assert "DE ORDINATIONE CLERICI" in content1

    # Destination 2: PG089 physical p. 916 (Homilia CXXIII cont, cols 1819-1820)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "ANTIOCHI MONACHI" in content2
    assert "honeste et secundum ordinem" in content2

    # Destination 3: PG089 physical p. 621 (literal Migne col 1230)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "DISPUTATIO ADVERSUS JUDÆOS" in content3

    # Destination 4: PG089 physical p. 941 (primary index witness)
    dest4 = entry["destinations"][3]
    assert os.path.exists(dest4["file"])
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Clericus. — De ordinatione Clerici, 1230 seq" in content4


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 4
