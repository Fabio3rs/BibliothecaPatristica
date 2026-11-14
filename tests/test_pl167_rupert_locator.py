"""
Unit tests for tools.indexing.pl167_rupert_locator
"""

import pytest
from tools.indexing.pl167_rupert_locator import PL167RupertLocator


@pytest.fixture(scope="module")
def locator():
    return PL167RupertLocator()


def test_interpolate_low_column(locator):
    # Col 4 is directly recognized or in opening
    f = locator.interpolate_column(4)
    assert f is not None
    assert "PL167" in f


def test_interpolate_gap_column(locator):
    # Col 85 is in gap 84-86
    f = locator.interpolate_column(85)
    assert f is not None
    assert "PL167" in f


def test_resolve_item(locator):
    res = locator.resolve_item("Angelus est vestimentum Dei", "6")
    assert res is not None
    f, col = res
    assert col == 6
    assert "PL167" in f


def test_dry_run_all_promoted(locator):
    res = locator.run(dry_run=True, force_all=True)
    assert res["total_items"] > 0
    assert res["promoted"] > 0
    assert res["candidates"] > 0
