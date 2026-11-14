import os
import pytest
from tools.indexing.pg094_damascenus_locator import locate_all, TARGET_ITEMS


def test_pg094_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_pg094_basilius_fame():
    item_id = "region-entry:bc35ceebd8b748c7c250b48978a8e06a589cf2a0bb637a2bae04289149d3f1c5"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p058 = info["destinations"][0]["file"]
    assert os.path.exists(p058)
    with open(p058, "r", encoding="utf-8") as f:
        content = f.read()
    assert "S. Basil. Hom. in sicc. et fame" in content
    assert "Fames" in content
    assert "calamitatum" in content
    assert "Λιμὸς ἀνθρωπίνων" in content

    p028 = info["destinations"][2]["file"]
    assert os.path.exists(p028)
    with open(p028, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "In fame et siccitate" in content2
    assert "491" in content2


def test_pg094_philo_exodo():
    item_id = "region-entry:f88bfbef5e0fc646e3ab46f1351b84eb9cbdfc13cc6f962043a501af0efed785"
    assert item_id in TARGET_ITEMS
    info = TARGET_ITEMS[item_id]

    p243 = info["destinations"][0]["file"]
    assert os.path.exists(p243)
    with open(p243, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Quæstionum in Exodum" in content
    assert "τῶν ἐν Ἐξόδῳ ζητουμένων" in content

    p030 = info["destinations"][2]["file"]
    assert os.path.exists(p030)
    with open(p030, "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Ἐξόδῳ ζητημάτων" in content2
    assert "748" in content2


def test_pg094_locate_all_structure():
    results = locate_all()
    assert len(results) == 2
    for item_id, item in results.items():
        assert "destinations" in item
        assert len(item["destinations"]) >= 2
        for dest in item["destinations"]:
            assert os.path.exists(dest["file"])
            assert dest["rank"] >= 1
            assert dest["score"] >= 90.0
