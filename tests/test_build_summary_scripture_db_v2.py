from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.scripture_keywords.build_summary_scripture_db_v2 import build_database
from scripts.scripture_keywords.export_summary_scripture_json_v2 import build_payload
from scripts.scripture_keywords.export_summary_scripture_book_shards_v2 import (
    export_book_shards,
)


def _source_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE resumos (
            id INTEGER PRIMARY KEY,
            documento TEXT NOT NULL,
            pagina_num INTEGER NOT NULL,
            pagina_file TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            keywords_source TEXT NOT NULL,
            keywords_modelo TEXT NOT NULL
        )
        """
    )
    rows = [
        (
            10,
            "PG001",
            100,
            "page-100.txt",
            "2026-08-25T10:00:00+00:00",
            {"keywords": ["João 3,16", "João VI Cantacuzeno"]},
            "tudo",
            "model-a",
        ),
        (
            11,
            "PL002",
            42,
            "page-42.txt",
            "2026-08-25T11:00:00+00:00",
            {"keywords": ["Pecado (João 3,16)", "Romanos 8,1"]},
            "tudo",
            "model-b",
        ),
        (
            12,
            "PO003",
            7,
            "page-7.txt",
            "2026-08-25T12:00:00+00:00",
            {"keywords": ["Sem citação bíblica"]},
            "tudo",
            "model-c",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO resumos VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [row[:5] + (json.dumps(row[5], ensure_ascii=False),) + row[6:] for row in rows],
    )
    connection.commit()
    connection.close()


def test_builds_new_db_from_legacy_v1_keywords_with_physical_pages(tmp_path):
    source = tmp_path / "resumos.db"
    output = tmp_path / "scripture.db"
    _source_db(source)

    stats = build_database(source, output, progress_every=100)

    assert stats["source_rows"] == 3
    assert stats["stored_pages"] == 2
    assert stats["unique_references"] == 2
    assert stats["unique_reference_page_pairs"] == 3
    assert stats["issues_ambiguous_person_ordinal"] == 1

    connection = sqlite3.connect(output)
    connection.row_factory = sqlite3.Row
    pages = connection.execute(
        "SELECT resumo_id, volume_id, physical_page, source_row_created_at FROM pages ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in pages] == [
        (10, "PG001", 100, "2026-08-25T10:00:00+00:00"),
        (11, "PL002", 42, "2026-08-25T11:00:00+00:00"),
    ]

    joao_locations = connection.execute(
        """
        SELECT p.volume_id, p.physical_page, m.source_kind
          FROM scripture_references AS r
          JOIN scripture_mentions AS m ON m.reference_id = r.id
          JOIN pages AS p ON p.id = m.page_id
         WHERE r.canonical_key = 'unknown|joao|3:16'
         ORDER BY p.volume_id
        """
    ).fetchall()
    assert [tuple(row) for row in joao_locations] == [
        ("PG001", 100, "standalone"),
        ("PL002", 42, "embedded"),
    ]
    run = connection.execute(
        "SELECT source_table, status FROM build_runs"
    ).fetchone()
    assert tuple(run) == ("resumos", "complete")
    connection.close()


def test_refuses_to_overwrite_an_existing_output(tmp_path):
    source = tmp_path / "resumos.db"
    output = tmp_path / "scripture.db"
    _source_db(source)
    output.touch()

    try:
        build_database(source, output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output should not be overwritten")


def test_compact_export_preserves_volume_physical_page_and_source_mask(tmp_path):
    source = tmp_path / "resumos.db"
    output = tmp_path / "scripture.db"
    _source_db(source)
    build_database(source, output, progress_every=100)

    connection = sqlite3.connect(output)
    connection.row_factory = sqlite3.Row
    payload, stats = build_payload(connection)
    connection.close()

    joao_index = next(
        index for index, book in enumerate(payload["books"]) if book[0] == "joao"
    )
    joao = next(row for row in payload["references"] if row[0] == joao_index)
    decoded = []
    for volume_index, flat_locations in joao[3]:
        page = 0
        for offset in range(0, len(flat_locations), 2):
            page += flat_locations[offset]
            decoded.append(
                (
                    payload["volumes"][volume_index],
                    page,
                    flat_locations[offset + 1],
                )
            )
    assert decoded == [("PG001", 100, 1), ("PL002", 42, 2)]
    assert stats["reference_page_pairs"] == 3


def test_book_shards_are_self_contained_and_route_one_book_to_one_file(tmp_path):
    source = tmp_path / "resumos.db"
    database = tmp_path / "scripture.db"
    shards = tmp_path / "shards"
    _source_db(source)
    build_database(source, database, progress_every=100)

    manifest = export_book_shards(database, shards)

    joao_route = manifest["routes"]["joao"]
    assert manifest["schema"] == "bibliotheca-scripture-book-shards-v2"
    assert manifest["source"]["table"] == "resumos"
    assert joao_route["references"] == 1
    assert joao_route["reference_page_pairs"] == 2
    assert "ioan" in joao_route["aliases"]
    assert re.fullmatch(r"049-joao\.[0-9a-f]{12}\.json\.gz", joao_route["url"])
    assert joao_route["sha256"].startswith(joao_route["url"].split(".")[1])
    with gzip.open(shards / joao_route["url"], "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    assert payload["book"] == ["joao", "São João"]
    assert payload["volumes"] == ["PG001", "PL002"]
    assert payload["references"][0][2] == [[0, [100, 1]], [1, [42, 2]]]
    assert sorted(path.name for path in shards.glob("*.json")) == ["manifest.json"]
