"""
Tests for PG099 S. Theodorus Studita Locator (Sprint 111)
"""

import os
import pytest
from tools.indexing.pg099_theodorus_studita_locator import (
    TARGET_ITEMS,
    get_pg099_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_de_cura_insepultorum_resolution():
    item_id = "group:16ec3553caa03bfbe23964bf7990758d6f4ef85275b266c5278fe78d73931f62"
    cands = get_pg099_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 480
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "De cura" in content
    assert "insep" in content


def test_eucharistiam_sub_unica_specie_resolution():
    item_id = "fa8e627eca8579efdd389a0554aec4ecf5b37cde72d6822910632564ff3ce118"
    cands = get_pg099_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 103
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Sola Eucharistia vescitur" in content
    assert "vivifici corporis" in content


def test_sabas_resolution():
    item_id = "799169d948c58373981b7da96a68b79532ae3d3fd311beaf86805472b95032ee"
    cands = get_pg099_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 564
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "beati Sabas" in content
    assert "Anastasius" in content

    r2 = cands[1]
    assert r2["physical"] == 904
    assert os.path.exists(r2["file"])
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "In sanctum Sabbam" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 3
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
