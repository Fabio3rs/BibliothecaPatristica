from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.editorial_page_estimator import (
    _connect_db,
    estimate_editorial_pages,
    list_page_overrides,
    set_page_override,
)


def write_page(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_estimator_cache_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    conn = _connect_db(tmp_path / "editorial_pages.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    finally:
        conn.close()


def test_estimator_extracts_header_pair_as_strong_guess(tmp_path: Path) -> None:
    text_root = tmp_path / "PGX" / "text"
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">121 PRÆFATIO GENERALIS. 122</bloco>
          <bloco tipo="texto_principal" script="latino">Aliud corpus textus.</bloco>
        </pagina>
        """,
    )

    result = estimate_editorial_pages(source_root=text_root, collection="PG")
    item = result["files"][0]
    assert item["best_left_page"] == 121
    assert item["best_right_page"] == 122
    assert item["confidence_label"] == "high"
    assert any(ev["kind"] == "header_pair" for ev in item["evidence"])


def test_estimator_degrades_nonconsecutive_header_pair(tmp_path: Path) -> None:
    text_root = tmp_path / "PGX" / "text"
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">f 89 HOMILIA. 890</bloco>
          <bloco tipo="texto_principal" script="latino">Aliud corpus textus.</bloco>
        </pagina>
        """,
    )

    result = estimate_editorial_pages(source_root=text_root, collection="PG")
    item = result["files"][0]

    assert item["confidence_label"] == "low"
    assert item["confidence"] <= 0.35
    assert "nonconsecutive_header_pair_suspected_ocr" in item["warnings"]
    assert any(
        ev["kind"] == "header_pair_nonconsecutive" for ev in item["evidence"]
    )


def test_estimator_ignores_google_footer_noise(tmp_path: Path) -> None:
    text_root = tmp_path / "PLY" / "text"
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="rodape" script="latino">Digitized by Google</bloco>
          <bloco tipo="texto_principal" script="latino">27 Luc. XII, 6-9. 28 Isa. LIII, 4.</bloco>
        </pagina>
        """,
    )

    result = estimate_editorial_pages(source_root=text_root, collection="PL")
    item = result["files"][0]
    assert item["best_guess"] is None or item["confidence_label"] == "low"
    assert "no_editorial_page_signal" in item["warnings"] or "body_numbers_may_be_citations" in item["warnings"]


def test_estimator_uses_neighbors_to_fill_missing_header(tmp_path: Path) -> None:
    text_root = tmp_path / "PGN" / "text"
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">65 INDEX. 66</bloco>
          <bloco tipo="texto_principal" script="latino">Initium.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-002.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="texto_principal" script="latino">Corpus sine capite manifesto.</bloco>
        </pagina>
        """,
    )
    write_page(
        text_root / "page-003.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">69 INDEX. 70</bloco>
          <bloco tipo="texto_principal" script="latino">Finis.</bloco>
        </pagina>
        """,
    )

    result = estimate_editorial_pages(source_root=text_root, collection="PG", window=2)
    item = result["files"][1]
    assert item["best_left_page"] == 67
    assert item["best_right_page"] == 68
    assert any(ev["kind"] == "neighbor_fit" for ev in item["evidence"])


def test_estimator_overrides_consecutive_cer_pair_with_bracketed_sequence(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PGQ" / "text"
    for file_number, left in enumerate((101, 103, 405, 107, 109), start=1):
        write_page(
            text_root / f"page-{file_number:03d}.txt",
            (
                "<pagina estado=\"com_texto\">"
                f"<bloco tipo=\"cabecalho\">{left} INDEX {left + 1}</bloco>"
                "<bloco tipo=\"texto_principal\">Corpus.</bloco>"
                "</pagina>"
            ),
        )

    result = estimate_editorial_pages(
        volume_id="PGQ",
        source_root=text_root,
        collection="PG",
        db_path=tmp_path / "editorial_pages.db",
        use_cache=False,
    )
    middle = result["files"][2]

    assert middle["best_guess"] == [105, 106]
    assert "header_pair_overridden_by_sequence" in middle["warnings"]
    assert any(ev["kind"] == "sequence_consensus" for ev in middle["evidence"])


def test_estimator_reads_split_header_blocks_and_cer_digits(tmp_path: Path) -> None:
    text_root = tmp_path / "PGC" / "text"
    write_page(
        text_root / "page-001.txt",
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho">12I</bloco>
          <bloco tipo="cabecalho">INDEX RERUM</bloco>
          <bloco tipo="cabecalho">122</bloco>
          <bloco tipo="texto_principal">AARON .... 10</bloco>
        </pagina>
        """,
    )

    result = estimate_editorial_pages(source_root=text_root, collection="PG")
    item = result["files"][0]

    assert item["best_left_page"] == 121
    assert item["best_right_page"] == 122


