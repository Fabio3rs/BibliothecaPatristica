"""Observed profile boundaries; these checks do not certify extraction quality."""
from pathlib import Path

import pytest

from tools.indexing.entry_parser_experiment import parse_page


ROOT = Path(__file__).resolve().parents[1]


def page(volume, physical):
    paths = list((ROOT / "teste" / volume / "text").glob(f"*-{physical}.txt"))
    assert len(paths) == 1
    raw = paths[0].read_text()
    return raw, parse_page(raw, source=str(paths[0]), physical=physical, volume=volume,
                          profile="generic-literal-experimental")


@pytest.mark.parametrize("volume,physical", [
    ("PG040", 427), ("PG127", 750), ("PG127", 753), ("PO022", 313), ("PO022", 318),
])
def test_misc_real_pages_remain_experimental_and_preserve_source_spans(volume, physical):
    raw, parsed = page(volume, physical)
    assert parsed["import_ready"] is False
    for entry in parsed["entries"]:
        assert "unvalidated_profile" in entry["flags"]
        assert raw[entry["char_start"]:entry["char_end"]] == entry["entry_raw"]
        if entry["references_raw"] is not None:
            span = entry["reference_span"]
            assert raw[span["char_start"]:span["char_end"]] == entry["references_raw"]


def test_pg040_missing_comma_is_flagged_and_table_is_not_claimed_as_entries():
    _, parsed = page("PG040", 427)
    platonici = next(e for e in parsed["entries"] if e["entry_raw"] == "Platonici 32, 50.")
    assert "numeric_text_before_reference_group" in platonici["flags"]
    assert any("Edit. Antv." in r["raw"] for r in parsed["residuals"])
    assert any("381." in r["raw"] for r in parsed["residuals"])


def test_pg127_ibid_is_not_resolved_and_subentry_parent_is_not_guessed():
    _, parsed = page("PG127", 753)
    abram = next(e for e in parsed["entries"] if e["text_display"] == "Abrami exsilium")
    assert abram["references_raw"] == "ibid"
    assert "qualified_reference_preserved" in abram["flags"]
    defender = next(e for e in parsed["entries"] if e["text_display"] == "Defensor Palamedis")
    assert defender["context_candidate_id"] is None
    assert "target_file" not in defender


def test_pg127_column_continuation_is_not_silently_certified():
    _, parsed = page("PG127", 750)
    continuation = next(e for e in parsed["entries"]
                        if e["text_display"].startswith("et ejus uxor eum generum"))
    assert "leading_continuation_requires_review" in continuation["flags"]
    assert any(r["raw"].strip() == "Andronicus" for r in parsed["residuals"])


@pytest.mark.parametrize("physical", [313, 318])
def test_po022_subscript_format_is_unsupported_not_materially_parsed(physical):
    _, parsed = page("PO022", physical)
    assert parsed["residuals"]
    assert all(e["references_raw"] is None for e in parsed["entries"])
    assert all("cross_reference_with_leading_unsegmented_text" in e["flags"]
               for e in parsed["entries"])
