"""
Unit tests for Sprint 156 PL012 S. Philastrius Brixiensis Locator
"""

import os
import json
import pytest
from tools.indexing.pl012_philastrius_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "54e27fab49483fe4d6958a9adfc10aa32837da09b46d1fcc27b61ff492794cb7"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Lex media inter Mandatum, et Gratiam"
    assert data["ref_raw"] == "1500"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 655
    dest1 = dests[0]
    assert dest1["physical"] == 655
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "SANCTI PHILASTRII" in content1
    assert "Inter mandatum et gratiam" in content1

    # Secondary destination: physical p. 684
    dest2 = dests[1]
    assert dest2["physical"] == 684
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "In Mandato, Lege, et Christi gratia" in content2

    # Tertiary destination: index page p. 692
    dest3 = dests[2]
    assert dest3["physical"] == 692
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Lex media inter Mandatum, et Gratiam" in content3


def test_dry_run_application():
    from tools.indexing.pl012_philastrius_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
