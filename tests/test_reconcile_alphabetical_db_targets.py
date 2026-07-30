from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.alphabetical_index_db import connect_db, init_schema
from scripts.reconcile_alphabetical_db_targets import reconcile_volume


def test_reconcile_volume_requires_dual_evidence_and_can_apply(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    body_file = source_root / "page-001.txt"
    body_file.write_text(
        '<bloco tipo="cabecalho">101 HOMILIA 102</bloco>\n'
        "<apparatus>I Cor. 1, 4 cod. A; ms. B</apparatus>",
        encoding="utf-8",
    )
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX SCRIPTURAE", encoding="utf-8")
    db_path = tmp_path / "alphabetical.db"
    with connect_db(db_path) as con:
        init_schema(con)
        con.execute(
            """
            INSERT INTO alphabetical_volumes (
                volume_id, collection, source_root, volume_label,
                notes, created_at, updated_at
            ) VALUES ('PL001', 'PL', ?, 'PL001', NULL, 'now', 'now')
            """,
            (str(source_root),),
        )
        con.execute(
            """
            INSERT INTO alphabetical_sections (
                section_key, volume_id, section_order, section_kind,
                heading_raw, file_start, file_end, raw_json
            ) VALUES (
                'PL001:index', 'PL001', 1, 'scripture_index',
                'INDEX SCRIPTURAE', ?, ?, '{}'
            )
            """,
            (str(index_file), str(index_file)),
        )
        con.execute(
            """
            INSERT INTO alphabetical_entries (
                entry_key, section_key, entry_order, entry_kind,
                lemma_raw, entry_raw, raw_json
            ) VALUES (
                'PL001:index:e1', 'PL001:index', 1, 'scripture_citation',
                'I Cor. 1, 4', 'I Cor. 1, 4 ... 101', '{}'
            )
            """
        )
        con.execute(
            """
            INSERT INTO alphabetical_scripture_refs (
                entry_key, ref_order, ref_role, ref_raw,
                book_raw, book_norm, book_key,
                chapter_start, verse_start, raw_json
            ) VALUES (
                'PL001:index:e1', 1, 'citation', 'I Cor. 1, 4',
                'I Cor.', '1 Coríntios', '1 corintios', 1, 4, '{}'
            )
            """
        )
        con.execute(
            """
            INSERT INTO alphabetical_refs (
                entry_key, ref_order, scripture_ref_order, ref_kind,
                ref_raw, page_ref_raw, page_ref_int, locator_status,
                section_start_file, editorial_anchor_file, raw_json
            ) VALUES (
                'PL001:index:e1', 1, 1, 'editorial_page',
                '101', '101', 101, 'unresolved', ?, ?, '{}'
            )
            """,
            (str(index_file), str(index_file)),
        )
        con.commit()

        dry_run = reconcile_volume(con, volume_id="PL001", apply=False)
        assert dry_run["deterministic_resolved_count"] == 1
        assert dry_run["applied_count"] == 0
        assert con.execute(
            "SELECT target_file FROM alphabetical_refs"
        ).fetchone()["target_file"] is None

        applied = reconcile_volume(con, volume_id="PL001", apply=True)
        assert applied["applied_count"] == 1
        row = con.execute(
            """
            SELECT target_file, locator_status, raw_json
            FROM alphabetical_refs
            """
        ).fetchone()
        assert row["target_file"] == str(body_file)
        assert row["locator_status"] == "resolved"
        locator = json.loads(row["raw_json"])["compact_locator"]
        assert locator["method"] == "deterministic_dual_evidence"
