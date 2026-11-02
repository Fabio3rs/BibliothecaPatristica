"""Alignment regressions use small artifacts; no database, model or network calls."""
from collections import Counter
import json
from pathlib import Path
import sys
import unicodedata

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_ocr.diff_v2 import compare, sequence_diff


def assert_partition(result):
    for side in ("candidate", "reference"):
        expected = [t["id"] for t in result[f"{side}_tokens"]]
        actual = [x for link in result["links"] for x in link[f"{side}_ids"]]
        assert Counter(actual) == Counter(expected)
        assert all(count == 1 for count in Counter(actual).values())
        assert sum(result["coverage"][side]["by_status"].values()) == len(expected)


def missing(result):
    return " ".join(x["text"] for x in result["omission_candidates"])


@pytest.mark.parametrize("text", ["", "et", "et et et", "In principio erat Verbum.",
                                      "λόγος καὶ θεός", "ܐܠܗܐ ܡܠܬܐ", "١٢٣ 1276 71"])
def test_identical_has_no_omissions(text):
    result = compare(text, text)
    assert not result["omission_candidates"]
    assert not result["addition_candidates"]
    assert_partition(result)


@pytest.mark.parametrize("a,b,kind", [
    ("adulteros", "aduIteros", "ocr_variant"),
    ("homicidas", "homicldas", "ocr_variant"),
    ("sobrius", "sobrios", "ocr_variant"),
    ("animæ", "animae", "typography_variant"),
    ("ſalvat", "salvat", "typography_variant"),
    ("λόγος", "λογος", "typography_variant"),
    ("λόγος", unicodedata.normalize("NFD", "λόγος"), "normalized_equal"),
    ("1276", "1275", "uncertain_substitution"),
    ("res", "rex", "ocr_variant"),
])
def test_corrupt_words_remain_paired(a, b, kind):
    result = compare(f"Ante verbum {a} postea sequitur", f"Ante verbum {b} postea sequitur")
    assert not result["omission_candidates"]
    assert not result["addition_candidates"]
    pair = next(l for l in result["links"] if l["candidate_text"] == a)
    assert pair["reference_text"] == b
    assert pair["status"] == kind
    assert pair["character_edits"]
    assert_partition(result)


@pytest.mark.parametrize("candidate,reference,expected", [
    ("Et Petrus Ergo si cognoverint eum", "Et Petrus Ergo et adulteros et homicidas salvat si cognoverint eum", "adulteros et homicidas"),
    ("primum deinde", "initium primum deinde", "initium"),
    ("primum deinde", "primum deinde finis", "finis"),
    ("λόγος θεός", "λόγος καὶ θεός", "καὶ"),
    ("bonus est", "bonus non est", "non"),
    ("in principio erat verbum", "in principio erat erat verbum", "erat"),
    ("", "adulteros et homicidas", "adulteros et homicidas"),
])
def test_missing_words_preserve_multiplicity(candidate, reference, expected):
    result = compare(candidate, reference)
    assert expected in missing(result)
    assert_partition(result)


def test_tesseract_omission_has_opposite_direction():
    result = compare("Et adulteros et homicidas salvat", "Et salvat")
    assert not result["omission_candidates"]
    assert "adulteros et homicidas" in " ".join(x["text"] for x in result["addition_candidates"])
    assert_partition(result)


@pytest.mark.parametrize("a,b", [("semel ipsum", "semelipsum"), ("misericordes", "miseri cordes"),
                                  ("conditorem", "con di torem")])
def test_split_merge_is_not_missing(a, b):
    result = compare(f"Ante {a} postea", f"Ante {b} postea")
    assert result["stats"].get("split_merge") == 1
    assert not result["omission_candidates"]
    assert not result["addition_candidates"]
    assert_partition(result)


def test_markup_entities_hyphen_and_unicode_keep_source_spans():
    text = '<pagina><bloco>mi-\nsericordes &amp; <i>λόγος</i> &#x3B8;εός</bloco><notas>adulteros</notas></pagina>'
    result = compare(text, "misericordes λόγος θεός")
    assert [t["text"] for t in result["candidate_tokens"]] == ["misericordes", "λόγος", "θεός"]
    tokens = result["candidate_tokens"]
    assert [text[a:b] for p in tokens[0]["pieces"] for a, b in p["source_spans"]] == ["mi", "sericordes"]
    assert [text[a:b] for p in tokens[2]["pieces"] for a, b in p["source_spans"]] == ["&#x3B8;εός"]
    assert not result["omission_candidates"]
    assert_partition(result)


