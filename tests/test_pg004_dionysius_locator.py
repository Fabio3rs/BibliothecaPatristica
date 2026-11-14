import os
import pytest
from tools.indexing.pg004_dionysius_locator import locate_all, TARGET_ITEMS


def test_pg004_target_items_count():
    assert len(TARGET_ITEMS) == 1


def test_pg004_areopagiticarum_vindiciae():
    item_id = "group:9c1704984eba6e7d50046f0c5cfcb53b9211c10963ee3bc1e3a15f823e6d66c1"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p499 = info["destinations"][0]["file"]
    assert os.path.exists(p499)
    with open(p499, "r", encoding="utf-8") as f:
        content = f.read()
    assert "DISPUTATIO APOLOGET" in content
    assert "618" in content

    p523 = info["destinations"][1]["file"]
    assert os.path.exists(p523)
    with open(p523, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "VINDICIÆ OPERUM AREOPAGITICORUM" in content2

    p551 = info["destinations"][3]["file"]
    assert os.path.exists(p551)
    with open(p551, "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Areopagiticarum operum Vindiciæ" in content3


def test_pg004_locate_all_structure():
    results = locate_all()
    assert len(results) == 1
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 85.0
