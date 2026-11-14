"""
Unit tests for Migne Column Tolerance Locator (tools/indexing/migne_column_tolerance_locator.py).
"""

import json
import pytest
from tools.indexing.migne_column_tolerance_locator import MigneColumnToleranceLocator


@pytest.fixture(scope="module")
def locator():
    return MigneColumnToleranceLocator()


def test_extract_target_column(locator):
    # From metadata_json
    meta1 = json.dumps({"columns": ["324"]})
    assert locator.extract_target_column(None, meta1) == 324

    meta2 = json.dumps({"columns": [1008]})
    assert locator.extract_target_column(None, meta2) == 1008

    # From ref_raw
    assert locator.extract_target_column("1008", None) == 1008
    assert locator.extract_target_column("554 a.", None) == 554
    assert locator.extract_target_column("12b", None) == 12

    # None cases
    assert locator.extract_target_column(None, None) is None
    assert locator.extract_target_column("invalid ref", json.dumps({"columns": []})) is None


def test_locate_item_pl077(locator):
    # In PL077, col 1008 should match file 511
    res = locator.locate_item("PL077", "Neque enim eleemosyna", "1008", None)
    assert res is not None
    target_file, score, match_type, col = res
    assert "PL077" in target_file
    assert col == 1008
    assert score >= 8.5
    assert "511" in target_file


def test_locate_item_pg111_deconcatenated(locator):
    meta = json.dumps({"columns": ["324"]})
    res = locator.locate_item("PG111", "Sample verbete", None, meta)
    assert res is not None
    target_file, score, match_type, col = res
    assert "PG111" in target_file
    assert col == 324
    assert score >= 8.5


def test_locate_item_pl061_deconcatenated(locator):
    meta = json.dumps({"columns": ["182"]})
    res = locator.locate_item("PL061", "Sample verbete", None, meta)
    assert res is not None
    target_file, score, match_type, col = res
    assert "PL061" in target_file
    assert col == 182
    assert score >= 8.5
