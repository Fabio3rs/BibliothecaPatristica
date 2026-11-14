import os
import pytest
from tools.indexing.pg103_photius_locator import locate_all, TARGET_ITEMS


def test_pg103_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg103_bekker_collatorum():
    item_id = "region-entry:fda435a7294ed10b0455dfea91b564b555041d9d2c726cd0782b0e20ebdf81e7"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p027 = info["destinations"][0]["file"]
    assert os.path.exists(p027)
    with open(p027, "r", encoding="utf-8") as f:
        content = f.read()
    assert "INDEX CODICUM MSS." in content
    assert "EMMANUELE BEKKERO" in content
    assert "1824" in content

    p026 = info["destinations"][1]["file"]
    assert os.path.exists(p026)
    with open(p026, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "AUCTORUM PHOTIANÆ BIBLIOTHECÆ SERIES" in content2


def test_pg103_myriobiblon_scotti():
    item_id = "region-entry:60621dcd5e32b48abe02f69c41e5d497cf7537d9e1fbb5c7682ec877506f9280"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p027 = info["destinations"][0]["file"]
    assert os.path.exists(p027)
    with open(p027, "r", encoding="utf-8") as f:
        content = f.read()
    assert "PHOTII" in content
    assert "MYRIOBIBLON" in content
    assert "ANDREÆ SCOTTI" in content
    assert "ROTHOMAGI AN. 1653" in content

    p028 = info["destinations"][2]["file"]
    assert os.path.exists(p028)
    with open(p028, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Tarasius" in content2 or "Tarasio" in content2 or "voluminum" in content2


def test_pg103_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
