"""
Unit tests for Sprint 154 PG159 Laonicus Chalcocondylas Locator
"""

import os
import json
import pytest
from tools.indexing.pg159_chalcocondylas_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "4daf4b874363ea01a8678bf7c101a926d40ef586ddfc233579eee6095f10c523"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Lesbo donatus Franciscus Catalusius"
    assert data["ref_raw"] == "1535"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 260
    dest1 = dests[0]
    assert dest1["physical"] == 260
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "LAONICI CHALCOCONDYLÆ" in content1
    assert "Franciscus Catalusius" in content1
    assert "Dominicus" in content1

    # Secondary destination: adjacent context p. 259
    dest2 = dests[1]
    assert dest2["physical"] == 259
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Lesbum" in content2 or "Lesbi" in content2
    assert "277" in content2

    # Tertiary destination: index page p. 707
    dest3 = dests[2]
    assert dest3["physical"] == 707
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Franciscus Catalusius" in content3


def test_dry_run_application():
    from tools.indexing.pg159_chalcocondylas_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
