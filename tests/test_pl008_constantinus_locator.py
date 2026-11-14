import os
import pytest
from tools.indexing.pl008_constantinus_locator import locate_all, TARGET_ITEMS


def test_pl008_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pl008_de_advocato_fisci():
    item_id = "1a77b2dc3391bf15b437b20760a3957f1b0713a74343d09e89019a4b143151c8"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p66 = info["destinations"][0]["file"]
    assert os.path.exists(p66)
    with open(p66, "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE ADVOCATO" in content
    assert "FISCI" in content
    assert "Fisci advocatus, pœnam metuens" in content


def test_pl008_de_maternis_bonis():
    item_id = "5af322887913e539b507e1e540b8070de3174bb6ac815dd18a988f155dc43b7d"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p70 = info["destinations"][0]["file"]
    assert os.path.exists(p70)
    with open(p70, "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE MATERNIS BONIS ET MATERNI GENERIS ET CRETIONE SUBLATA" in content
    assert "Cesset itaque in maternis duntaxat successionibus" in content


def test_pl008_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
