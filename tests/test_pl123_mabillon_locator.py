"""
Unit tests for Sprint 163 PL123 Ado Viennensis / S. Barnardus Locator
"""

import os
import json
import pytest
from tools.indexing.pl123_mabillon_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:f5bfcb793ad0489c5fe6e6e2cf9340c93eb55be33d4659a6b91a88b316698e78"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "(Apud Mabill."
    assert data["ref_raw"] == "ibid"

    dests = data["destinations"]
    assert len(dests) == 2

    # Primary destination: physical p. 230
    dest1 = dests[0]
    assert dest1["physical"] == 230
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "MIRACULA EJUSDEM" in content1
    assert "Barnardi" in content1
    assert "Mabill." in content1

    # Secondary destination: physical p. 229
    dest2 = dests[1]
    assert dest2["physical"] == 229
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert len(content2) > 100


def test_dry_run_application():
    from tools.indexing.pl123_mabillon_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
