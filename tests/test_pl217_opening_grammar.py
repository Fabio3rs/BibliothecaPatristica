from pathlib import Path

import pytest

from tools.indexing.pl217_opening_grammar import analyze_opening, analyze_title_page, parse_index_page

LEGEND = ('INDEX EPISTOLARUM\nSECUNDUM LITTERAM INITIALEM ORDINATUS.\n'
          'PRIOR NUMERUS LIBRUM, POSTERIOR EPISTOLAM INDICAT.')


def block(text, kind="texto_principal"):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def test_legend_requires_explicit_semantics_and_keeps_original_spans():
    raw = block(LEGEND, "cabecalho")
    opening = analyze_opening(raw, source="fixture")
    assert opening["matched"] and opening["numbers_are_pages"] is False
    assert opening["ordering"] == "initial_letter"
    for item in opening["evidence"]:
        assert raw[item["char_start"]:item["char_end"]] == item["raw"]
    assert not analyze_opening(block("INDEX EPISTOLARUM"))["matched"]
    assert not analyze_opening(block("PRIOR NUMERUS PAGINAM INDICAT."))["matched"]
    with pytest.raises(ValueError):
        parse_index_page(block("Alienum, XV, 3."), "fixture", 1)


def test_register_supplement_imperial_appendix_are_separate():
    raw = block(LEGEND, "cabecalho") + block(
        "Gravem dilecti. XV, 104.\nContra gravamina. Suppl. 121.\n"
        "Contumaciam Joannis. Registr. ad neg. imp. 78.\nDilecta in Christo. XIV, Append. 1.")
    result = parse_index_page(raw, "fixture", 1)
    assert [(e["editorial_locators"][0]["series"],
             e["editorial_locators"][0]["book"], e["editorial_locators"][0]["epistle"])
            for e in result["entries"]] == [
                ("register", 15, 104), ("supplement", None, 121),
                ("imperial_register", None, 78), ("appendix", 14, 1)]
    for entry in result["entries"]:
        assert raw[entry["char_start"]:entry["char_end"]] == entry["entry_raw"]
        for loc in entry["editorial_locators"]:
            assert raw[loc["char_start"]:loc["char_end"]] == loc["raw"]
        assert "target_file" not in entry


def test_running_header_does_not_close_mixed_page_but_internal_title_does():
    opening = analyze_opening(block(LEGEND, "cabecalho"), "opening")
    raw = (block("1189 ORDO RERUM 1190", "cabecalho") +
           block("Vox clamantis. Suppl. 88.\n6.\nWillehadi præpositos. Suppl. 233.") +
           block("ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR.", "cabecalho") +
           block("Catalogus. I, 120."))
    result = parse_index_page(raw, "mixed", 600, opening)
    assert len(result["entries"]) == 2
    assert result["closing"]
    assert any(r["raw"] == "6." for r in result["residuals"])


def test_actual_opening_and_mixed_page():
    root = Path(__file__).resolve().parents[1] / "teste/PL217/text"
    opening_files = list(root.glob("*-585.txt"))
    mixed_files = list(root.glob("*-600.txt"))
    if not opening_files or not mixed_files:
        pytest.skip("Corpus fixture not installed")
    opening = analyze_opening(opening_files[0].read_text(), str(opening_files[0]))
    assert opening["matched"]
    raw = mixed_files[0].read_text()
    result = parse_index_page(raw, str(mixed_files[0]), 600, opening)
    assert result["closing"] and len(result["entries"]) > 100
    assert all(e["char_end"] <= result["closing"]["char_start"] for e in result["entries"])


def test_title_tomes_are_distinct_from_index_books():
    raw = block('TOMIS. QUATUOR. DISTRIBVTA,\nQUORUM PRIORES TRES REGESTORUM '
                'BALUZIANAM RECENSIONEM COMPLECTUNTUR,\nQUARTO VOLUMINI INSUNT '
                'EPISTOLÆ EXTRA REGESTUM VAGANTES,') + block('TOMUS QUARTUS.')
    result = analyze_title_page(raw, 'title')
    assert result['declared_tomes'] == result['current_tome'] == 4
    assert result['register_tomes'] == [1, 2, 3]
    assert result['fourth_tome_extra_register_letters']
    assert result['physical_volume_mapping'] is None
    assert not analyze_opening(raw)['matched']
    for e in result['evidence']:
        assert raw[e['char_start']:e['char_end']] == e['raw']
