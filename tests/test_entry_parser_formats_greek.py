"""Scoped corpus smoke tests; successful syntax is not editorial ground truth."""
from pathlib import Path

import pytest

from tools.indexing.entry_parser_experiment import parse_page

ROOT = Path(__file__).resolve().parents[1]


def read_page(volume, physical):
    paths = list((ROOT / "teste" / volume / "text").glob(f"*-{physical}.txt"))
    assert len(paths) == 1
    raw = paths[0].read_text()
    result = parse_page(raw, source=str(paths[0]), physical=physical, volume=volume,
                        profile="generic-literal-experimental")
    return raw, result


@pytest.mark.parametrize("volume,physical", [
    ("PG148", 736), ("PG148", 739), ("PG113", 644), ("PG113", 653),
    ("PG113", 655), ("PG113", 660), ("PG066", 903), ("PG066", 904),
])
def test_generic_foreign_formats_preserve_spans_and_remain_unvalidated(volume, physical):
    raw, result = read_page(volume, physical)
    assert result["entries"]
    assert result["import_ready"] is False
    for entry in result["entries"]:
        assert "unvalidated_profile" in entry["flags"]
        assert raw[entry["char_start"]:entry["char_end"]] == entry["entry_raw"]
        assert entry["context_candidate_id"] is None
        if "reference_span" in entry:
            span = entry["reference_span"]
            assert raw[span["char_start"]:span["char_end"]] == entry["references_raw"]


def test_pg113_named_items_and_wrapped_roma_list_are_distinct():
    _, result = read_page("PG113", 653)
    entries = {e["text_display"]: e for e in result["entries"]}
    assert entries["Rastotza"]["references_raw"] == "96"
    assert entries["Rhamble"]["references_raw"] == "80"
    assert " ".join(entries["Roma"]["references_raw"].split()) == (
        "27, 29, 81, 82, 83, 86, 97, 100, 103, 114, 146, 147")


@pytest.mark.parametrize("volume,physical,needle", [
    ("PG148", 739, "Chamdrenus"), ("PG148", 739, "Chartarum collectio"),
    ("PG066", 904, "Auctor lib. De mundo"),
])
def test_unsupported_qualifiers_cannot_look_like_safe_plain_entries(volume, physical, needle):
    _, result = read_page(volume, physical)
    matches = [e for e in result["entries"] if needle in e["entry_raw"]]
    assert len(matches) == 1
    assert "numeric_text_before_reference_group" in matches[0]["flags"]
    assert matches[0]["status"] == "candidate_only"


def test_pg113_leading_page_fragment_is_not_assigned_an_invented_owner():
    _, result = read_page("PG113", 655)
    first = result["entries"][0]
    assert first["entry_raw"].startswith("manorum)")
    assert "leading_continuation_requires_review" in first["flags"]
    assert first["context_candidate_id"] is None
