"""
Tests for PG066 Synesius Cyrenensis Locator (Sprint 144)
"""

import os
import pytest
from tools.indexing.pg066_synesius_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "group:b1f3b616cbf6962aea1d6cc3a26c78de561e18e2ad1eb98fc856e0ead73c703d"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Astrolabii laus" in entry["label"]
    assert entry["ref_raw"] == "307, 308 et seqq"
    assert len(entry["destinations"]) == 4


def test_files_exist_and_content_matches():
    item_id = "group:b1f3b616cbf6962aea1d6cc3a26c78de561e18e2ad1eb98fc856e0ead73c703d"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG066 physical p. 817 (Sermo de Dono Astrolabii, Petavius p. 307)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "SERMO DE DONO ASTROLABII" in content1
    assert "307" in content1

    # Destination 2: PG066 physical p. 818 (Sermo de Dono Astrolabii, Petavius p. 308)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "308" in content2
    assert "sorte philosophiæ" in content2

    # Destination 3: PG066 physical p. 819 (Sermo de Dono Astrolabii, Petavius p. 309 et seqq)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "DE DONO ASTROLABII SERMO" in content3

    # Destination 4: PG066 physical p. 897 (primary index witness)
    dest4 = entry["destinations"][3]
    assert os.path.exists(dest4["file"])
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Astrolabii laus, 307, 308 et seqq" in content4


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 4
