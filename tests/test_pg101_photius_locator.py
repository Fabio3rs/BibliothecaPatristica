import os
import pytest
from tools.indexing.pg101_photius_locator import locate_all, TARGET_ITEMS


def test_pg101_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg101_afflato_divinitus():
    item_id = "group:d43b4e6cb08ff4192fd9dbb98d892c07f028ecbd9b7a89531468bc55ca626d46"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p177 = info["destinations"][0]["file"]
    assert os.path.exists(p177)
    with open(p177, "r", encoding="utf-8") as f:
        content = f.read()
    assert "princeps apostolorum" in content
    assert "afflato divinitus animo loquens" in content
    assert "310" in content

    p653 = info["destinations"][2]["file"]
    assert os.path.exists(p653)
    with open(p653, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Afflato divinitus animo" in content2


def test_pg101_simon_magus_imaginibus():
    item_id = "group:017259258f64ea3b031c8a89e86f9e1bd4f5d51256e42e9badada5a08521a8ee"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p489 = info["destinations"][0]["file"]
    assert os.path.exists(p489)
    with open(p489, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Simon Magus" in content
    assert "De imaginibus" in content
    assert "934" in content

    p653 = info["destinations"][2]["file"]
    assert os.path.exists(p653)
    with open(p653, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Quod de imaginibus sentiebat" in content2


def test_pg101_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
