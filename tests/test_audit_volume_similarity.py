from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tools.audit_volume_similarity import (
    build_volume_sample,
    compare_samples,
    detect_declared_parts,
    detect_declared_series,
    detect_declared_tomes,
    run,
    select_text_pages,
)


def write_page(
    corpus: Path,
    volume_id: str,
    page: int,
    text: str,
    *,
    filename_prefix: str | None = None,
) -> Path:
    text_dir = corpus / volume_id / "text"
    image_dir = corpus / volume_id / "images"
    text_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    prefix = filename_prefix or volume_id
    text_path = text_dir / f"{prefix}-{page:03d}.txt"
    text_path.write_text(text, encoding="utf-8")
    (image_dir / f"{volume_id}-{page:03d}.png").write_bytes(b"facsimile")
    return text_path


def test_selects_first_unique_pages_and_prefers_canonical_filename(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    canonical = write_page(corpus, "PL020", 1, "canonical")
    write_page(corpus, "PL020", 1, "longer alternate text", filename_prefix="uuid")
    write_page(corpus, "PL020", 3, "third")
    write_page(corpus, "PL020", 2, "second")

    selected = select_text_pages(corpus / "PL020" / "text", "PL020", 2)

    assert [(number, path) for number, path, _alternatives in selected] == [
        (1, canonical),
        (2, corpus / "PL020" / "text" / "PL020-002.txt"),
    ]
    assert selected[0][2] == 1


def test_cleaner_ignores_xml_notes_and_detects_declared_tome(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    write_page(
        corpus,
        "PL020",
        1,
        """
        <pagina estado="com_texto">
          <bloco tipo="titulo" script="latino">
            PATROLOGIÆ TOMUS XX. QUINTI SÆCULI SCRIPTORUM ECCLESIASTICORUM.
          </bloco>
          <notas>Patrologia Latina, Vol. XXI.</notas>
        </pagina>
        """,
    )

    sample = build_volume_sample(corpus / "PL020", page_limit=10, shingle_width=3)

    assert sample.declared_tomes == (20,)
    assert sample.name_status == "match"
    assert "vol" not in sample.tokens
    assert detect_declared_tomes("PATROLOGIAE GRAECAE TOMUS CLXI") == (161,)
    assert detect_declared_tomes("AMPATROLOGIAE GRAECAE TOMUS VII") == (7,)
    assert detect_declared_series("AMPATROLOGIAE GRAECAE TOMUS VII") == ("PG",)
    assert detect_declared_parts("TOMUS VII. PARS SECUNDA") == ("secunda",)


def test_similarity_report_groups_duplicate_samples_and_writes_facsimiles(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "teste"
    shared = (
        "PATROLOGIAE TOMUS I. Auctoris antiqui opera omnia nunc primum "
        "collecta ordinata illustrata commentariis et variis lectionibus. "
    )
    for page in range(1, 4):
        write_page(corpus, "PL001", page, shared + f"caput pagina {page}")
        write_page(corpus, "PL099", page, shared + f"caput pagina {page}")
        write_page(
            corpus,
            "PG050",
            page,
            "Historia prorsus diversa de conciliis episcopis monasteriis "
            f"atque epistolis pagina {page}",
        )

    output_dir = tmp_path / "audit"
    args = argparse.Namespace(
        corpus=corpus,
        pages=3,
        shingle_width=3,
        threshold=0.25,
        group_threshold=0.40,
        max_df_ratio=0.20,
        output_dir=output_dir,
        no_diffs=False,
    )

    samples, pairs = run(args)

    duplicate = next(
        pair
        for pair in pairs
        if {pair.volume_a.volume_id, pair.volume_b.volume_id} == {"PL001", "PL099"}
    )
    assert duplicate.similarity > 0.9
    assert duplicate.group_id == "G001"
    assert duplicate.best_page_matches
    assert duplicate.diff_file.endswith("PL001__PL099.diff")
    assert (output_dir / "diffs" / "PL001__PL099.diff").is_file()

    with (output_dir / "volume_inventory.csv").open(encoding="utf-8-sig") as handle:
        inventory = {row["volume_id"]: row for row in csv.DictReader(handle)}
    assert inventory["PL001"]["name_status"] == "match"
    assert inventory["PL099"]["name_status"] == "review_tome_mismatch"
    assert "PL001-001.png" in inventory["PL001"]["facsimiles"]

    with (output_dir / "similarity_groups.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["group_id"] == "G001"
    assert rows[0]["similarity"] == f"{duplicate.similarity:.6f}"
    assert len(samples) == 3


def test_compare_samples_returns_no_pairs_for_one_volume(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    write_page(corpus, "PL001", 1, "PATROLOGIAE TOMUS I")
    sample = build_volume_sample(corpus / "PL001", page_limit=10, shingle_width=3)

    assert compare_samples([sample], max_df_ratio=0.2, report_threshold=0.0) == []
