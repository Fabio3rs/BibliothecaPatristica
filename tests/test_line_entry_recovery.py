import hashlib

from tools.indexing.line_entry_recovery import repair_line_owner


def group(raw, owner, reference):
    start = raw.index(reference, raw.index(owner) + len(owner))
    return {"id": "fixture", "source": "fixture.txt", "owner_raw": owner,
            "raw": reference, "char_start": start, "char_end": start + len(reference),
            "source_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def assert_spans(raw, result):
    for entry in result["entries"]:
        for stem in ("owner", "reference", "parent_owner"):
            a, b = entry[f"{stem}_span"]
            assert raw[a:b] == entry[f"{stem}_raw"]
    start, end = result["source_span"]
    coverage = result["coverage"]
    assert coverage[0]["span"][0] == start
    assert coverage[-1]["span"][1] == end
    assert all(a["span"][1] == b["span"][0] for a, b in zip(coverage, coverage[1:]))
    assert result["accounted_characters"] == end - start
    assert not any(row["kind"] == "overlap" for row in coverage)


def test_recovers_list_prefix_and_neighboring_owners():
    raw = "Πισιδία 40.\nΠιτουοῦς 495.\nΠλακίλλα Placilla 464, 467, 479."
    owner = raw[:raw.index(", 467")]
    result = repair_line_owner(raw, group(raw, owner, "467, 479"))
    assert result["status"] == "candidate_repair"
    assert [e["owner_raw"] for e in result["entries"]] == [
        "Πισιδία", "Πιτουοῦς", "Πλακίλλα Placilla"]
    assert result["entries"][2]["reference_raw"] == "464, 467, 479"
    assert result["existing_reference_entry"] == 2
    assert_spans(raw, result)


def test_keeps_numeric_wrap_and_inherits_darius_context():
    raw = "Δάραι 621.\nΔαρεῖος, Ἀστυάγης 13, 196,\n205, Ὑστάσπου 200, 206, 293."
    owner = raw[:raw.index(", 206")]
    result = repair_line_owner(raw, group(raw, owner, "206, 293"))
    assert len(result["entries"]) == 3
    assert result["entries"][1]["reference_raw"] == "13, 196,\n205"
    matched = result["entries"][result["existing_reference_entry"]]
    assert matched["owner_raw"] == "Ὑστάσπου"
    assert matched["parent_owner_raw"] == "Δαρεῖος, Ἀστυάγης"
    assert "inherits_parent_context" in matched["flags"]
    assert_spans(raw, result)


def test_maria_subclauses_not_promoted_to_independent_headwords():
    raw = ("Μαρδόνιος εὐνοῦχος 481.\nΜαρδοχαῖος ἀνὴρ μικρὸς\n196.\n"
           "Μαρία מרים ἀντιλογία, ἀδελφὴ\nΜωϋσέως 88 — 90. Ἰουδαία 287.\n"
           "Θεοτόκος 204, 221, 498, 500,\n510, 512, 514, 553, 593, 600, 608.")
    owner = raw[:raw.index(", 221")]
    result = repair_line_owner(raw, group(raw, owner, "221, 498, 500,\n510, 512, 514, 553, 593"))
    matched = result["entries"][result["existing_reference_entry"]]
    assert matched["owner_raw"] == "Θεοτόκος"
    assert matched["parent_owner_raw"].startswith("Μαρία")
    assert matched["reference_raw"].endswith("593, 600, 608")
    assert_spans(raw, result)


def test_hyphenated_name_and_numeric_only_line_remain_together():
    raw = "Λοκρίς 40.\nΛουκᾶς, ὁ ἅγιος εὐαγγε-\nλιστὴς τοῦ Χριστοῦ\n279, 351, 438."
    owner = raw[:raw.index(", 351")]
    result = repair_line_owner(raw, group(raw, owner, "351, 438"))
    assert len(result["entries"]) == 2
    assert result["entries"][-1]["owner_raw"].startswith("Λουκᾶς")
    assert result["entries"][-1]["reference_raw"] == "279, 351, 438"
    assert_spans(raw, result)


def test_preserves_uncertain_missing_reference_boundary():
    raw = "Λαφφιδώθ λαμπάδες 104.\nΛέα ἐπώουσα, κοπιῶσα?\nΛειχούδης πατρ. ΚΠ. 882.\nλείψανα 643, 654."
    owner = raw[:raw.index(", 654")]
    result = repair_line_owner(raw, group(raw, owner, "654"))
    assert "possible_unrecognized_entry_boundary" in result["ambiguities"]
    assert_spans(raw, result)


def test_letters_as_headings_are_not_attached_to_next_name():
    raw = "Ὄχος 17.\nΠ.\nΠαγκρατούσιος 805.\nΠαλαιστίνη 12, 38."
    result = repair_line_owner(raw, group(raw, raw[:raw.index(", 38")], "38"))
    assert result["entries"][1]["owner_raw"] == "Παγκρατούσιος"
    assert any(x["kind"] == "alphabetic_heading" for x in result["leftovers"])


def test_changed_hash_with_matching_local_spans_remains_candidate():
    raw = "Πισιδία 40.\nΠαλαιστίνη 12, 38."
    source_group = group(raw, "Πισιδία 40.", "38")
    assert repair_line_owner(raw, source_group)["status"] == "abstained"
    source_group = group(raw, raw[:raw.index(", 38")], "38")
    source_group["source_sha256"] = "bad"
    result = repair_line_owner(raw, source_group)
    assert result["status"] == "candidate_repair"
    assert result["ambiguities"] == ["source_changed_local_spans_match"]
    assert_spans(raw, result)


def test_changed_numeric_reference_abstains():
    raw = "Παλαιστίνη 12, 38."
    source_group = group(raw, "Παλαιστίνη 12", "38")
    assert repair_line_owner(raw.replace("38", "39"), source_group)["status"] == "abstained"


def test_reset_context_after_incomplete_previous_headword():
    raw = "κοινωνίτης 751. στρατηγὸς 638.\nΠαυσολύπων, ὁ ἅγιος 340.\nΠαφλαγονία, Παφλαγών 28, 40."
    source_group = group(raw, raw[:raw.index(", 40")], "40")
    result = repair_line_owner(raw, source_group)
    matched = result["entries"][result["existing_reference_entry"]]
    assert matched["parent_owner_raw"] == "Παφλαγονία, Παφλαγών"
    assert "inherits_parent_context" not in matched["flags"]
    assert_spans(raw, result)


def test_numeric_continuation_after_ocr_period_is_flagged_not_merged():
    raw = "Μαρδόνιος 481.\nΜαρία Θεοτόκος 204, 593. 600, 608."
    result = repair_line_owner(raw, group(raw, raw[:raw.index(", 593")], "593"))
    matched = result["entries"][result["existing_reference_entry"]]
    assert matched["reference_raw"] == "204, 593"
    assert "numeric_continuation_after_period_requires_ocr_review" in result["ambiguities"]


def test_cannot_cross_known_ocr_block_boundary():
    raw = '<pagina><bloco tipo="texto_principal">Πισιδία 40.</bloco><bloco tipo="texto_principal">Παλαιστίνη 12, 38.</bloco></pagina>'
    owner = raw[raw.index("Πισιδία"):raw.index(", 38")]
    result = repair_line_owner(raw, group(raw, owner, "38"))
    assert result["status"] == "abstained"
    assert "owner_reference_crosses_ocr_blocks" in result["ambiguities"]


def test_known_block_preserves_literal_offsets():
    raw = '<pagina><bloco tipo="texto_principal">Πισιδία 40.\nΠαλαιστίνη 12, 38.</bloco></pagina>'
    owner = raw[raw.index("Πισιδία"):raw.index(", 38")]
    result = repair_line_owner(raw, group(raw, owner, "38"))
    assert result["status"] == "candidate_repair"
    assert_spans(raw, result)
