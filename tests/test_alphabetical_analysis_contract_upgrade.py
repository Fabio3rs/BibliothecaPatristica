from __future__ import annotations

import json
from pathlib import Path

from patristica_pipeline.alphabetical_analysis_db import (
    connect_analysis_db,
    ensure_volume,
    init_analysis_schema,
    upgrade_locator_contracts,
)


def test_upgrade_locator_contracts_preserves_decision_and_adds_coordinates(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG003" / "text"
    source_root.mkdir(parents=True)
    db_path = tmp_path / "analysis.db"
    with connect_analysis_db(db_path) as con:
        init_analysis_schema(con)
        ensure_volume(
            con,
            volume_id="PG003",
            collection="PG",
            source_root=source_root,
        )
        con.execute(
            """INSERT INTO analysis_sections(
                section_key, volume_id, section_order, section_kind,
                heading_raw, file_start, file_end, semantic_json
            ) VALUES (?, ?, 1, 'foreign_terms', 'ONOMASTICUM', ?, ?, '{}')""",
            (
                "PG003:index",
                "PG003",
                str(source_root / "index-590.txt"),
                str(source_root / "index-600.txt"),
            ),
        )
        con.execute(
            """INSERT INTO analysis_entries(
                entry_key, volume_id, section_key, entry_order, entry_kind,
                lemma_raw, entry_raw, source_file, semantic_json
            ) VALUES ('PG003:e1', 'PG003', 'PG003:index', 1, 'lemma',
                      'Ablespia', 'Ablespia, Myst. Theol. cap. 2', ?, '{}')""",
            (str(source_root / "index-591.txt"),),
        )
        legacy = {
            "locator_key": "PG003:e1::ref:000001",
            "entry_key": "PG003:e1",
            "ref_order": 1,
            "ref_kind": "target_locator",
            "ref_raw": "Myst. Theol. cap. 2",
            "cited_pages": [],
        }
        con.execute(
            """INSERT INTO analysis_occurrences(
                occurrence_key, volume_id, entry_key, ref_order, ref_kind,
                ref_raw, locator_status, ref_json, locator_item_json
            ) VALUES (?, 'PG003', 'PG003:e1', 1, 'target_locator', ?,
                      'pending', '{}', ?)""",
            (
                legacy["locator_key"],
                legacy["ref_raw"],
                json.dumps(legacy),
            ),
        )

        counts = upgrade_locator_contracts(con, volume_id="PG003")
        con.commit()
        upgraded = json.loads(
            con.execute(
                "SELECT locator_item_json FROM analysis_occurrences"
            ).fetchone()[0]
        )
        status = con.execute(
            "SELECT locator_status FROM analysis_occurrences"
        ).fetchone()[0]

    assert counts == {
        "examined": 1,
        "updated": 1,
        "unchanged": 0,
        "invalid_json": 0,
    }
    assert upgraded["locator_format_version"] == 2
    assert upgraded["locator_contract"]["cited_location"]["ref_kind"] == (
        "target_locator"
    )
    assert upgraded["excluded_index_intervals"][0][
        "index_ocr_file_start"
    ].endswith("index-590.txt")
    assert status == "pending"
