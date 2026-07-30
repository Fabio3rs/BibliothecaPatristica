from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.index_entry_estimator import (
    combine_entry_estimates,
    estimate_page_entries,
)


def test_estimator_reports_range_without_claiming_final_segmentation(tmp_path: Path) -> None:
    page = tmp_path / "scan-001.txt"
    page.write_text(
        """<pagina estado="com_texto">
<bloco tipo="cabecalho">915 INDEX RERUM 916</bloco>
<bloco tipo="texto_principal">AARON, de nomine .... 12
— subtopic continued, 13-14
wrapped OCR continuation without terminal reference
B
ABEL. Vide CAIN.</bloco>
</pagina>""",
        encoding="utf-8",
    )

    estimate = estimate_page_entries(page)

    assert estimate["line_count"] == 5
    assert estimate["estimated_entry_count"]["lower_bound"] >= 1
    assert estimate["estimated_entry_count"]["likely"] >= 3
    assert estimate["estimated_entry_count"]["upper_bound"] >= estimate["estimated_entry_count"]["likely"]
    assert "does not segment entries" in estimate["method"]


def test_combined_estimate_warns_about_overlapping_chunks() -> None:
    page_estimate = {
        "estimated_entry_count": {"lower_bound": 2, "likely": 3, "upper_bound": 4},
        "strong_entry_line_count": 2,
        "probable_entry_line_count": 1,
    }

    combined = combine_entry_estimates([page_estimate, page_estimate])

    assert combined["estimated_entry_count"] == {
        "lower_bound": 4,
        "likely": 6,
        "upper_bound": 8,
    }
    assert "Overlapping chunks" in combined["method"]
