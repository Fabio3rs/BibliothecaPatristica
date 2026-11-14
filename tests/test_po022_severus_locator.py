import os
import pytest
from tools.indexing.po022_severus_locator import locate_all, TARGET_ITEMS


def test_po022_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_po022_drosis():
    item_id = "24c1199e7910ca7ef4930106517f7158165be73a2f4bcbbc420cd9b6a83e71c7"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p240 = info["destinations"][0]["file"]
    assert os.path.exists(p240)
    with open(p240, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Δροσις" in content
    assert "SUR LA MARTYRE SAINTE DROSIS" in content


def test_po022_hypostasis():
    item_id = "604ddd6f0d78b3331b128108de08827beac2d8f2ca5e5a2060d9b8e3939bbe93"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p299 = info["destinations"][0]["file"]
    assert os.path.exists(p299)
    with open(p299, "r", encoding="utf-8") as f:
        content = f.read()
    assert "HOMÉLIE CIII" in content
    assert "ὑπόστασις" in content
    assert "ressources de ma pauvreté" in content


def test_po022_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
