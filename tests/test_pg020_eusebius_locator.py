import os
import pytest
from tools.indexing.pg020_eusebius_locator import locate_all, TARGET_ITEMS


def test_pg020_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg020_lex_haereticis():
    item_id = "group:1aeda49beb98eb94eb22b40b7c68d8bdb8388b6a5b411c710b544a6051b477ad"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p574 = info["destinations"][0]["file"]
    assert os.path.exists(p574)
    with open(p574, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Constantini constitutio adversus hæreticos" in content
    assert "Victor Constantinus Maximus Augustus" in content
    assert "521" in content

    p782 = info["destinations"][2]["file"]
    assert os.path.exists(p782)
    with open(p782, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Lex de hæreticis, 520 seq" in content2


def test_pg020_novatus():
    item_id = "group:64ee0686504bc173652f7098c903e299dbca9e2645c2284dab75ca0c1e11d093"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p312 = info["destinations"][0]["file"]
    assert os.path.exists(p312)
    with open(p312, "r", encoding="utf-8") as f:
        content = f.read()
    assert "De Novato" in content
    assert "Ecclesiæ Romanæ presbyter" in content
    assert "242" in content

    p784 = info["destinations"][2]["file"]
    assert os.path.exists(p784)
    with open(p784, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Novatus (rectius Novatianus), Ecclesiæ Rom. presbyter, 241 seq" in content2


def test_pg020_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
