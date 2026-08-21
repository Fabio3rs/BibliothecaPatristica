from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tools.audit_page_groups import build_page_groups, build_segments, run


def write_page(corpus: Path, volume_id: str, page: int, text: str) -> None:
    text_dir = corpus / volume_id / "text"
    image_dir = corpus / volume_id / "images"
    text_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / f"{volume_id}-{page:03d}.txt").write_text(text, encoding="utf-8")
    (image_dir / f"{volume_id}-{page:03d}.png").write_bytes(
        f"image-{page}".encode()
    )


def test_later_title_claim_starts_new_inferred_segment(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    for page in range(1, 26):
        text = f"Texto corrente da pagina {page}."
        if page == 1:
            text += " PATROLOGIAE TOMUS XX."
        if page == 12:
            text += " PATROLOGIAE TOMUS XXI."
        write_page(corpus, "PL020", page, text)

    groups = build_page_groups(corpus / "PL020", group_size=10)
    segments = build_segments(groups)

    assert [group.inferred_tome for group in groups] == [20, 21, 21]
    assert [group.identity_status for group in groups] == [
        "explicit_match",
        "explicit_mismatch",
        "inferred_mismatch",
    ]
    assert [(segment.groups[0].pages[0].number, segment.groups[-1].pages[-1].number) for segment in segments] == [
        (1, 10),
        (11, 25),
    ]


def test_part_reference_without_tome_does_not_split_segment(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    for page in range(1, 21):
        text = "PATROLOGIAE TOMUS XX" if page == 1 else "Texto corrente"
        if page == 12:
            text += " PARS SECUNDA"
        write_page(corpus, "PL020", page, text)

    groups = build_page_groups(corpus / "PL020", group_size=10)
    segments = build_segments(groups)

    assert len(segments) == 1
    assert groups[-1].inferred_part is None


def test_series_reference_without_tome_does_not_change_identity(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    for page in range(1, 21):
        text = "PATROLOGIAE GRAECAE TOMUS XXI" if page == 1 else "Texto corrente"
        if page == 12:
            text += " Vide Patrologiae Latinae tom. CXLV"
        write_page(corpus, "PG021", page, text)

    groups = build_page_groups(corpus / "PG021", group_size=10)
    segments = build_segments(groups)

    assert len(segments) == 1
    assert groups[-1].declared_series == ("PL",)
    assert groups[-1].inferred_series == "PG"
    assert groups[-1].identity_status == "inferred_match"


def test_reports_exact_groups_across_different_volumes(tmp_path: Path) -> None:
    corpus = tmp_path / "teste"
    for volume_id in ("PL001", "PL099"):
        for page in range(1, 4):
            write_page(corpus, volume_id, page, f"Mesmo texto antigo pagina {page}")

    output_dir = tmp_path / "out"
    groups, _segments = run(
        argparse.Namespace(
            corpus=corpus,
            volumes=["PL001", "PL099"],
            group_size=3,
            output_dir=output_dir,
        )
    )

    assert len(groups) == 2
    with (output_dir / "exact_page_group_duplicates.csv").open(
        encoding="utf-8-sig"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["match_type"] == "facsimile+text"
    assert {rows[0]["volume_a"], rows[0]["volume_b"]} == {"PL001", "PL099"}
