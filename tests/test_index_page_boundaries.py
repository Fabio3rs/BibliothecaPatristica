from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.index_entry_estimator import analyze_page_boundary


def _write_page(path: Path, body: str) -> None:
    path.write_text(
        f'<pagina estado="com_texto"><bloco tipo="texto_principal">{body}</bloco></pagina>',
        encoding="utf-8",
    )


def _write_page_with_footer(path: Path, body: str, footer: str) -> None:
    path.write_text(
        (
            '<pagina estado="com_texto">'
            f'<bloco tipo="texto_principal">{body}</bloco>'
            f'<bloco tipo="rodape">{footer}</bloco>'
            "</pagina>"
        ),
        encoding="utf-8",
    )


def test_boundary_detector_flags_word_split_across_physical_scans(tmp_path: Path) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page(left, "AARON. De interpreta-")
    _write_page(right, "tione nominis, 15")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] == "high"
    assert "left_trailing_linebreak_hyphen" in evidence["signals"]
    assert evidence["physical_left_file"] == str(left)
    assert "chunk owning physical_left_file" in evidence["ownership_rule"]


def test_boundary_detector_does_not_join_two_complete_entries(tmp_path: Path) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page(left, "AARON. De nomine, 15")
    _write_page(right, "ABEL. De fratre, 16")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] == "low"


def test_boundary_detector_ignores_scanner_boilerplate_and_recovers_continuation(
    tmp_path: Path,
) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page(left, "AARON. De nomine et interpreta- Digitized by Google")
    _write_page(right, "tione, 15")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] == "high"
    assert "Digitized by Google" not in evidence["left_last_body_line"]


def test_boundary_detector_flags_reference_list_continued_on_next_scan(
    tmp_path: Path,
) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page(left, "Zabdai 18₇ 42₃ 151₄")
    _write_page(right, "154₈-₁₀ 156₇-₉ 158₁₂")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] == "high"
    assert "right_starts_with_numeric_reference" in evidence["signals"]


def test_boundary_detector_does_not_join_new_numbered_entry_after_complete_reference(
    tmp_path: Path,
) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page(left, "AARON. De nomine, 15")
    _write_page(right, "1. ABEL. De fratre, 16")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] != "high"


def test_boundary_detector_treats_repeated_footer_catchword_as_layout_evidence(
    tmp_path: Path,
) -> None:
    left = tmp_path / "scan-001.txt"
    right = tmp_path / "scan-002.txt"
    _write_page_with_footer(left, "AARON. De nomine et", "interpretatione")
    _write_page(right, "interpretatione nominis, 15")

    evidence = analyze_page_boundary(left, right)

    assert evidence["likelihood"] == "high"
    assert evidence["left_footer_catchword"] == "interpretatione"
    assert "left_footer_catchword_repeats_right_start" in evidence["signals"]
