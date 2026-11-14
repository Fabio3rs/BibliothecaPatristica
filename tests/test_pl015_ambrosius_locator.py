import os
import pytest
from tools.indexing.pl015_ambrosius_locator import locate_all, TARGET_ITEMS


def test_pl015_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pl015_virginalis_pudoris_maritum():
    item_id = "588553ef58704949ec117726c022a62c23c2fab039a1fd6fe69cfabd22bf4872"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p231 = info["destinations"][0]["file"]
    assert os.path.exists(p231)
    with open(p231, "r", encoding="utf-8") as f:
        content = f.read()
    assert "non est enim virginalis pudoris eligere maritum" in content
    assert "Sponsalium quidem meorum pater" in content


def test_pl015_verbum_prius_vocis_officium():
    item_id = "084fd566db5530c8fc706feeb926cfcdd0debafba2fac1410c32a32eb28dee05"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p200 = info["destinations"][0]["file"]
    assert os.path.exists(p200)
    with open(p200, "r", encoding="utf-8") as f:
        content = f.read()
    assert "verbum enim prius intus operatur, sequitur vo-" in content
    assert "Factum est autem verbum, ut sanctus Ba-" in content


def test_pl015_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
