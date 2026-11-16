"""
Tests for PG034 S. Macarius Locator (Sprint 143)
"""

import os
import pytest
from tools.indexing.pg034_macarius_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 2
    item_id = "4c80e4eac051f10702d08098cbf54cf6b564a2e1413ced652c656339f1504b11"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Archelaus in Judæam properat" in entry["label"]
    assert entry["ref_raw"] == "176;"
    assert len(entry["destinations"]) == 4


def test_files_exist_and_content_matches():
    item_id = "4c80e4eac051f10702d08098cbf54cf6b564a2e1413ced652c656339f1504b11"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG034 physical p. 439 (Liber de Oratione, embedded page 176)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "176" in content1
    assert "LIBER DE ORATIONE" in content1

    # Destination 2: PG034 physical p. 440 (Liber de Oratione cont, embedded page 177 / et seqq)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. MACARII ÆGYPTII" in content2
    assert "corrumpi contingat" in content2

    # Destination 3: PG034 physical p. 98 (parallel Migne column 176)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "SS. MACARIORUM AEGYPTII ET ALEXANDRINI VITAE" in content3

    # Destination 4: PG034 physical p. 656 (primary index witness)
    dest4 = entry["destinations"][3]
    assert os.path.exists(dest4["file"])
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Archelaus in Judæam properat, 176" in content4


def test_locate_all():
    items = locate_all()
    assert len(items) == 2
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) >= 3
