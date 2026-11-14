import os
import pytest
from tools.indexing.pg111_eutychius_locator import locate_all, TARGET_ITEMS


def test_pg111_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg111_sarjabil_oroddano():
    item_id = "9854ad47215ad3972039be8ca74332d8a1d888e2a678688da8f4a85c9b5a0f09"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p555 = info["destinations"][0]["file"]
    assert os.path.exists(p555)
    with open(p555, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Serjabilem filium Hasanae" in content
    assert "Oroddano" in content
    assert "281-283" in content

    p614 = info["destinations"][2]["file"]
    assert os.path.exists(p614)
    with open(p614, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Sarjabil" in content2
    assert "Hasanæ" in content2


def test_pg111_captioni_damasci():
    item_id = "dec45f1bb249eebd72df0ba39e9f1c082a9d9858d2a269f7b6caf213eb0538e5"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p555 = info["destinations"][0]["file"]
    assert os.path.exists(p555)
    with open(p555, "r", encoding="utf-8") as f:
        content = f.read()
    assert "captam esse Damascum" in content
    assert "Serjabil" in content

    p614 = info["destinations"][2]["file"]
    assert os.path.exists(p614)
    with open(p614, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Captioni Damasci interfuit" in content2


def test_pg111_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
