"""
Tests for PL065 S. Fulgentius Ruspensis Locator (Sprint 105)
"""

import os
import pytest
from tools.indexing.pl065_fulgentius_ruspensis_locator import (
    TARGET_ITEMS,
    get_pl065_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 3


def test_candidate_files_exist():
    for item_id, info in TARGET_ITEMS.items():
        assert len(info["destinations"]) >= 3
        for dest in info["destinations"]:
            assert os.path.exists(dest["file"]), f"File does not exist: {dest['file']}"
            assert dest["physical"] > 0
            assert dest["score"] >= 70.0
            assert "witness" in dest
            assert "snippet" in dest


def test_evangeliorum_conciliatio_magdalena_resolution():
    item_id = "group:5aae23633a4a0c1b709b0c78daecc5659916aad96c565f80e61809f913cd2143"
    cands = get_pl065_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 138 (Ad Trasimundum Lib. II cap. XIII)
    assert cands[0]["physical"] == 138
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Disputatio contrarietatis quae videtur inter Evangelia" in content
    assert "Mariae Magdalenae" in content


def test_praedestinationis_firmitas_resolution():
    item_id = "group:bd1497900c4fb3cf57a2552226f2ed7785dd4ef0ea085727e16c24d6522e0907"
    cands = get_pl065_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 335 (De Veritate Praedestinationis Lib. III cap. 6)
    assert cands[0]["physical"] == 335
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "operum, verum etiam in numero personarum, 484" in content
    assert "prædestinationis" in content


def test_spiritus_sanctus_inter_opera_resolution():
    item_id = "group:faa0dbbeefcc62fcda7a5075f572e2356d0062130b524cf53ec9c26a28d3b429"
    cands = get_pl065_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 419 (Contra Fabianum fragmenta)
    assert cands[0]["physical"] == 419
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "non debere inter opera Domini computari" in content
