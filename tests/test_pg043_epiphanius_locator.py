"""
Tests for PG043 S. Epiphanius Locator (Sprint 115)
"""

import os
import pytest
from tools.indexing.pg043_epiphanius_locator import (
    TARGET_ITEMS,
    get_pg043_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_philodakryos_marie_resolution():
    item_id = "region-entry:78c0f78fdf3bdc17ac63bc32bb9916d275f0273cd6ce36159f1e493b050e0bd0"
    cands = get_pg043_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 465
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Μαγδαλινὴ Μαρίη φιλοδάκρυος" in content

    r2 = cands[1]
    assert r2["physical"] == 467
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Μαγδαληνὴ Μαρίη φιλοδάκρυος" in content2


def test_bacchus_resolution():
    item_id = "region-entry:9d5128aa2fd1fda889794bf8d3566ac4ced55f8a3ebb5eb3237057fc78033965"
    cands = get_pg043_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 180
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "templum Liberi patris apud Indos" in content
    assert "sapphiro" in content


def test_non_distingui_a_resolution():
    item_id = "e1772fa8e0330dca36bae680d1c1c399926b87708598ecb7c3fd30867fb4415a"
    cands = get_pg043_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 538
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "λήκυθον ab expositione non distinguit" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
