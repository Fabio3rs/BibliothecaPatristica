from pathlib import Path

from tools.indexing.reviewed_po_named_targets import (
    build_map, document_headings, locate, page_sequence,
)


def write_page(tmp_path, physical, text):
    path = tmp_path / f"fixture-{physical}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def block(text, kind="texto_principal"):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def test_corrupt_header_uses_both_neighbors_without_global_offset(tmp_path):
    files = {}
    for p in range(20, 27):
        printed = 1 if p == 23 else p - 10
        files[p] = write_page(tmp_path, p, block(
            f"{p+100} INTRODUCTION. [{printed}]", "cabecalho") + block("Text"))
    mapping = page_sequence(files)
    assert "1" not in mapping
    row = mapping["13"][0]
    assert row["physical"] == 23
    assert "contradictory_ocr_header" in row["uncertainties"]
    assert len(row["evidence"]) == 3
    assert page_sequence(files) == mapping


def test_no_interpolation_over_numbering_restart_or_outside_scope(tmp_path):
    files = {}
    for p, printed in [(10, 20), (11, 21), (12, 22), (13, None), (14, 1), (15, 2), (16, 3)]:
        text = "" if printed is None else f"600 INTRODUCTION. [{printed}]"
        files[p] = write_page(tmp_path, p, block(text, "cabecalho") + block("Text"))
    mapping = page_sequence(files)
    assert not any(x["physical"] == 13 for rows in mapping.values() for x in rows)


def test_unprinted_introduction_requires_explicit_title(tmp_path):
    files = {n: write_page(tmp_path, n, block(f"600 INTRODUCTION. [{n-10}]", "cabecalho"))
             for n in [12, 13, 14]}
    files[11] = write_page(tmp_path, 11, block("INTRODUCTION", "cabecalho"))
    files[10] = write_page(tmp_path, 10, block("PERMIS D'IMPRIMER", "cabecalho"))
    mapping = page_sequence(files)
    assert mapping["1"][0]["physical"] == 11
    assert "0" not in mapping


def test_document_list_does_not_become_document_or_swallow_mixed_page(tmp_path):
    files = {
        1: write_page(tmp_path, 1, block("1) Évangile de Jean.\n2) Évangile de Luc.")
                      + block("1) Évangile de Jean.")),
        2: write_page(tmp_path, 2, block("Continuation") + block("2 Évangile de Luc.", "rodape")),
        3: write_page(tmp_path, 3, block("Text")),
    }
    result = document_headings(files, "livres canoniques")
    assert [x["physical"] for x in result["1"]] == [1, 2]
    assert [x["physical"] for x in result["2"]] == [2, 3]
    assert result["2"][0]["evidence"][0]["raw"] == "2 Évangile de Luc."


def test_letter_headings_exclude_bibliographers_and_accept_notes(tmp_path):
    files = {
        1: write_page(tmp_path, 1, block("J. DOELGER, citation.") + block("K. Amulette de Berlin.")),
        2: write_page(tmp_path, 2, block("L. Amulette chrétienne.", "nota")),
    }
    result = document_headings(files, "amulette")
    assert set(result) == {"K", "L"}
    assert [r["physical"] for r in result["K"]] == [1, 2]


def test_canon_number_never_becomes_page_and_multiple_targets_preserved():
    mapping = {"po008_pages": {
        "48": [{"file": "wrong", "score": 1, "physical": 48, "uncertainties": []}],
        "77": [{"file": "a", "score": .9, "physical": 639, "uncertainties": []}],
        "88": [{"file": "b", "score": .9, "physical": 650, "uncertainties": []}],
    }}
    entry = {"section_id": "po008-apostles-canons-subjects", "reference_namespace": "book_canon_page",
             "editorial_locators": [{"canon_raw": "48", "page_tokens": [{"raw": "77"}, {"raw": "88"}]}]}
    assert [x["file"] for x in locate(entry, mapping)] == ["a", "b"]
    assert locate({**entry, "section_id": "unrelated-section"}, mapping) == []


