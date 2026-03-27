import pytest

from keywords_serial import _clean_list
from keywords_serial import validate_keywords
from keywords_serial import normalize_keywords_payload, clean_keywords_structure


def test_not_split_multiword_name():
    cleaned, issues = _clean_list(["Atanasio de Alexandria"])
    assert cleaned == ["Atanasio de Alexandria"]
    assert "split_conjoined_keywords" not in issues


def test_emphasized_pair_is_not_split_now():
    cleaned, issues = _clean_list(["**palavra 1** e **palavra 2**"])
    assert cleaned == ["palavra 1 e palavra 2"]
    assert "split_conjoined_keywords" not in issues


def test_commas_semicolons_list_not_split():
    cleaned, issues = _clean_list(["kw1, kw2; kw3"])
    assert cleaned == ["kw1, kw2; kw3"]
    assert "split_conjoined_keywords" not in issues


def test_strip_markdown_and_quotes():
    cleaned, issues = _clean_list(["> **\"Termo\"**"])
    assert cleaned == ["Termo"]
    assert issues == []


def test_keywords_with_numbers_trigger_issue():
    cleaned = {"keywords": ["4", "Ap 3", "Canon 35"]}
    issues = validate_keywords(cleaned, parse_issue=None)
    assert "keyword_isolated_number" in issues
    assert "few_keywords(3)" in issues


def test_normalize_keywords_payload_strips_markdown():
    payload = '{"keywords": ["**santidade**", "`Cristo`", "*porta da justiça*"]}'
    parsed, issue = normalize_keywords_payload(payload)
    cleaned, issues = clean_keywords_structure(parsed)
    assert issue is None
    assert cleaned["keywords"] == ["santidade", "Cristo", "porta da justiça"]
    assert not issues
