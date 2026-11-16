"""
Unit tests for Sprint 158 PL016 S. Ambrosius Mediolanensis Locator
"""

import os
import json
import pytest
from tools.indexing.pl016_ambrosius_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "d112cfa24e298e63dc8a048fa0d4c67b97d5ffae571ea8f4328be98b86ce3960"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "probet Ambrosius?"
    assert data["ref_raw"] == "1149"

    dests = data["destinations"]
    assert len(dests) == 2

    # Primary destination: physical p. 666
    dest1 = dests[0]
    assert dest1["physical"] == 666
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "SANCTI AMBROSII" in content1
    assert "phœnix" in content1 or "phoenix" in content1
    assert "scripturarum auctoritate" in content1

    # Secondary destination: index page p. 760
    dest2 = dests[1]
    assert dest2["physical"] == 760
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Phœnicis resurrectionem cujus scripturæ auctoritate" in content2
    assert "probet Ambrosius?" in content2


def test_dry_run_application():
    from tools.indexing.pl016_ambrosius_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
