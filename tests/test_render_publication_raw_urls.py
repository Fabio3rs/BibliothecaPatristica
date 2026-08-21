from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "render_publication_from_shards.py"
SPEC = importlib.util.spec_from_file_location("render_publication_from_shards", MODULE_PATH)
assert SPEC and SPEC.loader
render_publication = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = render_publication
SPEC.loader.exec_module(render_publication)

PageRecord = render_publication.PageRecord
KeywordResolution = render_publication.KeywordResolution
page_blocks = render_publication.page_blocks
build_keyword_lookup = render_publication.build_keyword_lookup
load_keyword_occurrences = render_publication.load_keyword_occurrences
write_json = render_publication.write_json


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


def test_page_blocks_keep_explicit_raw_url_only_as_override() -> None:
    page = make_page(1, "one.txt")
    page.raw_url = "https://archive.example.test/special/one.txt"

    blocks, _ = page_blocks(
        [page],
        block_size=100,
        volume_id="PL030",
        keyword_ids={},
        raw_base_url="https://raw.example.test/corpus",
    )

    assert blocks[0]["pages"][0]["raw"] == {
        "file": "one.txt",
        "url": "https://archive.example.test/special/one.txt",
    }


def test_page_blocks_bake_source_labels_and_dedupe_canonical_ids() -> None:
    page = make_page(1)
    page.keywords = ["graça", "graça divina", "termo isolado", "graça"]
    page.keyword_categories = {
        "tema": ["graça", "graça divina", "termo isolado"]
    }

    blocks, _ = page_blocks(
        [page],
        block_size=100,
        volume_id="PL030",
        keyword_ids={
            "graça": KeywordResolution("graça", "k:graca"),
            "graça divina": KeywordResolution("graça divina", "k:graca"),
            "termo isolado": KeywordResolution("termo isolado", None),
            "amor": KeywordResolution("amor", "k:amor"),
        },
        keyword_occurrences={1: ["graça divina", "amor", "termo isolado"]},
    )

    emitted = blocks[0]["pages"][0]
    assert emitted["keyword_ids"] == ["k:graca", "k:amor"]
    assert emitted["keyword_labels"] == [
        "graça",
        "graça divina",
        "termo isolado",
        "amor",
    ]
    assert emitted["keyword_display_count"] == 3
    assert emitted["keyword_categories"] == {"tema": ["k:graca"]}


def test_keyword_lookup_keeps_ungrouped_and_scripture_labels(tmp_path) -> None:
    db_path = tmp_path / "keywords.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY,
            keyword_original TEXT,
            keyword_norm TEXT,
            hdbscan_group_id INTEGER,
            is_scripture_citation INTEGER
        );
        CREATE TABLE cluster_canonical_names (
            group_id INTEGER PRIMARY KEY,
            nome_canonico TEXT,
            nome_canonico_original TEXT
        );
        CREATE TABLE keyword_occurrence (
            id INTEGER PRIMARY KEY,
            keyword_id INTEGER,
            documento TEXT,
            pagina_num INTEGER,
            rank_in_page INTEGER,
            rank_position INTEGER
        );
        INSERT INTO cluster_canonical_names VALUES (7, 'graca divina', 'Graça divina');
        INSERT INTO keywords VALUES (10, 'Graça', 'graca', 7, 0);
        INSERT INTO keywords VALUES (11, 'Termo isolado', 'termo isolado', NULL, 0);
        INSERT INTO keywords VALUES (12, 'João 3:16', 'joao 3 16', -1, 1);
        INSERT INTO keyword_occurrence VALUES (1, 11, 'PL030', 7, 2, NULL);
        INSERT INTO keyword_occurrence VALUES (2, 10, 'PL030', 7, 1, NULL);
        """
    )
    con.commit()
    con.close()
    lookup_path = tmp_path / "keywords_lookup.json"
    lookup_path.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "k:graca-divina", "group_id": 7, "iscit": False},
                    {"id": "k:joao-3-16", "group_id": -1, "iscit": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    lookup = build_keyword_lookup(db_path, lookup_path)

    assert lookup["Graça"] == KeywordResolution("Graça", "k:graca-divina")
    assert lookup["Termo isolado"] == KeywordResolution("Termo isolado", None)
    assert lookup["João 3:16"] == KeywordResolution("João 3:16", "k:joao-3-16")
    assert load_keyword_occurrences(db_path, "PL030") == {
        7: ["Graça", "Termo isolado"]
    }


def test_write_json_gzip_is_deterministic(tmp_path) -> None:
    first = tmp_path / "first.json.gz"
    second = tmp_path / "second.json.gz"

    write_json(first, {"items": ["graça"]})
    write_json(second, {"items": ["graça"]})

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[4:8] == b"\0\0\0\0"
