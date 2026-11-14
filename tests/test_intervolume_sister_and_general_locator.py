"""
Unit tests for Inter-Volume Sister and General Locator (tools/indexing/apply_intervolume_sister_and_general_locator.py).
"""

import pytest
from tools.indexing.apply_intervolume_sister_and_general_locator import (
    InterVolumeSisterAndGeneralLocator,
    roman_to_int,
)


@pytest.fixture(scope="module")
def locator():
    return InterVolumeSisterAndGeneralLocator()


def test_roman_to_int():
    assert roman_to_int("I") == 1
    assert roman_to_int("IV") == 4
    assert roman_to_int("X") == 10
    assert roman_to_int("LXXIV") == 74
    assert roman_to_int("CXXXI") == 131
    assert roman_to_int("CLXVI") == 166
    assert roman_to_int("CCXV") == 215
    assert roman_to_int("CLXXXV.") == 185


def test_locate_sister_volume_pl045(locator):
    # In PL045, col 121 points to PL044
    res = locator.locate_item("PL045", "Non esse infantibus personale peccatum", "121", None)
    assert res is not None
    target_vol, target_file, score, reason, col = res
    assert target_vol == "PL044"
    assert "PL044" in target_file
    assert col == 121
    assert score >= 8.5


def test_locate_sister_volume_pg007_02(locator):
    # In PG007.02, col 127 points to PG007.01
    res = locator.locate_item("PG007.02", "Sample verbete", "127", None)
    assert res is not None
    target_vol, target_file, score, reason, col = res
    assert target_vol == "PG007.01"
    assert "PG007.01" in target_file
    assert col == 127
    assert score >= 8.5


def test_locate_general_index_pl218(locator):
    # In PL218, Adeobaltus (S.) papa | ref: LXXXVII, 1139 points to PL087 col 1139
    res = locator.locate_item("PL218", "Adeobaltus (S.) papa", "LXXXVII, 1139", None)
    assert res is not None
    target_vol, target_file, score, reason, col = res
    assert target_vol == "PL087"
    assert "PL087" in target_file
    assert col == 1139
    assert score >= 8.5
