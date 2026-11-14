import os
import pytest
from tools.indexing.pg149_gregoras_locator import locate_all, TARGET_ITEMS


def test_pg149_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg149_aeros_pneousi():
    item_id = "context-child:d7928fd4d7fa0ee70f1f62a4"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p701 = info["destinations"][0]["file"]
    assert os.path.exists(p701)
    with open(p701, "r", encoding="utf-8") as f:
        content = f.read()
    assert "τοῖς ἀέρος πνέουσι" in content

    p700 = info["destinations"][1]["file"]
    assert os.path.exists(p700)
    with open(p700, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "1105" in content2


def test_pg149_eikotos():
    item_id = "context-child:8a920a0c6188b18e4a8231ad"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p717 = info["destinations"][0]["file"]
    assert os.path.exists(p717)
    with open(p717, "r", encoding="utf-8") as f:
        content = f.read()
    assert "1133" in content
    assert "εἰκότερον" in content or "εἰκότως" in content


def test_pg149_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
