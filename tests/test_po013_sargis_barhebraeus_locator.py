import os
import pytest
from tools.indexing.po013_sargis_barhebraeus_locator import locate_all, TARGET_ITEMS


def test_po013_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_po013_sargis_p41():
    item_id = "region-entry:5e5e1995c7541faccee0f55f85c07ff36f5356e6df5010186a0763ed3288f4a9"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p51 = info["destinations"][0]["file"]
    assert os.path.exists(p51)
    with open(p51, "r", encoding="utf-8") as f:
        content = f.read()
    assert "SIXIÈME ASSEMBLÉE" in content
    assert "41" in content


def test_po013_barhebraeus_astronomes():
    item_id = "region-entry:640699359092d90ca71105136d66541d7225cf34aea90d63c609e110e65fb7a9"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p278 = info["destinations"][0]["file"]
    assert os.path.exists(p278)
    with open(p278, "r", encoding="utf-8") as f:
        content = f.read()
    assert "APPENDICE" in content
    assert "Voici l'hérésie des astronomes" in content
    assert "Troisième hérésie. Des astronomes" in content


def test_po013_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
