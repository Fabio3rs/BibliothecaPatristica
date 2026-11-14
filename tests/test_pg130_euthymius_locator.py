import os
import pytest
from tools.indexing.pg130_euthymius_locator import locate_all, TARGET_ITEMS


def test_pg130_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg130_paratitlos_greek():
    item_id = "region-entry:3eeb27adeaa4bfbf72f48788e5602fc47d7ee88a2307f2472cc6d4fbc32e1ef0"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p686 = info["destinations"][0]["file"]
    assert os.path.exists(p686)
    with open(p686, "r", encoding="utf-8") as f:
        content = f.read()
    assert "ΠΑΡΑΤΙΤΛΟΣ" in content
    assert "Φωτίου πατριάρχου" in content
    assert "Βουλγάρων" in content or "Βουλγαρίας" in content

    p692 = info["destinations"][1]["file"]
    assert os.path.exists(p692)
    with open(p692, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "1359" in content2


def test_pg130_appendix_latin():
    item_id = "region-entry:c8cdf1c7cf1d7cb538a3296def944022a3a7234ad80d2e2d23034de06ff0a435"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p686 = info["destinations"][0]["file"]
    assert os.path.exists(p686)
    with open(p686, "r", encoding="utf-8") as f:
        content = f.read()
    assert "APPENDIX" in content
    assert "Photii patriarchæ" in content
    assert "Michaelem" in content

    p023 = info["destinations"][2]["file"]
    assert os.path.exists(p023)
    with open(p023, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Appendix" in content2
    assert "Bulgariæ" in content2


def test_pg130_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
