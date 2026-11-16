"""
Tests for PG027 S. Athanasius / Eusebius Locator (Sprint 141)
"""

import os
import pytest
from tools.indexing.pg027_athanasius_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:3c6364319fadc14f16b50599ec312418f838c7ae2a21d6e5f0eda093d11ab317"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert entry["label"] == "Πολυωρία"
    assert entry["ref_raw"] == "ibid"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "group:3c6364319fadc14f16b50599ec312418f838c7ae2a21d6e5f0eda093d11ab317"
    entry = TARGET_ITEMS[item_id]
    
    # Destination 1: Ps. 136-137 (col 529-530, physical p. 271)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "Πολυώριαν" in content1
    assert "πολυχρονιότητά" in content1

    # Destination 2: Ps. 11 (col 96 B, physical p. 54)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Secundum altitudinem" in content2
    assert "temporis diuturnitatem" in content2

    # Destination 3: Primary index witness (col 1400, physical p. 713)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "INDEX GRÆCITATIS" in content3
    assert "Πολυωρεῖν" in content3
    assert "Πολυωρία" in content3


def test_locate_all():
    res = locate_all()
    assert len(res) == 1