def test_real_scopes_regressions_and_evidence_literals():
    root = Path(__file__).resolve().parents[1]
    mapping = build_map(root)
    assert mapping["po008_pages"]["88"][0]["physical"] == 650
    assert mapping["po008_pages"]["3"][0]["physical"] == 565
    assert [x["physical"] for x in mapping["po018_documents"]["amulette"]["O"]] == [427, 428, 429, 430]
    assert [x["physical"] for x in mapping["po018_documents"]["livres canoniques"]["2"]] == [462, 463, 464]
    assert [x["physical"] for x in mapping["po018_documents"]["textes divers"]["11"]] == [500, 501, 502, 503]
    assert "24" not in mapping["po018_documents"]["textes divers"]
    rows = [row for group in mapping["po008_pages"].values() for row in group]
    rows += [row for docs in mapping["po018_documents"].values() for group in docs.values() for row in group]
    for row in rows:
        for evidence in row["evidence"]:
            start, end = evidence["span"]
            assert Path(evidence["source"]).read_text(encoding="utf-8")[start:end] == evidence["raw"]


def test_named_multi_document_union():
    row = lambda file, p: {"file": file, "physical": p, "score": .8, "uncertainties": []}
    mapping = {"po018_documents": {"amulette": {"J": [row("a", 1)], "L": [row("a", 1), row("b", 2)]}}}
    entry = {"section_id": "po018-nubian-scripture", "named_locator": {
        "division_raw": "Amulettes", "codes": [{"raw": "J"}, {"raw": "L"}]}}
    result = locate(entry, mapping)
    assert [r["file"] for r in result] == ["a", "b"]
    assert len(result[0]["reference_tokens"]) == 2


def test_scripture_ranges_keep_interior_pages_and_literal_spans():
    mapping = {"po008_pages": {
        str(n): [{"file": f"p{n}", "score": .9, "physical": n + 562, "uncertainties": []}]
        for n in range(80, 100)
    }}
    entry = {"section_id": "po008-apostles-canons-scripture", "reference_raw": "89-91 et 94",
             "reference_span": [100, 111]}
    result = locate(entry, mapping)
    assert [x["file"] for x in result] == ["p89", "p90", "p91", "p94"]
    assert result[1]["reference_token"]["page"] == 90
    assert result[1]["reference_token"]["raw"] == "89-91"
    assert result[1]["reference_token"]["span"] == [100, 105]
    assert result[-1]["reference_token"]["span"] == [109, 111]
    assert locate({**entry, "reference_raw": "91-89"}, mapping) == []


def test_shared_page_accumulates_evidence_and_document_identities():
    def row(code, evidence):
        return {"file": "shared", "physical": 423, "score": .8, "document_code": code,
                "uncertainties": [code], "evidence": [evidence]}
    mapping = {"po018_documents": {"amulette": {
        "L": [row("L", {"raw": "L. Amulette", "span": [10, 21]})],
        "M": [row("M", {"raw": "M. Amulette", "span": [30, 41]})],
    }}}
    entry = {"section_id": "po018-nubian-scripture", "named_locator": {
        "division_raw": "Amulettes", "codes": [{"raw": "L"}, {"raw": "M"}]}}
    result = locate(entry, mapping)
    assert len(result) == 1
    assert [x["raw"] for x in result[0]["evidence"]] == ["L. Amulette", "M. Amulette"]
    assert [x["document_code"] for x in result[0]["localizations"]] == ["L", "M"]
    assert result[0]["uncertainties"] == ["L", "M"]


def test_introduction_does_not_infer_from_nonadjacent_headers(tmp_path):
    files = {n: write_page(tmp_path, n, block(f"600 INTRODUCTION. [{n-10}]", "cabecalho"))
             for n in [13, 14, 15]}
    files[11] = write_page(tmp_path, 11, block("INTRODUCTION", "cabecalho"))
    assert "1" not in page_sequence(files)
