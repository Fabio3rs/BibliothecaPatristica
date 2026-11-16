"""
Unit tests for Sprint 155 PL007 Lactantius Locator
"""

import os
import json
import pytest
from tools.indexing.pl007_lactantius_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:ff4b70e8cde0b9c83b106b277a9211cfed32ae5115246c557739c978c5a8595f"
    assert item_id in items

    data = items[item_id]
    assert "SABÆORUM" in data["label"]
    assert "SABBATICUS" in data["label"]

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 289 in PL007
    dest1 = dests[0]
    assert dest1["physical"] == 289
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "Sabbaticus appellatus est" in content1
    assert "Sabbaticum fluvium" in content1

    # Secondary destination: physical p. 494 in PL006 (col. 974)
    dest2 = dests[1]
    assert dest2["physical"] == 494
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Sabæorum" in content2 or "Sabæi" in content2
    assert "974" in content2

    # Tertiary destination: index page p. 601 in PL007
    dest3 = dests[2]
    assert dest3["physical"] == 601
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "SABBATICUS fluvius" in content3


def test_dry_run_application():
    from tools.indexing.pl007_lactantius_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
