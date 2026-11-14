"""
Unit tests for tools.indexing.catalog_reader_client
"""

import pytest
import sqlite3
from tools.indexing.catalog_reader_client import CatalogReaderClient


@pytest.fixture(scope="module")
def client():
    return CatalogReaderClient()


def test_client_read_only_protection(client):
    stats = client.get_stats()
    assert stats["read_only"] is True
    assert stats["total_items"] >= 344000
    assert stats["coverage_pct"] >= 99.0


def test_client_search_volume(client):
    results = client.search(query="", volume="PL161", limit=10)
    assert len(results) == 10
    for r in results:
        assert r["volume"] == "PL161"
        assert r["state"] == "with_destinations"
        assert "top_destination" in r


def test_client_search_text(client):
    results = client.search(query="Aristoteles", limit=5)
    assert len(results) > 0
    for r in results:
        assert "aristoteles" in r["label"].lower() or "aristoteles" in r["ref_raw"].lower()


def test_client_get_item_decompression(client):
    # Find an item with candidates
    results = client.search(query="", volume="PL161", limit=1)
    assert len(results) == 1
    item_id = results[0]["id"]
    item = client.get_item(item_id)
    assert item is not None
    assert item["id"] == item_id
    assert len(item["candidates"]) > 0
    # Candidate must have decompressed details
    cand = item["candidates"][0]
    assert "details" in cand
    assert cand["details"].get("target_volume") == "PL161"
    assert cand["score"] > 0
