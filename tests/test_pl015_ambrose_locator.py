"""
Unit tests for Ambrose Benedictine Index Locator (tools/indexing/pl015_ambrose_benedictine_locator.py).
"""

import os
import pytest
from tools.indexing.pl015_ambrose_benedictine_locator import AmbroseBenedictineLocator


@pytest.fixture(scope="module")
def locator():
    return AmbroseBenedictineLocator()


def test_parse_entry_reference(locator):
    # Test parsing with page and para
    src_path = "/homessddata/Projects/pdfocr/teste/PL015/text/00147bd1-9b19-49c9-8195-f9beb666a21a-538.txt"
    label = "Hospitalia jura servanda"
    page, para = locator.parse_entry_reference(src_path, label, "51")
    assert page == 101
    assert para == 51

    # Test fallback to db_ref when snippet not found
    p_fallback, para_fallback = locator.parse_entry_reference("nonexistent", "Unknown label", "376")
    assert p_fallback == 376
    assert para_fallback is None


def test_interpolate_file_num(locator):
    # Synthetic anchors
    anchors = [(10, 100), (20, 200), (30, 300)]
    assert locator.interpolate_file_num(100, anchors, 50) == 10
    assert locator.interpolate_file_num(150, anchors, 50) == 15
    assert locator.interpolate_file_num(200, anchors, 50) == 20
    assert locator.interpolate_file_num(250, anchors, 50) == 25
    assert locator.interpolate_file_num(300, anchors, 50) == 30
    assert locator.interpolate_file_num(50, anchors, 50) == 10
    assert locator.interpolate_file_num(400, anchors, 50) == 30


def test_locate_item_pl014_cross_volume(locator):
    src_path = "/homessddata/Projects/pdfocr/teste/PL015/text/00147bd1-9b19-49c9-8195-f9beb666a21a-538.txt"
    # Hospitalia jura servanda is p. 101, §51 -> PL014 file 119
    res = locator.locate_item(src_path, "Hospitalia jura servanda", "51")
    assert res is not None
    target_file, score, reason, page, para = res
    assert "PL014" in target_file
    assert page == 101
    assert para == 51
    assert score >= 9.0


def test_locate_item_pl015_intra_volume(locator):
    src_path = "/homessddata/Projects/pdfocr/teste/PL015/text/00147bd1-9b19-49c9-8195-f9beb666a21a-538.txt"
    # IMPERATORIS Olla is p. 1410, §108 -> PL015 file 260
    res = locator.locate_item(src_path, "IMPERATORIS Olla propter Græcorum exercitus", "108")
    assert res is not None
    target_file, score, reason, page, para = res
    assert "PL015" in target_file
    assert page == 1410
    assert para == 108
    assert score >= 9.0


def test_locate_item_invalid_page(locator):
    res = locator.locate_item("nonexistent", "random label", "invalid")
    assert res is None
