"""
Tests for PL210 Alanus de Insulis Locator (Sprint 120)
"""

import os
import pytest
from tools.indexing.pl210_alanus_locator import (
    TARGET_ITEMS,
    get_pl210_candidates,
    locate_all,
)


def test_target_items_count():
    assert len(TARGET_ITEMS) == 2


def test_domus_naturae_resolution():
    item_id = "group:45f3164524060bffd72a8e0951f114d32bc443b389ecd1a69b5e2c6e74282d85"
    cands = get_pl210_candidates(item_id)
    assert len(cands) == 4
    r1 = cands[0]
    assert r1["physical"] == 249
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "ANTICLAUDIANUS" in content
    assert "Domus Naturæ in monte sita" in content
    assert "325" in content
    r2 = cands[1]
    assert r2["physical"] == 284
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Fortuna et Nobilitas domum Naturae adveni" in content2


def test_tionibus_albigenses_resolution():
    item_id = "group:267aa9a873d7a546d0311c6eec9ddb50fc20988c7b648828b57af66d8712b1ad"
    cands = get_pl210_candidates(item_id)
    assert len(cands) == 3
    r1 = cands[0]
    assert r1["physical"] == 159
    assert os.path.exists(r1["file"])
    with open(r1["file"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "CONTRA HÆRETICOS LIBRI QUATUOR" in content
    assert "204" in content
    assert "Quibus rationibus muniti hæretici" in content
    r2 = cands[1]
    assert r2["physical"] == 158
    with open(r2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Aiunt hæretici temporis nostri quod duo sunt principia rerum" in content2


def test_locate_all_complete():
    res = locate_all()
    assert len(res) == 2
    for item_id, dests in res.items():
        assert len(dests) >= 2
        for d in dests:
            assert os.path.exists(d["file"])
            assert d["score"] > 0
