import os
import pytest
from tools.indexing.pg135_zonaras_locator import locate_all, TARGET_ITEMS


def test_pg135_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg135_mauricius_captivos():
    item_id = "797d4f4ac6cc5e313766de4aa90b9fa96a91a982237fb116d13df8f4f994fad7"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p640 = info["destinations"][0]["file"]
    assert os.path.exists(p640)
    with open(p640, "r", encoding="utf-8") as f:
        content = f.read()
    assert "si captivos redimere velit" in content
    assert "Recusante Mauricio" in content
    assert "78" in content

    p639 = info["destinations"][1]["file"]
    assert os.path.exists(p639)
    with open(p639, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Mauricius" in content2
    assert "77" in content2


def test_pg135_vectigalium_pompeio():
    item_id = "3611c02ffc21e228efa6b74b0c93d1196b7ce8b68cdae1bdad056628036fd53f"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p412 = info["destinations"][0]["file"]
    assert os.path.exists(p412)
    with open(p412, "r", encoding="utf-8") as f:
        content = f.read()
    assert "480" in content
    assert "μυρίας ὀκτακισχιλίας" in content

    p411 = info["destinations"][1]["file"]
    assert os.path.exists(p411)
    with open(p411, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "vectigalibus" in content2
    assert "decies octies" in content2
    assert "quingenties" in content2


def test_pg135_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
