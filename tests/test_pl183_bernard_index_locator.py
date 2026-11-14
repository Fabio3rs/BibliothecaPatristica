"""
Unit tests for Bernard of Clairvaux Index Locator in PL183 (tools/indexing/pl183_bernard_index_locator.py).
"""

import os
import pytest
from tools.indexing.pl183_bernard_index_locator import (
    PL183BernardIndexLocator,
    MABILLON_OFFSET,
)


@pytest.fixture(scope="module")
def locator():
    return PL183BernardIndexLocator()


def test_non_pl183_ignored(locator):
    res = locator.resolve_item("test_id", "PL001", "Some label", "100", "/some/path.txt")
    assert res is None


def test_resolve_col_under_1150_to_pl182(locator):
    # Conradus rex Romanorum, col 178 -> PL182
    res = locator.resolve_item(
        item_id="test_conrad",
        vol="PL183",
        label="Conradus rex Romanorum",
        ref_raw="178",
        source_file="/homessddata/Projects/pdfocr/teste/PL183/text/dda38bee-dc06-4fee-9e30-26afcdd7e175-621.txt",
    )
    assert res is not None
    assert res["target_vol"] == "PL182"
    assert res["target_col"] == 178
    assert "PL182" in res["matched_file"]
    assert os.path.exists(res["matched_file"])


def test_resolve_mabillon_offset_to_pl183(locator):
    # Deus exigit timeri ut Dominus, col 1538 -> PL183 col 1183
    res = locator.resolve_item(
        item_id="test_deus_exigit",
        vol="PL183",
        label="Deus exigit timeri ut Dominus, honorari ut pater, et sponsus amari",
        ref_raw="1538",
        source_file="/homessddata/Projects/pdfocr/teste/PL183/text/dda38bee-dc06-4fee-9e30-26afcdd7e175-624.txt",
    )
    assert res is not None
    assert res["target_vol"] == "PL183"
    assert res["target_col"] == (1538 - MABILLON_OFFSET)
    assert "PL183" in res["matched_file"]
    assert os.path.exists(res["matched_file"])
    # Second hint should confirm keywords
    assert "second_hint" in res["reason"]
    assert res["score"] >= 9.5


def test_resolve_with_second_hint_corroboration(locator):
    # Parentum praepositos amor monachis quam periculosus | ref 1481
    res = locator.resolve_item(
        item_id="test_monachis",
        vol="PL183",
        label="Parentum præpositos amor monachis quam periculosus",
        ref_raw="1481",
        source_file="/homessddata/Projects/pdfocr/teste/PL183/text/dda38bee-dc06-4fee-9e30-26afcdd7e175-644.txt",
    )
    assert res is not None
    assert res["target_vol"] == "PL183"
    assert "PL183" in res["matched_file"]
    assert os.path.exists(res["matched_file"])
    assert "second_hint" in res["reason"]
