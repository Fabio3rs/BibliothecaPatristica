"""
Tests for PL104 S. Agobardus Locator (Sprint 119)
"""

import os
import pytest
from tools.indexing.pl104_agobardus_locator import (
    TARGET_ITEMS,
    get_pl104_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_paulus_apostolus_resolution():
    item_id = "group:19983689b5f08ecd5472726f14a917169d70dd37e85ae826fda41eb2dd85ac58"
    cands = get_pl104_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 49
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "DE JUDAICIS SUPERSTITIONIBUS" in content
    assert "89" in content or "90" in content
    r2 = cands[1]
    assert r2["physical"] == 50
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Vas electionis, et magister gentium Paulus" in content2


def test_legum_contemptus_resolution():
    item_id = "group:56be0dba8d932f978363a56dc6102170a9f710a63e12ef898daf701cee2230b9"
    cands = get_pl104_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 98
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "S. AGOBARDI" in content
    assert "Quievit timor regum et legum" in content
    assert "Parentes, id est, consanguineos" in content


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
