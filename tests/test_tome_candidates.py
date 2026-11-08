from pathlib import Path

import pytest

from tools.indexing.tome_candidates import parse_tome_candidates


def parse(raw, **kwargs):
    result = parse_tome_candidates({"entry_raw": raw, "char_start": 103}, **kwargs)
    if result:
        for ref in result["interpreted_references"]:
            a, b = ref["source_span"]
            assert raw[a - 103:b - 103] == ref["ref_raw"]
            a, b = ref["owner_span"]
            assert raw[a - 103:b - 103] == ref["owner_raw"]
    return result


def test_numeric_list_tome_switch_and_closed_range():
    result = parse("Virtus, II, 70, 72–74; III, 517.")
    refs = result["interpreted_references"]
    assert [(r["tome"], r["number"], r["range_end"]) for r in refs] == [
        ("II", 70, None), ("II", 72, 74), ("III", 517, None)]
    assert refs[1]["tome_basis"] == "same_numeric_group"
    assert refs[2]["tome_basis"] == "explicit"


def test_column_subdivision_lists_and_qualifier():
    refs = parse("ACHAMOTH, æon Valentini, II, 68 B, 564 A, B, 568 A, 571 sqq.",
                 unit="column")["interpreted_references"]
    assert [r["number"] for r in refs] == [68, 564, 568, 571]
    assert refs[1]["subdivision_raw"] == "A, B"
    assert refs[-1]["qualifiers"][0]["raw"] == "sqq."
    assert refs[-1]["range_end"] is None


def test_multiple_clauses_keep_separate_owners():
    refs = parse("ABELIS sacrificia Deus accepit, II, 601 A. Quare, ibid. "
                 "Populum christianum demonstrabat, II, 607 A.", unit="column")["interpreted_references"]
    assert len(refs) == 2
    assert refs[0]["owner_raw"] == "ABELIS sacrificia Deus accepit"
    assert refs[1]["owner_raw"] == "Populum christianum demonstrabat"
    assert refs[1]["owner_context_raw"] == "Quare, ibid. Populum christianum demonstrabat"
    assert "owner_clause_not_principal_lemma" in refs[1]["flags"]


def test_a_preposition_not_column_subdivision():
    refs = parse("Res, II, 17 A Phoca devictus, III, 18.", unit="column")["interpreted_references"]
    assert refs[0]["subdivision_raw"] is None
    assert refs[1]["owner_raw"] == "A Phoca devictus"


def test_does_not_inherit_across_prose():
    result = parse("ABRA unica Hilarii filia, II, 525, scripsit patri, ibid.; suadetur, 526.")
    assert [r["number"] for r in result["interpreted_references"]] == [525]
    assert "526" in result["non_locator_spans"][-1]["raw"]


def test_numbers_in_owner_not_taken_as_locators():
    result = parse("Tria 3 sunt genera; de die 40 disputat, II, 525.")
    assert len(result["interpreted_references"]) == 1
    assert "40" in result["interpreted_references"][0]["owner_raw"]


def test_ibid_only_inside_numeric_list():
    refs = parse("Amor I, 20, ibid.; II, 30.")["interpreted_references"]
    assert [r["number"] for r in refs] == [20, 20, 30]
    assert "ibid_same_numeric_group_candidate" in refs[1]["flags"]


def test_invalid_range_kept_flagged_not_expanded():
    ref = parse("Amor I, 30–20.")["interpreted_references"][0]
    assert "invalid_numeric_range" in ref["flags"]
    assert ref["range_end"] == 20


@pytest.mark.parametrize("raw", ["Roma 519", "Nota IIII, 19", "V. Beatitudo.", "In anno 300 natus."])
def test_no_explicit_tome_group(raw):
    assert parse(raw) is None


def test_real_pl010_unpunctuated_entry_and_note_scope():
    path = Path("teste/PL010/text/df16dfcc-4a07-4c19-a107-59484f57a476-462.txt")
    source = path.read_text()
    raw = "AARON consecratio. I, 519"
    assert raw in source
    ref = parse(raw, namespace="verona")["interpreted_references"][0]
    assert ref["number"] == 519
    assert ref["namespace"] == "verona"
    assert ref["owner_raw"] == "AARON consecratio"


def test_real_pl003_third_tome_not_volume_number():
    path = Path("teste/PL003/text/18af8a0f-5a90-4c9e-b2bd-8cf36fc44e37-807.txt")
    source = path.read_text()
    raw = "Aaron et filii ejus cum adirent ad sanctum, prohibiti sunt portare vinum, III, 517 B."
    assert raw in source
    ref = parse(raw, unit="column")["interpreted_references"][0]
    assert (ref["tome"], ref["number"], ref["subdivision_raw"]) == ("III", 517, "B")
    assert "target_volume" not in ref
