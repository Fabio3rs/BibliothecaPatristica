"""
Tests for PG114 Symeon Metaphrastes Locator (Sprint 151)
"""

import os
import pytest
from tools.indexing.pg114_metaphrastes_locator import TARGET_ITEMS, locate_all


def test_target_items_structure():
    assert len(TARGET_ITEMS) == 1
    item_id = "region-entry:a2963ccee2129fc0ffca3906911424825ff80098bd145f1ef2eee0ade7d741c3"
    assert item_id in TARGET_ITEMS
    entry = TARGET_ITEMS[item_id]
    assert "Joannes Damascenus, de Symeone Stylita" in entry["label"]
    assert entry["ref_raw"] == "17, 18"
    assert len(entry["destinations"]) == 3


def test_files_exist_and_content_matches():
    item_id = "region-entry:a2963ccee2129fc0ffca3906911424825ff80098bd145f1ef2eee0ade7d741c3"
    entry = TARGET_ITEMS[item_id]

    # Destination 1: PG114 physical p. 15 (Allatii Diatriba, bracketed [24] onwards)
    dest1 = entry["destinations"][0]
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "NOTITIA" in content1
    assert "SYMEONIS METAPHRASTÆ" in content1
    assert "[24]" in content1

    # Destination 2: PG114 physical p. 79 (Indices in Diatribam rubric)
    dest2 = entry["destinations"][1]
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "INDICES IN DIATRIBAM" in content2
    assert "Numeri ad eos te revocant quos in textu inter uncos posuimus" in content2

    # Destination 3: PG114 physical p. 80 (primary index witness)
    dest3 = entry["destinations"][2]
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Joannes Damascenus, de Symeone Stylita Montis Mirabilis, 17, 18" in content3


def test_locate_all():
    items = locate_all()
    assert len(items) == 1
    for item_id, item_data in items.items():
        assert "destinations" in item_data
        assert len(item_data["destinations"]) == 3
