from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_alphabetical_filtered_pages import build_fallback_filtered_pages
from scripts.build_alphabetical_filtered_pages import _looks_like_heading_candidate
from scripts.build_alphabetical_filtered_pages import resolve_filtered_pages


def _write_page(path: Path, header: str, body: str = "Aaron, 12.") -> None:
    path.write_text(
        (
            '<pagina estado="com_texto">'
            f'<bloco tipo="cabecalho">{header}</bloco>'
            f'<bloco tipo="texto_principal">{body}</bloco>'
            "</pagina>"
        ),
        encoding="utf-8",
    )


def test_po_alphabetical_profile_detects_generic_table_and_index_headings(tmp_path: Path) -> None:
    text_root = tmp_path / "PO001" / "text"
    text_root.mkdir(parents=True)
    table = text_root / "po-001.txt"
    index = text_root / "po-002.txt"
    _write_page(table, "TABLE DES AMULETTES")
    _write_page(index, "INDEX DES PASSAGES DE LA SAINTE ECRITURE")

    payload = build_fallback_filtered_pages("PO001", text_root, "PO", "alphabetical")

    markers = {item["marker"] for item in payload["candidate_sections"]}
    assert "TABLE" in markers
    assert "INDEX" in markers


def test_general_profile_owns_contents_and_alpha_marks_them_as_boundaries(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PL001" / "text"
    text_root.mkdir(parents=True)
    contents = text_root / "pl-001.txt"
    _write_page(contents, "CONSPECTUS TOMI PRIMI")

    alphabetical = build_fallback_filtered_pages(
        "PL001", text_root, "PL", "alphabetical"
    )
    general = build_fallback_filtered_pages("PL001", text_root, "PL", "general")

    assert alphabetical["candidate_sections"][0]["marker"] == "CONSPECTUS TOMI"
    assert (
        alphabetical["candidate_sections"][0]["role"]
        == "alphabetical_stop_boundary"
    )
    assert general["candidate_sections"][0]["marker"] == "CONSPECTUS TOMI"


def test_general_profile_matches_multiline_heading_with_cer(tmp_path: Path) -> None:
    text_root = tmp_path / "PG001" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "pg-001.txt"
    page.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">ELENCHUS</bloco>
          <bloco tipo="cabecalho">AUCT0RUM ET 0PERUM</bloco>
          <bloco tipo="texto_principal">QUI IN HOC TOMO CONTINENTUR.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )

    payload = build_fallback_filtered_pages("PG001", text_root, "PG", "general")

    assert payload["candidate_sections"][0]["marker"] == "AUCTORUM ET OPERUM"
    assert "ELENCHUS AUCT0RUM ET 0PERUM" in payload["candidate_sections"][0]["heading"]


def test_general_and_alphabetical_profiles_are_editorially_disjoint(tmp_path: Path) -> None:
    text_root = tmp_path / "PL001" / "text"
    text_root.mkdir(parents=True)
    _write_page(text_root / "pl-001.txt", "CONSPECTUS TOMI PRIMI")
    _write_page(text_root / "pl-002.txt", "INDEX RERUM ET VERBORUM")

    general = build_fallback_filtered_pages("PL001", text_root, "PL", "general")
    alphabetical = build_fallback_filtered_pages("PL001", text_root, "PL", "alphabetical")

    assert {item["marker"] for item in general["candidate_sections"]} == {"CONSPECTUS TOMI"}
    assert {
        item["marker"]
        for item in alphabetical["candidate_sections"]
        if item["role"] == "section_heading"
    } == {
        "INDEX RERUM ET VERBORUM"
    }
    assert any(
        item["marker"] == "CONSPECTUS TOMI"
        and item["role"] == "alphabetical_stop_boundary"
        for item in alphabetical["candidate_sections"]
    )
    assert general["head_files"]
    assert general["tail_files"] == []
    assert alphabetical["head_files"] == []
    assert alphabetical["tail_files"]


def test_generic_index_marker_does_not_match_vindex_inside_another_word() -> None:
    assert not _looks_like_heading_candidate(
        "TRADITIONIS VINDEX, NEC ERRORIS AB ULLO ARGUITUR",
        "INDEX",
        "PL",
    )
    assert _looks_like_heading_candidate(
        "S. BASILII EPISTOLARUM INDEX ALPHABETICUS",
        "INDEX ALPHABETICUS",
        "PG",
    )


