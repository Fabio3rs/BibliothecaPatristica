import os
import pytest
from tools.indexing.pl062_eugyppius_locator import locate_all, TARGET_ITEMS


def test_pl062_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pl062_affectus_habent_boni():
    item_id = "region-entry:d1767d094bf6f8a310d336f1744f3f2edc9c163a52e62b952010f1032573b4f8"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]
    assert info["ref_raw"] == "6"

    p25 = info["destinations"][0]["file"]
    assert os.path.exists(p25)
    with open(p25, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Habemus ergo eas ex humanæ conditionis infirmitate" in content
    assert "Illi motus, hi affectus, de amore boni" in content

    p24 = info["destinations"][1]["file"]
    assert os.path.exists(p24)
    with open(p24, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "6 Amor ergo habere quod amatur" in content2


def test_pl062_juvenibus_quomodo_vivendum():
    item_id = "region-entry:e192f87296a1f10c1b55a1e05acf0e2c5ed2e9c94429e29fdf287ef914aa919e"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]
    assert info["ref_raw"] == "20"

    p174 = info["destinations"][0]["file"]
    assert os.path.exists(p174)
    with open(p174, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Adolescentibus ergo studiosis ejus ita vivendum 20 est" in content
    assert "Nemini faciant quod pati nolunt" in content


def test_pl062_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
