"""
Unit tests for Sprint 165 PL128 Anastasius Bibliothecarius Locator
"""

import os
import json
import pytest
from tools.indexing.pl128_anastasius_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:7077291c529e8208009749dfc892089f22a3d81fb6c9a659862893a3b3beb043"
    assert item_id in items

    data = items[item_id]
    assert "Canthara cerostrata" in data["label"]
    assert data["ref_raw"] == "56 et passim"

    dests = data["destinations"]
    assert len(dests) == 4

    # Primary destination: physical p. 31
    dest1 = dests[0]
    assert dest1["physical"] == 31
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "53" in content1
    assert "LIBERIUS" in content1

    # Secondary destination: physical p. 32
    dest2 = dests[1]
    assert dest2["physical"] == 32
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "55" in content2
    assert "ANASTASII" in content2

    # Tertiary destination: physical p. 41
    dest3 = dests[2]
    assert dest3["physical"] == 41
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "canthara cerostrata" in content3

    # Quaternary destination: index page p. 721
    dest4 = dests[3]
    assert dest4["physical"] == 721
    assert os.path.exists(dest4["file"])
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Canthara cerostrata" in content4


def test_dry_run_application():
    from tools.indexing.pl128_anastasius_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
