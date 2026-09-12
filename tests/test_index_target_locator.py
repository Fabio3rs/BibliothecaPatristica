from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.index_target_locator import (
    _contains_normalized_phrase,
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
    assert normalize_for_search("GRÆCITATIS") == normalize_for_search("GRAECITATIS")
    assert normalize_for_search("PROŒMIA") == normalize_for_search("PROOEMIA")
    assert normalize_for_search("Éphésiens") == "ephesiens"


def test_short_name_match_requires_complete_normalized_tokens() -> None:
    body = normalize_for_search(
        "The conclusion is manifest and never a matter of doubt."
    )

    assert not _contains_normalized_phrase(body, normalize_for_search("Mani"))
    assert not _contains_normalized_phrase(
        normalize_for_search("Leontius episcopus"),
        normalize_for_search("Leo"),
    )
    assert _contains_normalized_phrase(
        normalize_for_search("The doctrine attributed to Mani is rejected."),
        normalize_for_search("Mani"),
    )


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
    image_root = tmp_path / "PGE" / "images"
    image_root.mkdir()
    paired_image = image_root / "PGE-001.png"
    paired_image.write_bytes(b"")
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
    assert candidate["image_file"] == str(paired_image.resolve())
    assert result["entries"][0]["best_candidate"]["image_file"] == str(
        paired_image.resolve()
    )
    assert 122 in candidate["estimator_pages"]
    assert any(ev["kind"] == "estimator_page_match" for ev in candidate["evidence"])
    assert candidate["editorial_page_decision_source"] == "estimator_confirmed"


def test_resolve_index_targets_uses_cer_tolerant_single_editorial_page(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PGC" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">I2O INDEX</bloco>
          <bloco tipo="texto_principal">Dionysius laudatur hic.</bloco>
        </pagina>
        """,
    )

    result = resolve_index_targets(
        {
            "volume_id": "PGC",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "query_names": ["Dionysius"],
                    "page_hint_ints": [120],
                }
            ],
        }
    )

    candidate = result["entries"][0]["candidates"][0]
    assert candidate["file"].endswith("page-001.txt")
    assert 120 in candidate["estimator_pages"]
    assert any(
        evidence["kind"] == "estimator_page_match"
        for evidence in candidate["evidence"]
    )


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


def test_query_match_mode_best_does_not_add_equivalent_title_aliases(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PGA" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="texto_principal">EPISTOLAE DUAE AD VIRGINES</bloco>
        </pagina>
        """,
    )
    base = {
        "volume_id": "PGA",
        "source_root": str(text_root),
    }
    single = resolve_index_targets(
        {
            **base,
            "entries": [
                {
                    "entry_id": "single",
                    "query_names": ["Epistolae duae ad virgines"],
                    "query_match_mode": "best",
                }
            ],
        }
    )["entries"][0]["candidates"][0]
    aliases = resolve_index_targets(
        {
            **base,
            "entries": [
                {
                    "entry_id": "aliases",
                    "query_names": [
                        "Epistolae duae ad virgines",
                        "Epistolæ duæ ad virgines",
                    ],
                    "query_match_mode": "best",
                }
            ],
        }
    )["entries"][0]["candidates"][0]

    assert aliases["score"] == single["score"]


def test_page_less_work_locator_excludes_index_and_crosses_three_hints(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PGW" / "text"
    text_root.mkdir(parents=True)
    target = text_root / "work-001.txt"
    write_page(
        target,
        """
        <pagina><bloco tipo="cabecalho">ONOMASTICUM DIONYSIANUM</bloco>
        <bloco tipo="texto_principal">Myst. Theol. cap. 2. De caligine divina.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "work-002.txt",
        """
        <pagina><bloco tipo="cabecalho">ONOMASTICUM DIONYSIANUM</bloco>
        <bloco tipo="texto_principal">Aliud capitulum.</bloco></pagina>
        """,
    )
    index_file = text_root / "index-003.txt"
    write_page(
        index_file,
        """
        <pagina><bloco tipo="texto_principal">
        Ablespia, invisibilitas. Myst. Theol. cap. 2.
        </bloco></pagina>
        """,
    )

    entry = resolve_index_targets(
        {
            "volume_id": "PGW",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "section_kind": "foreign_terms",
                    "section_heading": "ONOMASTICUM DIONYSIANUM",
                    "section_file_start": str(index_file),
                    "section_file_end": str(index_file),
                    "query_names": ["Ablespia"],
                    "ref_kind": "target_locator",
                    "ref_raw": "Myst. Theol. cap. 2",
                }
            ],
        }
    )["entries"][0]

    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"] == str(target)
    assert all(
        candidate["file"] != str(index_file)
        for candidate in entry["candidates"]
    )
    kinds = {item["kind"] for item in entry["candidates"][0]["evidence"]}
    assert "body_work_locator_match" in kinds
    assert "work_locator_number_cooccurrence" in kinds
    assert "section_heading_family_match" in kinds


def test_resolve_index_targets_scores_levenshtein_phrase_match(tmp_path: Path) -> None:
    text_root = tmp_path / "POF" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">349 OPUS 350</bloco>
          <bloco tipo="texto_principal">EPIST0LAE DUAE AD VIRGINES</bloco>
        </pagina>
        """,
    )

    result = resolve_index_targets(
        {
            "volume_id": "POF",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "query_names": ["EPISTOLAE DUAE AD VIRGINES"],
                    "page_hint_ints": [349],
                }
            ],
        }
    )

    entry = result["entries"][0]
    assert entry["status"] == "resolved"
    assert any(
        evidence["kind"] == "body_name_fuzzy"
        and "levenshtein_similarity=" in evidence["raw"]
        for evidence in entry["candidates"][0]["evidence"]
    )


