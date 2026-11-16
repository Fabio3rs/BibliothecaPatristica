"""
Unit tests for Sprint 162 PL119 S. Servatus Lupus Ferrariensis Locator
"""

import os
import json
import pytest
from tools.indexing.pl119_lupus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:2206c96267b754691c1cdba112387d31ed907336de74518f07647d419f1423aa"
    assert item_id in items

    data = items[item_id]
    assert "Carolomannus rex" in data["label"]
    assert data["ref_raw"] == "524 et seqq"

    dests = data["destinations"]
    assert len(dests) == 4

    # Primary destination: physical p. 266 (col. 524)
    dest1 = dests[0]
    assert dest1["physical"] == 266
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "LUPI" in content1
    assert "523" in content1

    # Secondary destination: physical p. 267
    dest2 = dests[1]
    assert dest2["physical"] == 267
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "525" in content2

    # Tertiary destination: physical p. 277
    dest3 = dests[2]
    assert dest3["physical"] == 277
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Carlomanno" in content3

    # Quaternary destination: index page p. 612
    dest4 = dests[3]
    assert dest4["physical"] == 612
    assert os.path.exists(dest4["file"])
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "INDEX IN S. LUPUM" in content4
    assert "Carolomannus rex" in content4


def test_dry_run_application():
    from tools.indexing.pl119_lupus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