def test_estimator_reads_single_header_page_with_cer_digits(tmp_path: Path) -> None:
    text_root = tmp_path / "PGC" / "text"
    write_page(
        text_root / "page-001.txt",
        '<pagina><bloco tipo="cabecalho">I2O INDEX</bloco></pagina>',
    )

    result = estimate_editorial_pages(
        source_root=text_root,
        collection="PG",
        db_path=tmp_path / "editorial_pages.db",
        use_cache=False,
    )

    assert result["files"][0]["best_single_page"] == 120


def test_estimator_supports_single_page_ocr_generators_with_neighbor_window(
    tmp_path: Path,
) -> None:
    text_root = tmp_path / "PGS" / "text"
    write_page(
        text_root / "page-001.txt",
        '<pagina><bloco tipo="cabecalho">101 INDEX</bloco></pagina>',
    )
    write_page(
        text_root / "page-002.txt",
        '<pagina><bloco tipo="texto_principal">header missing</bloco></pagina>',
    )
    write_page(
        text_root / "page-003.txt",
        '<pagina><bloco tipo="cabecalho">103 INDEX</bloco></pagina>',
    )

    result = estimate_editorial_pages(
        source_root=text_root,
        collection="PG",
        window=4,
    )
    middle = result["files"][1]

    assert middle["best_single_page"] == 102
    assert any(ev["kind"] == "neighbor_single_fit" for ev in middle["evidence"])


def test_estimator_rejects_po_collection() -> None:
    with pytest.raises(ValueError, match="unsupported collection"):
        estimate_editorial_pages(volume_id="PO025", collection="PO", files=[])


def test_estimator_pg035_regression_sample() -> None:
    source_root = Path("/homessddata/Projects/pdfocr/teste/PG035/text")
    files = [
        source_root / "c04a8d6b-e7d3-4d60-8102-632b78fea2d2-065.txt",
        source_root / "c04a8d6b-e7d3-4d60-8102-632b78fea2d2-066.txt",
    ]
    result = estimate_editorial_pages(volume_id="PG035", files=files, collection="PG")
    assert result["files"][0]["best_left_page"] == 121
    assert result["files"][0]["best_right_page"] == 122
    assert result["files"][1]["best_left_page"] == 123
    assert result["files"][1]["best_right_page"] == 124


def test_estimator_applies_sqlite_override(tmp_path: Path) -> None:
    text_root = tmp_path / "PGO" / "text"
    db_path = tmp_path / "editorial_pages.db"
    page_path = text_root / "page-001.txt"
    write_page(
        page_path,
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">121 INDEX. 122</bloco>
          <bloco tipo="texto_principal" script="latino">Aliud corpus textus.</bloco>
        </pagina>
        """,
    )
    set_page_override(
        db_path=db_path,
        file_path=page_path,
        pages=[700, 701],
        volume_id="PGO",
        note="manual fix",
    )

    result = estimate_editorial_pages(
        volume_id="PGO",
        source_root=text_root,
        collection="PG",
        db_path=db_path,
    )
    item = result["files"][0]
    assert item["best_left_page"] == 700
    assert item["best_right_page"] == 701
    assert "override_applied" in item["warnings"]
    assert result["summary"]["override_applied_files"] == 1


def test_estimator_lists_sqlite_overrides(tmp_path: Path) -> None:
    db_path = tmp_path / "editorial_pages.db"
    file_path = tmp_path / "PGX" / "text" / "page-001.txt"
    set_page_override(
        db_path=db_path,
        file_path=file_path,
        pages=[101, 102],
        volume_id="PGX",
        note="qa",
    )
    payload = list_page_overrides(db_path=db_path, volume_id="PGX")
    assert payload["overrides"][0]["pages"] == [101, 102]