def test_general_profile_does_not_reuse_untyped_legacy_alpha_candidates(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PL001" / "text"
    text_root.mkdir(parents=True)
    opening = text_root / "pl-001.txt"
    closing = text_root / "pl-002.txt"
    _write_page(opening, "CONSPECTUS TOMI PRIMI")
    _write_page(closing, "INDEX RERUM ET VERBORUM")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "candidate_files": [str(closing)],
                "candidate_sections": [
                    {
                        "file": str(closing),
                        "heading": "INDEX RERUM ET VERBORUM",
                        "marker": "INDEX RERUM ET VERBORUM",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_filtered_pages(
        volume_id="PL001",
        root=tmp_path,
        filtered_pages_json=legacy,
        filtered_pages_dir=None,
        profile="general",
    )

    assert payload["source"] == "fallback_internal"
    assert payload["ignored_legacy_external_file"] == str(legacy)
    assert {item["marker"] for item in payload["candidate_sections"]} == {"CONSPECTUS TOMI"}


def test_alphabetical_profile_rejects_explicit_general_external_candidates(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PL001" / "text"
    text_root.mkdir(parents=True)
    opening = text_root / "pl-001.txt"
    closing = text_root / "pl-002.txt"
    _write_page(opening, "CONSPECTUS TOMI PRIMI")
    _write_page(closing, "INDEX RERUM ET VERBORUM")
    external = tmp_path / "general.json"
    external.write_text(
        json.dumps(
            {
                "volume_id": "PL001",
                "profile": "general",
                "candidate_files": [str(opening)],
                "candidate_sections": [
                    {
                        "file": str(opening),
                        "heading": "CONSPECTUS TOMI PRIMI",
                        "marker": "CONSPECTUS TOMI",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_filtered_pages(
        volume_id="PL001",
        root=tmp_path,
        filtered_pages_json=external,
        filtered_pages_dir=None,
        profile="alphabetical",
    )

    assert payload["source"] == "fallback_internal"
    assert payload["ignored_legacy_external_file"] == str(external)
    assert {
        item["marker"]
        for item in payload["candidate_sections"]
        if item["role"] == "section_heading"
    } == {
        "INDEX RERUM ET VERBORUM"
    }
    assert any(
        item["marker"] == "CONSPECTUS TOMI"
        and item["role"] == "alphabetical_stop_boundary"
        for item in payload["candidate_sections"]
    )


def test_pg_pl_ordo_rerum_is_tagged_as_alphabetical_stop_boundary(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PL072" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "pl-576.txt"
    _write_page(page, "ORDO RERUM", "JOANNES PAPA III .... 1")

    payload = build_fallback_filtered_pages(
        "PL072", text_root, "PL", "alphabetical"
    )

    hit = next(
        item
        for item in payload["candidate_sections"]
        if item["marker"] == "ORDO RERUM"
    )
    assert hit["role"] == "alphabetical_stop_boundary"


def test_alphabetical_profile_detects_multiline_cer_works_boundary(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PL192" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "pl-001.txt"
    page.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">ELENCHUS.</bloco>
          <bloco tipo="cabecalho">AUCT0RUM ET 0PERUM</bloco>
          <bloco tipo="texto_principal">QUI IN HOC TOMO CONTINENTUR.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )

    payload = build_fallback_filtered_pages(
        "PL192", text_root, "PL", "alphabetical"
    )

    hit = next(
        item
        for item in payload["candidate_sections"]
        if item["marker"] == "AUCTORUM ET OPERUM"
    )
    assert hit["role"] == "alphabetical_stop_boundary"
    assert hit["line_end"] > hit["line"]


def test_alphabetical_profile_joins_hyphenated_heading_lines(tmp_path: Path) -> None:
    text_root = tmp_path / "PL001" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "pl-001.txt"
    page.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">INDEX ONO-</bloco>
          <bloco tipo="cabecalho">MASTICUS</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )

    payload = build_fallback_filtered_pages(
        "PL001", text_root, "PL", "alphabetical"
    )

    hit = next(
        item
        for item in payload["candidate_sections"]
        if item["marker"] == "INDEX ONOMASTICUS"
    )
    assert hit["role"] == "section_heading"
    assert hit["line_end"] > hit["line"]


def test_ordo_rerum_remains_a_general_pipeline_section(tmp_path: Path) -> None:
    text_root = tmp_path / "PL072" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "pl-576.txt"
    _write_page(page, "ORDO RERUM", "JOANNES PAPA III .... 1")

    payload = build_fallback_filtered_pages("PL072", text_root, "PL", "general")

    hit = next(
        item
        for item in payload["candidate_sections"]
        if item["marker"] == "ORDO RERUM"
    )
    assert hit["role"] == "section_heading"
