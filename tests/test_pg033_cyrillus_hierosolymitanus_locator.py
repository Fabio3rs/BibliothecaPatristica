"""
Tests for PG033 S. Cyrillus Hierosolymitanus Locator (Sprint 101)
"""

import os
import pytest
from tools.indexing.pg033_cyrillus_hierosolymitanus_locator import (
    TARGET_ITEMS,
    get_pg033_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 4


def test_candidate_files_exist():
    for item_id, info in TARGET_ITEMS.items():
        assert len(info["destinations"]) >= 3
        for dest in info["destinations"]:
            assert os.path.exists(dest["file"]), f"File does not exist: {dest['file']}"
            assert dest["physical"] > 0
            assert dest["score"] >= 70.0
            assert "witness" in dest
            assert "snippet" in dest


def test_antichristi_adventum_resolution():
    item_id = "group:9a59851c9be932026f5905293223f67a3eb9c0b95939dd2ec33145f77ccad4d6"
    cands = get_pg033_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is page 591 (Monitum XV in Epistola ad Constantium)
    assert cands[0]["physical"] == 591
    assert "Antichristi adventum" in cands[0]["snippet"]
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Cyrillus proximum timuit Antichristi adventum" in content


def test_silentii_religione_resolution():
    item_id = "group:9fe23718d0c37d293d2ddaf0888790952ca0e53299cf0102c3b59b9df6476b1a"
    cands = get_pg033_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is page 550 (Admonitio II in Catechesis XXI / Mystagogica III)
    assert cands[0]["physical"] == 550
    assert "religione silentii" in cands[0]["snippet"]
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "religione silentii tegat" in content


def test_transsubstantiatione_resolution():
    item_id = "group:ca0f941992721b65a76b9e3e23b283d0ede3d0b18729b59b8ea3ada8a765c5b4"
    cands = get_pg033_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is page 561 (Admonitio VI in Catechesis XXIII / Mystagogica V)
    assert cands[0]["physical"] == 561
    assert "madoremque sanguinis" in cands[0]["snippet"] or "panis sacer, oculis admoveatur" in cands[0]["snippet"]
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "antequam ore sumatur panis sacer, oculis admoveatur" in content


def test_stylus_facilis_resolution():
    item_id = "group:7aa15fbed4f422a22fd0b10248deaed1efa91aa2321a80c5a8abcef3f35ccee2"
    cands = get_pg033_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is page 574 (Monitum VIII in Homilia in Paralyticum)
    assert cands[0]["physical"] == 574
    assert "stylus ipse" in cands[0]["snippet"]
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "stylus ipse et dicendi ratio" in content
