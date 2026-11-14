import os
import pytest
from tools.indexing.pg102_photius_locator import locate_all, TARGET_ITEMS


def test_pg102_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg102_reverentia_parentes():
    item_id = "113174dea8987b34b163d97bbd3b01c15956fbae2646c1db8cda93a784e0cf3f"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p396 = info["destinations"][0]["file"]
    assert os.path.exists(p396)
    with open(p396, "r", encoding="utf-8") as f:
        content = f.read()
    assert "leges ecclesiasticas" in content
    assert "supplicium" in content
    assert "judicium" in content

    p538 = info["destinations"][2]["file"]
    assert os.path.exists(p538)
    with open(p538, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Reverentia in parentes semper adhibenda 750 c" in content2


def test_pg102_scandalum():
    item_id = "1878e242a54e8d75a2ec1c6a5ab84a64ed7e9dad0595194a5068bbb0bdd72147"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p396 = info["destinations"][0]["file"]
    assert os.path.exists(p396)
    with open(p396, "r", encoding="utf-8") as f:
        content = f.read()
    assert "latrones, aut civitatum expilatores" in content
    assert "fidei pro" in content
    assert "supplicium" in content

    p539 = info["destinations"][2]["file"]
    assert os.path.exists(p539)
    with open(p539, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Scandalum quid, et quibus suppliciis obnoxium, 750 b" in content2


def test_pg102_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
