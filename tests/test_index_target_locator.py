from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.index_target_locator import (
    normalize_for_search,
    parse_ocr_page_xml,
    resolve_index_targets,
)


def write_page(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_parse_ocr_page_xml_splits_zones() -> None:
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho" script="latino">1181 INDEX</bloco>
      <bloco tipo="texto_principal" script="latino">Dionysius laudatur, 543.</bloco>
      <bloco tipo="rodape" script="latino">scan footer 1</bloco>
      <bloco tipo="nota_marginal" script="latino">A</bloco>
    </pagina>
    """
    parsed = parse_ocr_page_xml(xml)
    assert "1181 INDEX" in parsed["header_text"]
    assert "Dionysius" in parsed["body_text"]
    assert "scan footer 1" in parsed["footer_text"]
    assert parsed["notes_text"] == "A"


def test_normalize_for_search_folds_ligatures_and_diacritics() -> None:
    assert normalize_for_search("GRÆCITATIS") == "graecitatis"
    assert normalize_for_search("Éphésiens") == "ephesiens"


def test_resolve_index_targets_uses_name_and_page_hints(tmp_path: Path) -> None:
    text_root = tmp_path / "PGX" / "text"
    text_root.mkdir(parents=True)

    write_page(
        text_root / "page-590.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">541 INDEX RERUM</bloco>
          <bloco tipo="texto_principal" script="latino">Aliud nomen.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-591.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">543 INDEX RERUM</bloco>
          <bloco tipo="texto_principal" script="latino">S. Dionysius de Hierotheo laudabile testimonium.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-592.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">544 INDEX RERUM</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius alibi memoratur.</bloco>
        </pagina>
        """,
    )

    request = {
        "volume_id": "PGX",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "lemma_raw": "Dionysius",
                "query_names": ["S. Dionysius", "Dionysius"],
                "page_hint_ints": [543],
                "context_raw": "S. Dionysius de Hierotheo laudabile testimonium, 543.",
            }
        ],
    }

    result = resolve_index_targets(request)
    entry = result["entries"][0]
    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"].endswith("page-591.txt")
    assert entry["candidates"][0]["inferred_printed_page"] == 543
    assert "estimator_pages" in entry["candidates"][0]
    assert "editorial_page_decision_source" in entry["best_candidate"]


def test_resolve_index_targets_infers_page_from_neighbors(tmp_path: Path) -> None:
    text_root = tmp_path / "PGY" / "text"
    text_root.mkdir(parents=True)

    write_page(
        text_root / "page-100.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">542 INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">Praeambulum.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-101.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">54B INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">S. Dionysius laudatur.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-102.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">544 INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">Aliud textum.</bloco>
        </pagina>
        """,
    )

    request = {
        "volume_id": "PGY",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["Dionysius"],
                "page_hint_ints": [543],
            }
        ],
    }
    result = resolve_index_targets(request)
    candidate = result["entries"][0]["candidates"][0]
    assert candidate["inferred_printed_page"] == 543
    assert any(ev["kind"] == "adjacent_page_consensus" for ev in candidate["evidence"])


def test_resolve_index_targets_tracks_parallel_editorial_numbering_sequences(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "POY" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-100.txt",
        """
        <pagina><bloco tipo="cabecalho">[104] LIBER JOB 664</bloco>
        <bloco tipo="texto_principal">Praeambulum.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "page-101.txt",
        """
        <pagina><bloco tipo="texto_principal">
        Dionysius Antiochenus testimonium.
        </bloco></pagina>
        """,
    )
    write_page(
        text_root / "page-102.txt",
        """
        <pagina><bloco tipo="cabecalho">[106] LIBER JOB 666</bloco>
        <bloco tipo="texto_principal">Finis.</bloco></pagina>
        """,
    )

    result = resolve_index_targets(
        {
            "volume_id": "POY",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "query_names": ["Dionysius Antiochenus"],
                    "page_hint_ints": [665],
                }
            ],
        }
    )

    candidate = result["entries"][0]["candidates"][0]
    assert candidate["file"].endswith("page-101.txt")
    assert 665 in candidate["inferred_page_candidates"]


def test_resolve_index_targets_uses_editorial_page_estimator_as_secondary_confirmation(tmp_path: Path) -> None:
    text_root = tmp_path / "PGE" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">121 PRÆFATIO GENERALIS. 122</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius laudatur hic.</bloco>
        </pagina>
        """,
    )
    request = {
        "volume_id": "PGE",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["Dionysius"],
                "page_hint_ints": [122],
            }
        ],
    }
    result = resolve_index_targets(request)
    candidate = result["entries"][0]["candidates"][0]
    assert 122 in candidate["estimator_pages"]
    assert any(ev["kind"] == "estimator_page_match" for ev in candidate["evidence"])
    assert candidate["editorial_page_decision_source"] == "estimator_confirmed"


def test_resolve_index_targets_marks_local_only_when_estimator_is_not_used(tmp_path: Path) -> None:
    text_root = tmp_path / "POZ" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">543 INDEX RERUM</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius laudatur hic.</bloco>
        </pagina>
        """,
    )
    request = {
        "volume_id": "POZ",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["Dionysius"],
                "page_hint_ints": [543],
            }
        ],
    }
    result = resolve_index_targets(request)
    candidate = result["entries"][0]["candidates"][0]
    assert candidate["editorial_page_decision_source"] == "local_only"


def test_resolve_index_targets_marks_ambiguous_when_probabilities_are_close(tmp_path: Path) -> None:
    text_root = tmp_path / "PLX" / "text"
    text_root.mkdir(parents=True)
    for idx, printed in ((1, 200), (2, 201)):
        write_page(
            text_root / f"page-{idx:03d}.txt",
            f"""
            <pagina estado="com_texto">
              <bloco tipo="cabecalho" script="latino">{printed} INDEX</bloco>
              <bloco tipo="texto_principal" script="latino">Udalricus memoratur hic.</bloco>
            </pagina>
            """,
        )
    request = {
        "volume_id": "PLX",
        "source_root": str(text_root),
        "entries": [{"entry_id": "e1", "query_names": ["Udalricus"]}],
    }
    result = resolve_index_targets(request)
    assert result["entries"][0]["status"] == "ambiguous"


def test_resolve_index_targets_unresolved_without_matches(tmp_path: Path) -> None:
    text_root = tmp_path / "POX" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="texto_principal" script="latino">Aliud omnino.</bloco>
        </pagina>
        """,
    )
    request = {
        "volume_id": "POX",
        "source_root": str(text_root),
        "entries": [{"entry_id": "e1", "query_names": ["Severus Antiochenus"]}],
    }
    result = resolve_index_targets(request)
    assert result["entries"][0]["status"] == "unresolved"
