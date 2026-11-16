"""
Unit tests for tools.indexing.catalog_reader_client
"""

import os
import pytest
from tools.indexing.catalog_reader_client import CatalogReaderClient


@pytest.fixture(scope="module")
def client():
    return CatalogReaderClient(corpus_root="/homessddata/Projects/pdfocr")


def test_client_read_only_protection(client):
    stats = client.get_stats()
    assert stats["read_only"] is True
    assert stats["total_items"] >= 344000
    assert stats["coverage_pct"] >= 99.8
    assert stats["corpus_root"] == "/homessddata/Projects/pdfocr"


def test_client_search_volume(client):
    results = client.search(query="", volume="PL161", limit=10)
    assert len(results) == 10
    for r in results:
        assert r["volume"] == "PL161"
        assert r["state"] == "with_destinations"
        assert "top_destination" in r
        assert "top_destination_relative" in r
        assert not r["top_destination_relative"].startswith("/homessddata/Projects/pdfocr")


def test_client_search_text(client):
    results = client.search(query="Aristoteles", limit=5)
    assert len(results) > 0
    for r in results:
        assert "aristoteles" in r["label"].lower() or "aristoteles" in r["ref_raw"].lower()
        assert "index_category" in r
        assert "entity_type" in r


def test_client_search_by_category_and_entity(client):
    results = client.search(category="AUCTORES", entity_type="AUCTOR_PATRISTICUS", limit=5)
    assert len(results) > 0
    for r in results:
        assert r["index_category"] == "AUCTORES"
        assert r["entity_type"] == "AUCTOR_PATRISTICUS"


def test_client_search_by_section(client):
    results = client.search(section="INDEX AUCTORUM", limit=5)
    assert len(results) > 0
    for r in results:
        assert "INDEX AUCTORUM" in r["section"].upper()


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
    assert "file_relative" in cand
    assert not cand["file_relative"].startswith("/homessddata/Projects/pdfocr")


def test_client_export_web_json(client):
    web_data = client.export_web_json(query="Augustinus", category="AUCTORES", limit=3)
    assert "metadata" in web_data
    assert "entries" in web_data
    assert web_data["metadata"]["count"] == len(web_data["entries"])
    assert web_data["metadata"]["count"] > 0

    entry = web_data["entries"][0]
    assert "id" in entry
    assert "label" in entry
    assert "volume" in entry
    assert "section" in entry
    assert "category" in entry
    assert "entity_type" in entry
    assert "ref_raw" in entry
    assert "state" in entry
    assert "source" in entry
    assert "relative_path" in entry["source"]
    assert "destinations" in entry
    if entry["destinations"]:
        dest = entry["destinations"][0]
        assert "rank" in dest
        assert "score" in dest
        assert "relative_path" in dest
        assert "snippet" in dest
