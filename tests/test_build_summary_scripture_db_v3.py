from __future__ import annotations

import gzip
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.scripture_citation_index import build_citation_database
from scripts.scripture_keywords.build_summary_scripture_db_v3 import build_database
from scripts.scripture_keywords.export_summary_scripture_book_shards_v3 import (
    export_book_shards,
)


def _versification(path: Path) -> None:
    def chapters(count: int, overrides: dict[int, int]) -> list[dict]:
        output = []
        for chapter in range(1, count + 1):
            maximum = overrides.get(chapter, 1)
            output.append(
                {
                    "chapter": chapter,
                    "verses": [
                        {"verse": verse, "text": f"v{verse}"}
                        for verse in range(1, maximum + 1)
                    ],
                }
            )
        return output

    path.write_text(
        json.dumps(
            {
                "books": [
                    {"name": "Psalms", "chapters": chapters(151, {42: 5, 50: 20})},
                    {"name": "Matthew", "chapters": chapters(28, {5: 48})},
                    {"name": "John", "chapters": chapters(21, {3: 36})},
                ]
            }
        ),
        encoding="utf-8",
    )


def _summary_db(path: Path, ocr_file: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE resumos(
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
        connection.execute(
            "INSERT INTO resumos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                10,
                "PL999",
                697,
                ocr_file.name,
                "2026-08-26T10:00:00+00:00",
                json.dumps(
                    {
                        "keywords": [
                            "João 3,16",
                            "Salmo 42",
                            "João VI Cantacuzeno",
                        ]
                    },
                    ensure_ascii=False,
                ),
                "tudo",
                "model-a",
            ),
        )


def test_combines_keyword_and_ocr_mentions_with_physical_page_and_provenance(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "teste" / "PL999" / "text"
    source_root.mkdir(parents=True)
    ocr_file = source_root / "fixture-697.txt"
    ocr_file.write_text(
        """<pagina tipo="conteudo">
<cabecalho>1371 PSALMUS XLII. 1372</cabecalho>
<bloco tipo="texto_principal">
PSALMUS XLII. Matth. V, 3. Ioan. III, 99. Psal. XLII, 14. Psal. L, 12-14. 4.
</bloco>
</pagina>""",
        encoding="utf-8",
    )
    source_db = tmp_path / "resumos.db"
    ocr_db = tmp_path / "ocr.db"
    output_db = tmp_path / "combined.db"
    versification = tmp_path / "vulgate.json"
    _summary_db(source_db, ocr_file)
    _versification(versification)
    build_citation_database(
        db_path=ocr_db,
        volumes=[("PL999", "PL", source_root)],
        workers=2,
        payload_dir=tmp_path / "missing-payloads",
    )

    stats = build_database(
        source_db,
        ocr_db,
        output_db,
        versification_json=versification,
        progress_every=100,
    )

    assert stats["mentions_keyword"] == 2
    assert stats["mentions_ocr"] == 4
    assert stats["ocr_rejected_verse_out_of_profile"] == 1
    assert stats["ocr_rejected_detector_incomplete"] == 1
    assert stats["keyword_rejected_ambiguous_person_ordinal"] == 1

    with sqlite3.connect(output_db) as connection:
        rows = connection.execute(
            """
            SELECT r.normalized, p.volume_id, p.physical_page,
                   m.source_origin, m.source_context, m.raw_reference
              FROM scripture_mentions AS m
              JOIN scripture_references AS r ON r.id = m.reference_id
              JOIN pages AS p ON p.id = m.page_id
             ORDER BY r.normalized, m.source_origin, m.source_context
            """
        ).fetchall()
        masks = connection.execute(
            """
            SELECT r.normalized, rp.source_mask
              FROM reference_pages AS rp
              JOIN scripture_references AS r ON r.id = rp.reference_id
             ORDER BY r.normalized
            """
        ).fetchall()
        versifications = connection.execute(
            "SELECT DISTINCT versification_id FROM scripture_references"
        ).fetchall()
        rejected = connection.execute(
            """
            SELECT source_origin, raw_reference, issue_code
              FROM rejected_mentions
             ORDER BY source_origin, issue_code
            """
        ).fetchall()

    assert rows == [
        ("Salmos 42", "PL999", 697, "keyword", "standalone", "Salmo 42"),
        ("Salmos 42", "PL999", 697, "ocr", "body", "PSALMUS XLII"),
        ("Salmos 42", "PL999", 697, "ocr", "header", "PSALMUS XLII"),
        ("Salmos 50:12-14", "PL999", 697, "ocr", "body", "Psal. L, 12-14. 4"),
        ("São João 3:16", "PL999", 697, "keyword", "standalone", "João 3,16"),
        ("São Mateus 5:3", "PL999", 697, "ocr", "body", "Matth. V, 3"),
    ]
    assert masks == [
        ("Salmos 42", 5),
        ("Salmos 50:12-14", 4),
        ("São João 3:16", 1),
        ("São Mateus 5:3", 4),
    ]
    assert versifications == [("unknown",)]
    assert ("ocr", "Ioan. III, 99", "verse_out_of_profile") in rejected
    assert ("ocr", "Psal. XLII, 14", "detector_incomplete") in rejected
    assert (
        "keyword",
        "João VI",
        "ambiguous_person_ordinal",
    ) in rejected

    shards = tmp_path / "shards"
    manifest = export_book_shards(output_db, shards)
    assert manifest["v"] == 3
    assert manifest["detector"] == "5"
    assert manifest["source_mask"] == [
        "standalone_keyword",
        "embedded_keyword",
        "ocr",
    ]
    salmos_route = manifest["routes"]["salmos"]
    payload = json.loads(gzip.decompress((shards / salmos_route["url"]).read_bytes()))
    assert payload["v"] == 3
    assert payload["source_mask"] == manifest["source_mask"]
    assert payload["references"] == [
        [1, [42, 0, 42, 0], [[0, [697, 5]]]],
        [3, [50, 12, 50, 14], [[0, [697, 4]]]],
    ]


def test_refuses_to_overwrite_existing_combined_db(tmp_path: Path) -> None:
    output = tmp_path / "combined.db"
    output.touch()

    try:
        build_database(
            tmp_path / "missing-summary.db",
            tmp_path / "missing-ocr.db",
            output,
            versification_json=tmp_path / "missing-versification.json",
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output should not be overwritten")
