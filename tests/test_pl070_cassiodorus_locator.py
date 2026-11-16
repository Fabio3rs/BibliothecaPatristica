"""
Unit tests for Sprint 161 PL070 M. Aurelius Cassiodorus Locator
"""

import os
import json
import pytest
from tools.indexing.pl070_cassiodorus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:8d23d9c1e45a579d86aefb4d0d3bf27d4fb8a56ec997ea9821ec55628338626e"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Manus Patris"
    assert data["ref_raw"] == "45-8"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 233
    dest1 = dests[0]
    assert dest1["physical"] == 233
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "EXPOSITIO IN PSALTERIUM" in content1 or "EXPOSTITIO IN PSALTERIUM" in content1
    assert "457" in content1

    # Secondary destination: physical p. 234
    dest2 = dests[1]
    assert dest2["physical"] == 234
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "M. AURELII CASSIODORI" in content2

    # Tertiary destination: index page p. 719
    dest3 = dests[2]
    assert dest3["physical"] == 719
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "INDEX ANALYTICVS IN SECVNDVM CASSIODORI TOMVM" in content3 or "INDEX ANALYTICUS" in content3
    assert "Manus Patris" in content3


def test_dry_run_application():
    from tools.indexing.pl070_cassiodorus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
