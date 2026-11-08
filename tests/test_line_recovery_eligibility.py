"""Small literal excerpts from the global audit, without database dependencies."""
import hashlib
from copy import deepcopy

import pytest

from tools.indexing.line_entry_recovery import repair_line_owner
from tools.indexing.line_recovery_eligibility import assess_line_recovery


def assess(raw, reference, **fields):
    start = raw.rindex(reference)
    owner = raw[:start].rstrip(" ,;:")
    group = {"owner_raw": owner, "raw": reference, "char_start": start,
             "char_end": start + len(reference),
             "source_sha256": hashlib.sha256(raw.encode()).hexdigest(), **fields}
    return assess_line_recovery(group, repair_line_owner(raw, group))


def test_pg002_simple_numeric_rows_are_eligible_without_interpreting_pages():
    # Group 5c3880ca6d87a6e66e69959f4a63b00084d8d531430ae215fcfee6bf82f9e7af.
    result = assess("Λαόνης 145.\n    λάχανον 258.\n    λειποτάκτης 18, 22.", "18, 22")
    assert result["eligible"]
    assert result["reference_interpretation"] == "unchanged"
    assert not result["destination_inference_allowed"]


def test_pg110_onomastic_rows_are_eligible():
    result = assess("Πισιδία 40.\nΠιτουοῦς 495.\nΠλακίλλα Placilla 464, 467, 479.", "467, 479")
    assert result["eligible"]


def test_pg067_years_are_not_locators():
    # Group 74d53b5d7b3e56d985282710b6825fbd8d8f8234883088f40d39076a543cafbf.
    result = assess("Ephraem Syrus obiit circa annum 378. Soz. 119.\n"
                    "Epiphanius diem supremum obiit anno 402. Socr. 350.\n"
                    "Episcopalis ordinationis necessitas. Socr. 64.", "64")
    assert not result["eligible"]
    assert "date_or_age_numbers_in_text" in result["reasons"]
    assert "typed_locator_requires_scoped_parser" in result["reasons"]


@pytest.mark.parametrize("qualifier", ["cap.", "vers.", "§", "n.", "not.", "I", "B", "Leo"])
def test_qualifiers_and_namespaces_require_specialized_parsers(qualifier):
    result = assess(f"Alpha {qualifier} 12.\nAltera {qualifier} 25.\nAlta {qualifier} 38.", "38")
    assert not result["eligible"]


def test_plain_pairs_do_not_grant_page_interpretation():
    raw = "Alpha 770, 9.\nAltera 600, 12.\nAlta 520, 18."
    result = assess(raw, "520, 18")
    assert result["eligible"]
    assert not result["destination_inference_allowed"]
    assert not assess(raw, "520, 18", notation="page_line")["eligible"]


def test_pg110_subclauses_require_preserved_parent_context():
    result = assess("Δάνατρις 652.\nΔάναστρις 652.\n"
                    "Δανιήλ 13, 62, στρατηγός 549, 600.", "600")
    assert result["eligible"]
    assert "subclause_context_requires_preservation" in result["warnings"]


def test_numeric_period_continuation_keeps_literal_proposal_and_warning():
    result = assess("Alpha 12.\nAltera 25.\nAlta 38. 45, 50.", "38")
    assert result["eligible"]
    assert "numeric_continuation_after_period_requires_ocr_review" in result["warnings"]


def test_uncertain_numeric_typography_is_not_eligible():
    assert not assess("Alpha 12₃.\nAltera 25₆.\nAlta 38₈.", "38₈")["eligible"]


def test_single_entry_does_not_establish_a_line_family():
    assert not assess("Alpha 12, 25, 38.", "38")["eligible"]


def test_pg110_655_excludes_uncertain_child_without_losing_good_original():
    raw = ("Λαφφιδώθ λαμπάδες 104.\nΛέα ἐπώουσα, κοπιῶσα?\n"
           "Λειχούδης πατρ. ΚΠ. 882.\nλείψανα 643, 654.")
    start = raw.index("654")
    group = {"owner_raw": raw[:raw.index(", 654")], "raw": "654",
             "char_start": start, "char_end": start + 3}
    proposal = repair_line_owner(raw, group)
    original = deepcopy(proposal)
    result = assess_line_recovery(group, proposal)
    assert result["eligible"]
    assert result["excluded_entry_indices"] == [1]
    assert proposal["existing_reference_entry"] == 2
    assert "possible_unrecognized_entry_boundary" in result["warnings"]
    assert proposal == original


def test_uncertain_original_entry_is_ineligible():
    raw = ("Λαφφιδώθ λαμπάδες 104.\nΛέα ἐπώουσα, κοπιῶσα?\n"
           "Λειχούδης πατρ. ΚΠ. 882.\nλείψανα 643, 654.")
    result = assess(raw, "882")
    assert not result["eligible"]
    assert result["excluded_entry_indices"] == [1]
    assert "original_reference_entry_excluded" in result["reasons"]


def test_boundary_ambiguity_without_localized_entry_remains_ineligible():
    raw = "Alpha 12.\nAltera 25.\nAlta 38."
    start = raw.index("38")
    group = {"owner_raw": raw[:start].strip(), "raw": "38",
             "char_start": start, "char_end": start + 2}
    proposal = repair_line_owner(raw, group)
    proposal["ambiguities"].append("possible_unrecognized_entry_boundary")
    result = assess_line_recovery(group, proposal)
    assert not result["eligible"]
    assert result["excluded_entry_indices"] == []
    assert "parser_ambiguities_require_review" in result["reasons"]
