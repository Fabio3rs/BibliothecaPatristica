"""
Unit tests for Sprint 164 PL124 Usuardus Sangermanensis Locator
"""

import os
import json
import pytest
from tools.indexing.pl124_usuardus_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "group:0a2bd83f637fff9ccfc7747952fba3051888de7a49073e6a913f61833dcb1048"
    assert item_id in items

    data = items[item_id]
    assert "Florentia virgo" in data["label"]
    assert data["ref_raw"] == "ibid"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 135
    dest1 = dests[0]
    assert dest1["physical"] == 135
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "Florentiæ virginis" in content1
    assert "translatio sanctæ Florentiæ" in content1

    # Secondary destination: physical p. 134
    dest2 = dests[1]
    assert dest2["physical"] == 134
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "USUARDI MONACHI" in content2
    assert "Scillitanorum" in content2

    # Tertiary destination: index page p. 652
    dest3 = dests[2]
    assert dest3["physical"] == 652
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "INDEX AD USUARDI AUCTARIA" in content3
    assert "Florentia virgo, 17 jul.; transl., ibid." in content3


def test_dry_run_application():
    from tools.indexing.pl124_usuardus_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
