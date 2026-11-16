"""
Unit tests for Sprint 166 PL130 Isidorus Mercator Locator
"""

import os
import json
import pytest
from tools.indexing.pl130_isidorus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:7145bdb71e03914d4d4a338c7982f561db90e4580365e48ea8ca4aacdb8f4e99"
    assert item_id in items

    data = items[item_id]
    assert "Ignorantia intolerabilis" in data["label"]
    assert data["ref_raw"] == "0"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 53
    dest1 = dests[0]
    assert dest1["physical"] == 53
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "ISIDORI MERCATORIS" in content1
    assert "ignorantia" in content1
    assert "scire se credat" in content1

    # Secondary destination: physical p. 54
    dest2 = dests[1]
    assert dest2["physical"] == 54
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "DECRETALIUM COLLECTIO" in content2

    # Tertiary destination: index page p. 649
    dest3 = dests[2]
    assert dest3["physical"] == 649
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "INDEX IN ISIDORUM MERCATOREM" in content3
    assert "Ignorantia intolerabilis" in content3
    assert "aliquid scire" in content3


def test_dry_run_application():
    from tools.indexing.pl130_isidorus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