def test_no_hyphen_join_across_blocks():
    result = compare('<pagina><bloco>con-\n</bloco><bloco>ditor</bloco></pagina>', "con ditor")
    assert [t["text"] for t in result["candidate_tokens"]] == ["con", "ditor"]


def test_unrelated_large_gap_abstains_with_complete_partition():
    a, b = " ".join(["abc"] * 30), " ".join(["xyz"] * 30)
    result = compare(a, b, max_cells=50)
    assert result["stats"] == {"unresolved": 1}
    assert not result["omission_candidates"]
    assert_partition(result)


def test_unrelated_substitution_does_not_claim_ocr_equivalence():
    result = compare("ante qqqqq post", "ante zzzzz post")
    assert not result["stats"].get("ocr_variant")
    assert not result["omission_candidates"]
    assert result["stats"].get("uncertain_substitution") == 1
    assert result["coverage"]["candidate"]["supported_tokens"] == 2
    assert_partition(result)


def test_reordered_passage_is_recovered_without_dropping_tokens():
    result = compare("alpha beta gamma delta epsilon zeta", "delta epsilon zeta alpha beta gamma")
    assert result["stats"].get("reordered")
    assert not result["omission_candidates"]
    assert not result["addition_candidates"]
    assert_partition(result)


def test_repeated_quotation_still_has_missing_occurrence():
    phrase = "in principio erat verbum"
    result = compare(f"{phrase} finis", f"{phrase} {phrase} finis")
    assert sum(x["token_count"] for x in result["omission_candidates"]) == 4
    assert_partition(result)


def test_text_order_mode_exposes_limit_and_never_approves():
    result = compare("abc", "abc")
    assert result["mode"] == "text_order_only"
    assert not result["automatic_acceptance"]
    assert any("interleaved columns" in w for w in result["warnings"])


def test_rejects_ambiguous_reference_and_missing_dimensions():
    with pytest.raises(ValueError):
        compare("a")
    with pytest.raises(ValueError):
        compare("a", "b", reference_tsv="tsv")
    with pytest.raises(ValueError):
        compare("a", reference_tsv="tsv")
    with pytest.raises(ValueError):
        sequence_diff(["a"], ["b"], max_cells=0)


def test_real_pg001_638_known_omission_and_historical_control():
    root = Path(__file__).parent / "fixtures" / "ocr_diff_v2"
    fixture = json.loads((root / "manifest.json").read_text())
    tsv = (root / "PG001_638_migne_raw.tsv").read_text()
    for item in fixture["cases"]:
        result = compare((root / item["candidate"]).read_text(), reference_tsv=tsv,
                         image_size=fixture["image_size"])
        assert result["mode"] == "text_and_geometry"
        for word in ("adulteros", "homicidas"):
            pairs = [l for l in result["links"] if l["reference_text"] == word]
            assert len(pairs) == 1
            assert pairs[0]["status"] == item["target_status"]
        assert_partition(result)


def test_missing_deo_is_not_stolen_by_other_column():
    root = Path(__file__).parent / "fixtures" / "ocr_diff_v2"
    previous = (root / "PG001_638_previous.xml").read_text()
    assert previous.count("a bono Deo sunt") == 1
    result = compare(previous.replace("a bono Deo sunt", "a bono sunt"),
                     reference_tsv=(root / "PG001_638_migne_raw.tsv").read_text(),
                     image_size=(2480, 3509))
    reference = next(t for t in result["reference_tokens"] if t["text"] == "Deo"
                     and t["pieces"][0]["bbox"][0] < 500)
    pair = next(l for l in result["links"] if reference["id"] in l["reference_ids"])
    assert pair["status"] == "reference_only"
    assert_partition(result)


@pytest.mark.parametrize("second_y,can_merge", [(20, True), (500, False)])
def test_split_merge_requires_physical_neighbors(second_y, can_merge):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv = (header + "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t\n"
           + "5\t1\t1\t1\t1\t1\t20\t20\t60\t20\t90\tcondito\n"
           + f"5\t1\t1\t1\t2\t1\t90\t{second_y}\t30\t20\t90\trem\n")
    result = compare('<pagina><bloco bbox="0,0,1000,1000">conditorem</bloco></pagina>',
                     reference_tsv=tsv, image_size=(1000, 1000))
    assert bool(result["stats"].get("split_merge")) == can_merge
    assert_partition(result)


def test_controlled_deletions_and_corruptions_on_real_page():
    from tools.benchmark_ocr_alignment import run

    report = run()
    assert all(c["passed"] for c in report["real_cases"] if c["mode"] == "tsv")
    assert all(case["passed"] for case in report["controlled_edits"])
    assert report["summary"]["deleted_reference_tokens"] == 108
    assert report["summary"]["detected_deleted_tokens"] == 108
