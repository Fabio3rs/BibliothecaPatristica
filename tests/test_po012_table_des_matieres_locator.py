"""
Tests for PO012 Table des Matières Locator (Sprint 102)
"""

import os
import pytest
from tools.indexing.po012_table_des_matieres_locator import (
    TARGET_ITEMS,
    get_po012_candidates,
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


def test_fasc_v_irenaeus_resolution():
    item_id = "region-entry:1c965d6ef3a4032f1de95c6558bcac092f15b324a7dd2fc27908f589028fd7c6"
    cands = get_po012_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 667 (Title page of Fasc. V)
    assert cands[0]["physical"] == 667
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "S. IRENAEUS" in content
    assert "ΑΠΟΣΤΟΛΙΚΟΥ" in content


def test_avertissement_resolution():
    item_id = "region-entry:99c1afe5a26ed47f5f20811d1adab993391160e53576a429efacc6c443519abc"
    cands = get_po012_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 567 (Avertissement p. 555)
    assert cands[0]["physical"] == 567
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "AVERTISSEMENT" in content
    assert "Les Miracles de Jésus" in content


def test_table_des_matieres_resolution():
    item_id = "region-entry:fc9c3b2fdd12b1e2fd1984cbbcd9d9ae38b8e6e136296b0e9653f501bec3b6f6"
    cands = get_po012_candidates(item_id)
    assert len(cands) >= 3
    # Locus primarius is physical page 820 (printed page 804)
    assert cands[0]["physical"] == 820
    with open(cands[0]["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "TABLE DES MATIÈRES" in content
    assert "DU TOME XII" in content
