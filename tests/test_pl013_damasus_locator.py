"""
Unit tests for Sprint 157 PL013 S. Damasus Papa Locator
"""

import os
import json
import pytest
from tools.indexing.pl013_damasus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:4311de4749a5c29c766b31363f2142a8e62f2d2658a375a251791fc9c695f691"
    assert item_id in items

    data = items[item_id]
    assert "Ariminensis decreta" in data["label"]

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 164
    dest1 = dests[0]
    assert dest1["physical"] == 164
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "Arimin" in content1
    assert "cassavit" in content1

    # Secondary destination: physical p. 179
    dest2 = dests[1]
    assert dest2["physical"] == 179
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "EPISTOLÆ" in content2

    # Tertiary destination: index page p. 623
    dest3 = dests[2]
    assert dest3["physical"] == 623
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Ariminensis decreta a Liberio et Damaso cassata" in content3


def test_dry_run_application():
    from tools.indexing.pl013_damasus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
