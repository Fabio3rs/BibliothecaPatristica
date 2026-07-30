from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "render_publication_from_shards.py"
SPEC = importlib.util.spec_from_file_location("render_publication_from_shards", MODULE_PATH)
assert SPEC and SPEC.loader
render_publication = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = render_publication
SPEC.loader.exec_module(render_publication)

PageRecord = render_publication.PageRecord
page_blocks = render_publication.page_blocks


def make_page(page: int, file: str = "page.txt") -> PageRecord:
    return PageRecord(
        doc="PL030",
        page=page,
        file=file,
        summary_page="Resumo",
        summary_global="Resumo global",
        author="Autor",
        work="Obra",
        keywords=["graça"],
        keyword_categories={"tema": ["graça"]},
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_page_blocks_emit_raw_base_url_once() -> None:
    blocks, page_files = page_blocks(
        [make_page(2, "two.txt"), make_page(1, "one.txt")],
        block_size=100,
        volume_id="PL030",
        keyword_ids={"graça": "k:graca"},
        raw_base_url="https://raw.example.test/corpus",
    )

    assert page_files == [
        {
            "index": 1,
            "file": "meta/PL030-pages-001.json.gz",
            "page_first": 1,
            "page_last": 2,
            "count": 2,
        }
    ]
    assert blocks[0]["raw_base_url"] == "https://raw.example.test/corpus/PL030/text"
    assert blocks[0]["pages"][0]["raw"] == {"file": "one.txt"}
    assert blocks[0]["pages"][1]["raw"] == {"file": "two.txt"}
    assert "url" not in blocks[0]["pages"][0]["raw"]


def test_page_blocks_omit_raw_base_url_when_disabled() -> None:
    blocks, _ = page_blocks(
        [make_page(1, "one.txt")],
        block_size=100,
        volume_id="PL030",
        keyword_ids={},
        raw_base_url="",
    )

    assert "raw_base_url" not in blocks[0]
    assert blocks[0]["pages"][0]["raw"] == {"file": "one.txt"}
