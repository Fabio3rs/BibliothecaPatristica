"""
Unit tests for Sprint 160 PL064 Anicius Manlius Severinus Boethius Locator
"""

import os
import json
import pytest
from tools.indexing.pl064_boethius_locator import locate_all, TARGET_ITEMS


def test_target_item_structure():
    items = locate_all()
    assert len(items) == 1
    item_id = "93497edb4c4c511915dcbabe73a851de1ea65518a6b3276a59919ee8f6222317"
    assert item_id in items

    data = items[item_id]
    assert data["label"] == "Geometriæ unde dicta"
    assert data["ref_raw"] == "I, 1359"

    dests = data["destinations"]
    assert len(dests) == 3

    # Primary destination: physical p. 682 in PL063 (col. 1359)
    dest1 = dests[0]
    assert dest1["physical"] == 682
    assert os.path.exists(dest1["file"])
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "AN. MANL. SEV. BOETII" in content1
    assert "geometriæ in disciplinis" in content1
    assert "Tituli inscriptio" in content1

    # Secondary destination: physical p. 681 in PL063
    dest2 = dests[1]
    assert dest2["physical"] == 681
    assert os.path.exists(dest2["file"])
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "EUCLIDIS GEOMETRICA" in content2

    # Tertiary destination: index page p. 808 in PL064
    dest3 = dests[2]
    assert dest3["physical"] == 808
    assert os.path.exists(dest3["file"])
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Geometriæ unde dicta" in content3


def test_dry_run_application():
    from tools.indexing.pl064_boethius_locator import apply_destinations
    fixed, cand = apply_destinations(dry_run=True)
    assert fixed == 1
    assert cand == 0
