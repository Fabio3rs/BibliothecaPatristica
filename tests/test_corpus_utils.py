from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.corpus_utils import (
    discover_preferred_pages,
    discover_unique_pages,
    resolve_page_file,
)


def test_resolver_treats_padding_variants_as_the_same_page(tmp_path: Path) -> None:
    text_dir = tmp_path / "PG013" / "text"
    text_dir.mkdir(parents=True)
    legacy = text_dir / "uuid-0001.txt"
    legacy.write_text("antigo", encoding="utf-8")

    chosen, ambiguous = resolve_page_file(
        text_dir,
        volume_id="PG013",
        page_num=1,
        suffixes=(".txt",),
    )

    assert chosen == legacy
    assert ambiguous is False


def test_discovery_prefers_canonical_and_deduplicates_logical_page(
    tmp_path: Path,
) -> None:
    text_dir = tmp_path / "PG013" / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "uuid-0001.txt").write_text("antigo", encoding="utf-8")
    canonical = text_dir / "PG013-001.txt"
    canonical.write_text("novo", encoding="utf-8")
    page_two = text_dir / "uuid-0002.txt"
    page_two.write_text("dois", encoding="utf-8")

    assert discover_unique_pages(text_dir, volume_id="PG013") == [canonical, page_two]


def test_discovery_rejects_two_noncanonical_sources(tmp_path: Path) -> None:
    text_dir = tmp_path / "PG013" / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "first-0001.txt").write_text("um", encoding="utf-8")
    (text_dir / "second-001.txt").write_text("outro", encoding="utf-8")

    with pytest.raises(RuntimeError, match="fontes ambíguas"):
        discover_unique_pages(text_dir, volume_id="PG013")


def test_preferred_discovery_only_collapses_known_uuid_variant(tmp_path: Path) -> None:
    text_dir = tmp_path / "PG013" / "text"
    text_dir.mkdir(parents=True)
    canonical = text_dir / "PG013-001.txt"
    canonical.write_text("novo", encoding="utf-8")
    legacy = text_dir / "bd07f101-534d-403a-b3ff-5b6ded5abf67-0001.txt"
    legacy.write_text("antigo", encoding="utf-8")
    work_a = text_dir / "work-a-002.txt"
    work_a.write_text("a", encoding="utf-8")
    work_b = text_dir / "work-b-002.txt"
    work_b.write_text("b", encoding="utf-8")

    assert discover_preferred_pages(text_dir, volume_id="PG013") == [
        canonical,
        work_a,
        work_b,
    ]
