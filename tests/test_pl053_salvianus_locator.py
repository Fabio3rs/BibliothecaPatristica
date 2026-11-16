"""
Unit tests for Sprint 159 PL053 S. Salvianus Massiliensis Locator
"""

import os
import json
import pytest
from tools.indexing.pl053_salvianus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:fff9ab4ad191a321317e3701555fada51a57ec82e67467d7ca59e397b2ff14f7"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Abundantia rerum, causa vitiorum"
    assert data["ref_raw"] == "150 seq"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 81
    dest1 = dests[0]
    assert dest1["physical"] == 81
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "DE GUBERNATIONE DEI LIB. VII" in content1

    # Secondary destination: physical p. 82
    dest2 = dests[1]
    assert dest2["physical"] == 82
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "S. SALVIANI MASSILIENSIS PRESBYTERI" in content2

    # Tertiary destination: index page p. 513
    dest3 = dests[2]
    assert dest3["physical"] == 513
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Abundantia rerum, causa vitiorum" in content3


def test_dry_run_application():
    from tools.indexing.pl053_salvianus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
