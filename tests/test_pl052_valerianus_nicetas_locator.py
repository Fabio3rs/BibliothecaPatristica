import os
import pytest
from tools.indexing.pl052_valerianus_nicetas_locator import locate_all, TARGET_ITEMS


def test_pl052_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pl052_valerianus_semipelagianismo():
    item_id = "e299264a5e88d5647e73d51ffa84c9d4da5a62c882ed542e754acde8a7f44ae9"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p384 = info["destinations"][0]["file"]
    assert os.path.exists(p384)
    with open(p384, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Ex qua 37 constare videtur Celedensem Ecclesiam in Syria fuisse" in content

    p385 = info["destinations"][1]["file"]
    assert os.path.exists(p385)
    with open(p385, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Semipelagianismi suspectum Valerianum" in content2
    assert "Agnoscat lector solemnia verba quibus Semipelagianismus" in content2


def test_pl052_aquileia_attilanam_cladem():
    item_id = "e3df09f840f93f9eee3c023247a5febc43b8e15c383375ef90873d29ddc767bb"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p461 = info["destinations"][0]["file"]
    assert os.path.exists(p461)
    with open(p461, "r", encoding="utf-8") as f:
        content = f.read()
    assert "splendidissimum ordinem appellatum" in content
    assert "circum Aquileia" in content

    p462 = info["destinations"][1]["file"]
    assert os.path.exists(p462)
    with open(p462, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Præterea non ita fortasse Aquileiensis civitas excisa fuit" in content2
    assert "quemadmodum circi erant atque theatra" in content2


def test_pl052_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 85.0
