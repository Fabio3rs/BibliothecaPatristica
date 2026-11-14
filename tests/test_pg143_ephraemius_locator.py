"""
Unit tests for tools.indexing.pg143_ephraemius_locator
"""

import pytest
from tools.indexing.pg143_ephraemius_locator import PG143EphraemiusLocator


@pytest.fixture(scope="module")
def locator():
    return PG143EphraemiusLocator()


def test_verse_to_page_early(locator):
    # Verse 10 should be on page 14
    pg = locator.verse_to_page(10)
    assert 13 <= pg <= 15


def test_verse_to_page_mid(locator):
    # Verse 5500 should be around page 110-120
    pg = locator.verse_to_page(5500)
    assert 105 <= pg <= 125


def test_verse_to_page_late(locator):
    # Verse 10538 should be around page 200-207
    pg = locator.verse_to_page(10538)
    assert 195 <= pg <= 207


def test_resolve_item_single(locator):
    res = locator.resolve_item("Andronicus Angelus", "4925")
    assert res is not None
    files, verses = res
    assert verses == [4925]
    assert len(files) == 1
    assert "PG143" in files[0]


def test_resolve_item_multiple(locator):
    res = locator.resolve_item("Costostephanus Ducas", "4935, 8136, 8647, 10137")
    assert res is not None
    files, verses = res
    assert verses == [4935, 8136, 8647, 10137]
    assert len(files) >= 2


def test_dry_run_all_promoted(locator):
    res = locator.run(dry_run=True, force_all=True)
    assert res["total_items"] > 0
    assert res["promoted"] > 0
    assert res["candidates"] > 0