def test_resolve_onomastic_internal_locator_uses_unique_body_cooccurrence(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PLI" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">151 CHRONICON 152</bloco>
          <bloco tipo="texto_principal">Praeambulum.</bloco>
        </pagina>
        """,
    )
    target = text_root / "page-002.txt"
    write_page(
        target,
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">153 CHRONICON 154</bloco>
          <bloco tipo="texto_principal">
          Gregorius secundus sedit annos XV.
          114 Egbertus vir sanctus memoratur.
          </bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-003.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">155 CHRONICON 156</bloco>
          <bloco tipo="texto_principal">Finis.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-004.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">113 ALIUD 114</bloco>
          <bloco tipo="texto_principal">Nomen non adest.</bloco>
        </pagina>
        """,
    )
    index_file = text_root / "page-005.txt"
    write_page(
        index_file,
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">INDEX ONOMASTICUS</bloco>
          <bloco tipo="texto_principal">Gregorius II 114.</bloco>
        </pagina>
        """,
    )

    result = resolve_index_targets(
        {
            "volume_id": "PLI",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "section_kind": "onomastic_person",
                    "section_file_start": str(index_file),
                    "query_names": [
                        "Gregorius II",
                        "Gregorius",
                        "Gregorius secundus",
                    ],
                    "page_hint_ints": [114],
                    "context_raw": "Gregorius II 114.",
                }
            ],
        }
    )

    entry = result["entries"][0]
    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"] == str(target)
    evidence_kinds = {
        evidence["kind"] for evidence in entry["candidates"][0]["evidence"]
    }
    assert "body_locator_match" in evidence_kinds
    assert "body_locator_name_unique" in evidence_kinds
    assert "internal_locator_vs_editorial_sequence" in evidence_kinds


def test_resolve_onomastic_name_cohort_without_reliable_page_number(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PLC" / "text"
    text_root.mkdir(parents=True)
    target = text_root / "work-a-001.txt"
    write_page(
        target,
        """
        <pagina><bloco tipo="cabecalho">501 CHRONICON 502</bloco>
        <bloco tipo="texto_principal">Gregorius secundus pontifex.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "work-a-002.txt",
        '<pagina><bloco tipo="texto_principal">Zacharias pontifex.</bloco></pagina>',
    )
    write_page(
        text_root / "work-a-003.txt",
        '<pagina><bloco tipo="texto_principal">Stephanus pontifex.</bloco></pagina>',
    )
    write_page(
        text_root / "work-b-001.txt",
        '<pagina><bloco tipo="texto_principal">Gregorius secundus alibi.</bloco></pagina>',
    )
    index_file = text_root / "index-010.txt"
    write_page(
        index_file,
        '<pagina><bloco tipo="texto_principal">Gregorius II 9S9.</bloco></pagina>',
    )

    entry = resolve_index_targets(
        {
            "volume_id": "PLC",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "section_kind": "onomastic_person",
                    "section_file_start": str(index_file),
                    "query_names": ["Gregorius", "Gregorius secundus"],
                    "neighbor_query_groups": [
                        ["Zacharias"],
                        ["Stephanus"],
                    ],
                    "page_hint_ints": [999],
                }
            ],
        }
    )["entries"][0]

    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"] == str(target)
    assert any(
        evidence["kind"] == "onomastic_neighbor_cohort_unique"
        for evidence in entry["candidates"][0]["evidence"]
    )


