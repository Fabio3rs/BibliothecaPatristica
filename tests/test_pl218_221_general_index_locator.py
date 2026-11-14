"""
Unit tests for Migne General Index Locator (PL218-PL221).
"""

import os
import pytest
from tools.indexing.pl218_221_general_index_locator import (
    PLGeneralIndexLocator,
    roman_to_int,
    strip_accents_and_normalize,
)


@pytest.fixture(scope="module")
def locator():
    return PLGeneralIndexLocator()


def test_roman_to_int():
    assert roman_to_int("I") == 1
    assert roman_to_int("IV") == 4
    assert roman_to_int("X") == 10
    assert roman_to_int("LXXIV") == 74
    assert roman_to_int("CXXXI") == 131
    assert roman_to_int("CLXVI") == 166
    assert roman_to_int("CLXXXVIII") == 188
    assert roman_to_int("CCXV") == 215
    assert roman_to_int("CCXXI") == 221


def test_strip_accents_and_normalize():
    assert strip_accents_and_normalize("Ægyptiorum") == "aegyptiorum"
    assert strip_accents_and_normalize("HILDEBERTI") == "hildeberti"
    assert strip_accents_and_normalize("Petri Abælardi") == "petri abaelardi"


def test_extract_keywords(locator):
    kws = locator.extract_keywords("Lanfranc. Cantuar. archiepisc.")
    assert "lanfranc" in kws
    assert "cantuar" in kws
    assert "archiepisc" not in kws  # stopword filtered


def test_resolve_multivolume_span(locator):
    # Span XXII-XXX (PL022 to PL030) for Hieronymus
    res = locator.resolve_item(
        item_id="test_span_hieronymus",
        vol="PL218",
        label="Hieronymus (S.) presbyter Stridonensis",
        ref_raw="XXII-XXX.",
        source_file="/homessddata/Projects/pdfocr/teste/PL218/text/3af56fd3-0b18-41d0-813c-f8bb9ab2b66f-657.txt",
    )
    assert res is not None
    assert res["kind"] == "span"
    assert len(res["candidates"]) == 9  # PL022 to PL030
    assert res["candidates"][0]["target_vol"] == "PL022"
    assert res["candidates"][-1]["target_vol"] == "PL030"
    for cand in res["candidates"]:
        assert os.path.exists(cand["file"])


def test_resolve_direct_roman_and_col(locator):
    # Gratian in CLXXXVII, col 1822 -> PL187
    res = locator.resolve_item(
        item_id="test_gratian",
        vol="PL218",
        label="Gratian.",
        ref_raw="CLXXXVII, 1822",
        source_file="/homessddata/Projects/pdfocr/teste/PL218/text/3af56fd3-0b18-41d0-813c-f8bb9ab2b66f-669.txt",
    )
    assert res is not None
    assert res["target_vol"] == "PL187"
    assert res["target_col"] == 1822
    assert os.path.exists(res["matched_file"])
    assert res["score"] >= 8.5


def test_resolve_roman_override_with_second_hint(locator):
    # Hildebertus with CLXV typo -> overrides to CLXXI (PL171) col 1339
    res = locator.resolve_item(
        item_id="test_hildebert",
        vol="PL218",
        label="Hilderbertus",
        ref_raw="CLXV, 1339",
        source_file="/homessddata/Projects/pdfocr/teste/PL218/text/3af56fd3-0b18-41d0-813c-f8bb9ab2b66f-657.txt",
    )
    assert res is not None
    assert res["target_vol"] == "PL171"
    assert res["target_col"] == 1339
    assert "PL171" in res["matched_file"]
    # Check that second hint matched Hildebert in header
    assert "second_hint" in res["reason"]
    assert res["score"] >= 9.5


def test_resolve_inherited_roman_on_page(locator):
    # Capitulare Compendiense Pippini with 'ibid., 1515' inherits XCVI (PL096)
    source_file = "/homessddata/Projects/pdfocr/teste/PL219/text/4ab3e4ff-18c4-4e3d-87f8-b25702db01fd-671.txt"
    res = locator.resolve_item(
        item_id="test_pippin",
        vol="PL219",
        label="Capitulare Compendiense Pippini Franc. regis (757)",
        ref_raw="ibid., 1515",
        source_file=source_file,
    )
    assert res is not None
    assert res["target_vol"] == "PL096"
    assert res["target_col"] == 1515
    assert "PL096" in res["matched_file"]
    assert res["score"] >= 9.0
