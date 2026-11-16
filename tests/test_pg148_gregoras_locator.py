"""
Unit tests for Sprint 153 PG148 Nicephorus Gregoras Locator
"""

import os
import json
import pytest
from tools.indexing.pg148_gregoras_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "df07fbae9b98823cb0c95a3571e7a48b1d49fb24d80f71a4ebf241ffec349872"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Ecclesiæ patriarchales et episcopales pæne omnes principibus barbaris subjectæ"
    assert data["ref_raw"] == "1954"

    dests = data["destinations"]
    assert len(dests) == 2

    # Primary destination: physical p. 661
    dest1 = dests[0]
    assert dest1["physical"] == 661
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "BYZANTINÆ HISTORIÆ LIB. XXI" in content1
    assert "principibus barbaris subjiciuntur" in content1
    assert "patriar-chales ecclesiæ" in content1 or "patriarchales ecclesiæ" in content1

    # Secondary destination: index page p. 741
    dest2 = dests[1]
    assert dest2["physical"] == 741
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "INDEX ANALYTICUS" in content2
    assert "principibus barbaris subjectæ" in content2


def test_dry_run_application():
    from tools.indexing.pg148_gregoras_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