def test_resolve_repeated_name_uses_inflected_form_and_local_locator(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PLR" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "work-a-001.txt",
        """
        <pagina><bloco tipo="cabecalho">465 CHRONICON 466</bloco>
        <bloco tipo="texto_principal">129 Leo III papa sedit.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "work-a-002.txt",
        """
        <pagina><bloco tipo="cabecalho">467 CHRONICON 468</bloco>
        <bloco tipo="texto_principal">130 Res prior. 131 Leo papa restituitur.</bloco></pagina>
        """,
    )
    target = text_root / "work-a-003.txt"
    write_page(
        target,
        """
        <pagina><bloco tipo="cabecalho">469 CHRONICON 470</bloco>
        <bloco tipo="texto_principal">Leoni papae imperator mandavit. 132 Res altera.</bloco></pagina>
        """,
    )
    index_file = text_root / "index-010.txt"
    write_page(
        index_file,
        '<pagina><bloco tipo="texto_principal">Leo III 129, 132.</bloco></pagina>',
    )

    entry = resolve_index_targets(
        {
            "volume_id": "PLR",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "leo_132",
                    "section_kind": "onomastic_person",
                    "section_file_start": str(index_file),
                    "query_names": ["Leo III", "Leo", "Leoni", "Leonis"],
                    "page_hint_ints": [132],
                }
            ],
        }
    )["entries"][0]

    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"] == str(target)
    evidence_kinds = {
        evidence["kind"] for evidence in entry["candidates"][0]["evidence"]
    }
    assert "body_locator_name_unique" in evidence_kinds
    assert "internal_locator_vs_editorial_sequence" in evidence_kinds


def test_sibling_locators_prefer_exact_family_over_numeric_cer_family(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PLS" / "text"
    text_root.mkdir(parents=True)
    target = text_root / "work-a-001.txt"
    write_page(
        target,
        """
        <pagina><bloco tipo="cabecalho">465 CHRONICON 466</bloco>
        <bloco tipo="texto_principal">129 Leo III papa sedit.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "work-a-002.txt",
        """
        <pagina><bloco tipo="cabecalho">467 CHRONICON 468</bloco>
        <bloco tipo="texto_principal">Leoni papae mandavit. 132 Res altera.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "work-b-001.txt",
        """
        <pagina><bloco tipo="cabecalho">601 CHRONICON 602</bloco>
        <bloco tipo="texto_principal">129 Leo III in alia chronica.</bloco></pagina>
        """,
    )
    write_page(
        text_root / "work-b-002.txt",
        """
        <pagina><bloco tipo="cabecalho">603 CHRONICON 604</bloco>
        <bloco tipo="texto_principal">Leoni nomen; 130 locus similis.</bloco></pagina>
        """,
    )
    index_file = text_root / "index-010.txt"
    write_page(
        index_file,
        '<pagina><bloco tipo="texto_principal">Leo III 129, 132.</bloco></pagina>',
    )

    entry = resolve_index_targets(
        {
            "volume_id": "PLS",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "leo_129",
                    "section_kind": "onomastic_person",
                    "section_heading": "INDEX ONOMASTICUS IN CHRONICON",
                    "section_file_start": str(index_file),
                    "query_names": ["Leo III", "Leoni", "Leonis"],
                    "page_hint_ints": [129],
                    "sibling_page_hint_ints": [129, 132],
                }
            ],
        }
    )["entries"][0]

    assert entry["status"] == "resolved"
    assert entry["best_candidate"]["file"] == str(target)
    evidence_kinds = {
        evidence["kind"] for evidence in entry["candidates"][0]["evidence"]
    }
    assert "onomastic_sibling_hint_family" in evidence_kinds
    assert "body_locator_name_unique" in evidence_kinds


def test_resolve_index_targets_detects_gapped_header_sequence_across_xml_layouts(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "POH" / "text"
    text_root.mkdir(parents=True)
    matching_files = {1, 2, 5, 8}
    for file_number in range(1, 9):
        if file_number == 1:
            header = """
              <bloco tipo="cabecalho">101</bloco>
              <bloco tipo="cabecalho">DE MYSTERIIS</bloco>
              <bloco tipo="cabecalho">102</bloco>
            """
        elif file_number in matching_files:
            title = "DE MYSTERlIS" if file_number == 2 else "DE MYSTERIIS"
            header = (
                f'<bloco tipo="cabecalho">{100 + file_number * 2 - 1} '
                f"{title} {100 + file_number * 2}</bloco>"
            )
        else:
            header = f'<bloco tipo="cabecalho">{file_number} ALIUD</bloco>'
        body = "DE MYSTERIIS initium." if file_number == 1 else "Corpus."
        write_page(
            text_root / f"page-{file_number:03d}.txt",
            f"""
            <pagina estado="com_texto">
              {header}
              <bloco tipo="texto_principal">{body}</bloco>
            </pagina>
            """,
        )

    result = resolve_index_targets(
        {
            "volume_id": "POH",
            "source_root": str(text_root),
            "entries": [
                {
                    "entry_id": "e1",
                    "query_names": ["DE MYSTERIIS"],
                }
            ],
        }
    )

    candidate = result["entries"][0]["candidates"][0]
    sequence = candidate["header_sequence"]
    assert candidate["file"].endswith("page-001.txt")
    assert sequence["start_file_seq"] == 1
    assert sequence["end_file_seq"] == 8
    assert sequence["match_count"] == 4
    assert sequence["max_missing_files"] == 3
    assert any(
        evidence["kind"] == "header_sequence_match"
        for evidence in candidate["evidence"]
    )


def test_resolve_index_targets_multiprocess_matches_serial_output(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "POM" / "text"
    text_root.mkdir(parents=True)
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">101 DE MYSTERIIS 102</bloco>
          <bloco tipo="texto_principal">DE MYSTERlIS initium.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-002.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">103 EPISTOLAE 104</bloco>
          <bloco tipo="texto_principal">EPIST0LAE DUAE AD VIRGINES</bloco>
        </pagina>
        """,
    )
    entries = [
        {
            "entry_id": "e1",
            "query_names": ["DE MYSTERIIS"],
            "page_hint_ints": [101],
        },
        {
            "entry_id": "e2",
            "query_names": ["EPISTOLAE DUAE AD VIRGINES"],
            "page_hint_ints": [103],
        },
    ]
    serial = resolve_index_targets(
        {
            "volume_id": "POM",
            "source_root": str(text_root),
            "options": {"workers": 1},
            "entries": entries,
        }
    )
    parallel = resolve_index_targets(
        {
            "volume_id": "POM",
            "source_root": str(text_root),
            "options": {"workers": 17},
            "entries": entries,
        }
    )

    assert parallel["entries"] == serial["entries"]
    assert parallel["options_used"]["workers"] == 2
    assert parallel["options_used"]["executor"] == "process"
    assert parallel["options_used"]["fuzzy_backend"] == "rapidfuzz"
