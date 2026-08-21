from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.alphabetical_mechanical_analysis import (
    analyze_index_file,
    build_mechanical_analysis,
    detect_heading,
    detect_notations,
    detect_scripture_book_heading,
    extract_material_locators,
)


def test_aho_rules_distinguish_owned_headings_and_boundaries() -> None:
    assert detect_heading("INDEX RERUM ET VERBORUM.") == {
        "phrase": "index rerum et verborum",
        "role": "owned_heading",
        "section_kind": "analytic_subject",
    }
    assert detect_heading("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR") == {
        "phrase": "ordo rerum",
        "role": "stop_boundary",
        "section_kind": "ordo_rerum",
    }


def test_aho_notation_and_book_heading_are_conservative() -> None:
    assert detect_notations("Otto, ibid. 292 seqq. Vide Brigantini.") == [
        {"notation_key": "ibid", "matched_phrase": "ibid"},
        {"notation_key": "seqq", "matched_phrase": "seqq"},
        {"notation_key": "vide", "matched_phrase": "vide"},
    ]
    assert detect_scripture_book_heading("Isaïe") == {
        "book_raw": "Isaïe",
        "book_key_candidates": ["isaias"],
    }
    assert detect_scripture_book_heading("INDEX SCRIPTURÆ SACRÆ") is None


def test_material_locator_regex_separates_lists_ranges_and_page_lines() -> None:
    assert extract_material_locators("Stephanus IV 133.") == [
        {
            "kind": "editorial_page",
            "raw": "133",
            "page_ref_int": 133,
            "char_start": 13,
            "char_end": 16,
        }
    ]
    locators = extract_material_locators(
        "S. Gebelardus II 191, 283 seqq. passim usque 323, it. 377 seqq."
    )
    assert [item["page_ref_int"] for item in locators if item["kind"] == "editorial_page"] == [
        191,
        283,
        323,
        377,
    ]
    assert extract_material_locators("Agnus, 1121, 29 sqq.")[0] == {
        "kind": "editorial_page_line",
        "raw": "1121, 29 sqq.",
        "page_ref_int": 1121,
        "line_ref_raw": "29",
        "open_ended": True,
        "char_start": 7,
        "char_end": 20,
    }
    ranges = extract_material_locators(
        "xxii, 29-xxiii, 6* . . . . . . 286-287"
    )
    assert [(item["range_start_raw"], item["range_end_raw"]) for item in ranges] == [
        ("286", "287")
    ]


def test_pipe_table_extracts_each_material_column() -> None:
    locators = extract_material_locators(
        "VI, 2.....221 | V, 16.....221 | II, 8.....224"
    )
    assert [item["page_ref_int"] for item in locators] == [221, 221, 224]


def test_mechanical_analysis_normalizes_cer_locators_and_soft_wraps(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "page-001.txt"
    source_file.write_text(
        '<bloco tipo="cabecalho">INDEX ONO-</bloco>\n'
        '<bloco tipo="cabecalho">MASTICUS</bloco>\n'
        '<bloco tipo="texto_principal">Alexan-</bloco>\n'
        '<bloco tipo="texto_principal">der .... I2I-I23.</bloco>',
        encoding="utf-8",
    )

    analysis = analyze_index_file(source_file)

    heading = next(
        item for item in analysis["candidates"] if item["role"] == "owned_heading"
    )
    assert heading["heading"]["section_kind"] == "onomastic_mixed"
    assert heading["line_end"] > heading["line"]
    wrapped = next(
        item
        for item in analysis["candidates"]
        if item.get("wrapped_locator_candidate")
    )
    locator = wrapped["material_locators"][0]
    assert locator["kind"] == "editorial_range"
    assert locator["range_start_raw"] == "121"
    assert locator["range_end_raw"] == "123"
    assert locator["ocr_normalized"] is True


def test_mechanical_analysis_marks_works_inventory_as_boundary() -> None:
    assert detect_heading(
        "ELENCHUS. AUCTORUM ET OPERUM QUI IN HOC TOMO CONTINENTUR"
    ) == {
        "phrase": "auctorum et operum",
        "role": "stop_boundary",
        "section_kind": "editorial_closure",
    }


def test_real_pg_pl_po_pages_expose_expected_mechanical_grammar() -> None:
    root = ROOT
    pg = analyze_index_file(
        root
        / "teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-591.txt"
    )
    pl = analyze_index_file(
        root
        / "teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-802.txt"
    )
    po = analyze_index_file(
        root
        / "teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-483.txt"
    )

    assert any(item["role"] == "owned_heading" for item in pg["candidates"])
    assert sum(
        len(item["material_locators"]) for item in pg["candidates"]
    ) >= 150
    assert any(item["role"] == "heading_group" for item in pl["candidates"])
    assert any(item["role"] == "scripture_book_heading" for item in po["candidates"])
    assert sum(
        item["role"] == "scripture_row_candidate" for item in po["candidates"]
    ) >= 70


def test_mechanical_analysis_limits_work_to_filtered_candidates(tmp_path: Path) -> None:
    source_root = tmp_path / "text"
    source_root.mkdir()
    selected = source_root / "page-001.txt"
    ignored = source_root / "page-002.txt"
    selected.write_text(
        '<bloco tipo="cabecalho">INDEX RERUM</bloco>\n'
        '<bloco tipo="texto_principal">Aaron 12, 14.</bloco>',
        encoding="utf-8",
    )
    ignored.write_text("ORDO RERUM", encoding="utf-8")

    analysis = build_mechanical_analysis(
        volume_id="PL001",
        collection="PL",
        source_root=source_root,
        filtered_pages={"candidate_files": [str(selected)]},
    )

    assert analysis["engine"] == "pyahocorasick+regex"
    assert analysis["inspected_file_count"] == 1
    assert analysis["material_locator_count"] == 2
    assert analysis["section_leads"][0]["section_kind"] == "analytic_subject"


def test_mechanical_analysis_accepts_project_relative_candidate_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    selected = source_root / "page-001.txt"
    selected.write_text("INDEX ONOMASTICUS\nAaron 12", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    analysis = build_mechanical_analysis(
        volume_id="PL001",
        collection="PL",
        source_root=Path("PL001/text"),
        filtered_pages={"candidate_files": ["PL001/text/page-001.txt"]},
    )

    assert analysis["inspected_file_count"] == 1
    assert analysis["files"][0]["file"] == str(selected.resolve())
