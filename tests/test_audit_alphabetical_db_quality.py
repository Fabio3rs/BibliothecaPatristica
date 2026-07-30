from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alphabetical_index_db import connect_db, init_schema  # noqa: E402
from audit_alphabetical_db_quality import audit_database  # noqa: E402


def insert_volume(con: sqlite3.Connection, volume_id: str) -> None:
    con.execute(
        """
        INSERT INTO alphabetical_volumes (
            volume_id, collection, source_root, created_at, updated_at
        ) VALUES (?, 'PL', ?, '2026-07-29T00:00:00+00:00',
                  '2026-07-29T00:00:00+00:00')
        """,
        (volume_id, f"/corpus/{volume_id}/text"),
    )


def insert_section(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    section_key: str,
    file_start: str,
    file_end: str,
    raw_json: dict | None = None,
    section_kind: str = "scripture_index",
    heading_raw: str = "INDEX SCRIPTURAE",
) -> None:
    con.execute(
        """
        INSERT INTO alphabetical_sections (
            section_key, volume_id, section_kind, heading_raw,
            file_start, file_end, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            section_key,
            volume_id,
            section_kind,
            heading_raw,
            file_start,
            file_end,
            json.dumps(raw_json or {}),
        ),
    )


def insert_entry(
    con: sqlite3.Connection,
    *,
    section_key: str,
    entry_key: str,
    entry_order: int,
    target_file_best: str | None = None,
    raw_json: dict | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO alphabetical_entries (
            entry_key, section_key, entry_order, entry_kind,
            entry_raw, target_file_best, raw_json
        ) VALUES (?, ?, ?, 'scripture_citation', ?, ?, ?)
        """,
        (
            entry_key,
            section_key,
            entry_order,
            entry_key,
            target_file_best,
            json.dumps(raw_json or {}),
        ),
    )


def insert_scripture_ref(
    con: sqlite3.Connection,
    *,
    entry_key: str,
    ref_order: int = 1,
    chapter_start: int = 1,
    chapter_end: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO alphabetical_scripture_refs (
            entry_key, ref_order, ref_role, ref_raw, book_raw, book_norm,
            chapter_start, chapter_end, raw_json
        ) VALUES (?, ?, 'citation', 'Gen. 1', 'Gen.', 'Genesis', ?, ?, '{}')
        """,
        (entry_key, ref_order, chapter_start, chapter_end),
    )


def insert_material_ref(
    con: sqlite3.Connection,
    *,
    entry_key: str,
    ref_order: int,
    target_file: str | None,
    scripture_ref_order: int | None = None,
    probability: float | None = 0.9,
) -> int:
    cursor = con.execute(
        """
        INSERT INTO alphabetical_refs (
            entry_key, ref_order, scripture_ref_order, ref_kind, ref_raw,
            page_ref_raw, page_ref_int, target_file, target_file_probability,
            raw_json
        ) VALUES (?, ?, ?, 'editorial_page', ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_key,
            ref_order,
            scripture_ref_order,
            str(1000 + ref_order),
            str(1000 + ref_order),
            1000 + ref_order,
            target_file,
            probability,
            json.dumps(
                {
                    "compact_locator": {
                        "status": "resolved",
                        "evidence": [{"kind": "editorial_header_match"}],
                    }
                }
            ),
        ),
    )
    return int(cursor.lastrowid)


def make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "alphabetical.db"
    with connect_db(db_path) as con:
        init_schema(con)
    return db_path


def test_dry_run_does_not_change_corpus_or_quality_rows(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    inside = "/corpus/PL001/text/scan-0105.txt"
    outside = "/corpus/PL001/text/scan-0090.txt"
    with connect_db(db_path) as con:
        insert_volume(con, "PL001")
        insert_section(
            con,
            volume_id="PL001",
            section_key="PL001:section:1",
            file_start="/corpus/PL001/text/scan-0100.txt",
            file_end="/corpus/PL001/text/scan-0110.txt",
        )
        insert_entry(
            con,
            section_key="PL001:section:1",
            entry_key="PL001:entry:1",
            entry_order=1,
            target_file_best=inside,
        )
        insert_scripture_ref(con, entry_key="PL001:entry:1")
        insert_material_ref(
            con,
            entry_key="PL001:entry:1",
            ref_order=1,
            target_file=inside,
        )
        insert_material_ref(
            con,
            entry_key="PL001:entry:1",
            ref_order=2,
            target_file=outside,
        )
        con.commit()

    summary = audit_database(db_path, volume_ids=["PL001"])

    report = summary["volumes"][0]
    assert summary["mode"] == "dry-run"
    assert report["issue_counts_before"]["targets_inside_section"] == 1
    assert report["issue_counts_before"]["missing_scripture_links"] == 2
    assert report["planned_repairs"] == {
        "scripture_links": 2,
        "targets_to_quarantine": 1,
        "entries_to_recompute": 1,
    }
    assert report["status_if_applied"] == "needs_reextract"

    with sqlite3.connect(db_path) as con:
        refs = con.execute(
            """
            SELECT ref_order, scripture_ref_order, target_file
            FROM alphabetical_refs
            ORDER BY ref_order
            """
        ).fetchall()
        quality_count = con.execute(
            "SELECT COUNT(*) FROM alphabetical_volume_quality"
        ).fetchone()[0]
    assert refs == [(1, None, inside), (2, None, outside)]
    assert quality_count == 0


def test_apply_links_single_passage_and_quarantines_index_target(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    inside = "/corpus/PL002/text/scan-0205.txt"
    outside = "/corpus/PL002/text/scan-0120.txt"
    with connect_db(db_path) as con:
        insert_volume(con, "PL002")
        insert_section(
            con,
            volume_id="PL002",
            section_key="PL002:section:1",
            file_start="/corpus/PL002/text/scan-0200.txt",
            file_end="/corpus/PL002/text/scan-0210.txt",
        )
        insert_entry(
            con,
            section_key="PL002:section:1",
            entry_key="PL002:entry:1",
            entry_order=1,
            target_file_best=inside,
        )
        insert_scripture_ref(con, entry_key="PL002:entry:1")
        quarantined_ref_id = insert_material_ref(
            con,
            entry_key="PL002:entry:1",
            ref_order=1,
            target_file=inside,
            probability=0.99,
        )
        insert_material_ref(
            con,
            entry_key="PL002:entry:1",
            ref_order=2,
            target_file=outside,
            probability=0.75,
        )
        con.commit()

    summary = audit_database(db_path, volume_ids=["PL002"], apply=True)
    report = summary["volumes"][0]
    assert report["repairs"]["scripture_links_filled"] == 2
    assert report["repairs"]["targets_quarantined"] == 1
    assert report["status"] == "needs_reextract"

    with sqlite3.connect(db_path) as con:
        quarantined = con.execute(
            """
            SELECT target_file, target_file_probability, scripture_ref_order
            FROM alphabetical_refs WHERE ref_id = ?
            """,
            (quarantined_ref_id,),
        ).fetchone()
        linked_orders = con.execute(
            "SELECT scripture_ref_order FROM alphabetical_refs ORDER BY ref_order"
        ).fetchall()
        entry_best = con.execute(
            """
            SELECT target_file_best FROM alphabetical_entries
            WHERE entry_key = 'PL002:entry:1'
            """
        ).fetchone()[0]
        quality = con.execute(
            """
            SELECT status, raw_json FROM alphabetical_volume_quality
            WHERE volume_id = 'PL002'
            """
        ).fetchone()
    assert quarantined == (None, None, 1)
    assert linked_orders == [(1,), (1,)]
    assert entry_best == outside
    assert quality[0] == "needs_reextract"
    details = json.loads(quality[1])
    assert details["repairs"]["targets_quarantined"] == 1
    assert details["blocking_issue_counts"][
        "quarantined_targets_needing_relocation"
    ] == 1


def test_apply_marks_valid_when_only_missing_link_is_deterministic(
    tmp_path: Path,
) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL003")
        insert_section(
            con,
            volume_id="PL003",
            section_key="PL003:section:1",
            file_start="/corpus/PL003/text/scan-0300.txt",
            file_end="/corpus/PL003/text/scan-0310.txt",
        )
        insert_entry(
            con,
            section_key="PL003:section:1",
            entry_key="PL003:entry:1",
            entry_order=1,
        )
        insert_scripture_ref(con, entry_key="PL003:entry:1")
        insert_material_ref(
            con,
            entry_key="PL003:entry:1",
            ref_order=1,
            target_file="/corpus/PL003/text/scan-0100.txt",
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL003"],
        apply=True,
    )["volumes"][0]

    assert report["status"] == "valid"
    assert report["repairs"]["scripture_links_filled"] == 1
    with sqlite3.connect(db_path) as con:
        quality_status = con.execute(
            """
            SELECT status FROM alphabetical_volume_quality
            WHERE volume_id = 'PL003'
            """
        ).fetchone()[0]
    assert quality_status == "valid"


def test_target_without_page_specific_evidence_marks_volume_partial(
    tmp_path: Path,
) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL003")
        insert_section(
            con,
            volume_id="PL003",
            section_key="PL003:section:1",
            file_start="/corpus/PL003/text/scan-0300.txt",
            file_end="/corpus/PL003/text/scan-0310.txt",
        )
        insert_entry(
            con,
            section_key="PL003:section:1",
            entry_key="PL003:entry:1",
            entry_order=1,
        )
        ref_id = insert_material_ref(
            con,
            entry_key="PL003:entry:1",
            ref_order=1,
            target_file="/corpus/PL003/text/scan-0100.txt",
        )
        con.execute(
            "UPDATE alphabetical_refs SET raw_json = '{}' WHERE ref_id = ?",
            (ref_id,),
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL003"],
        apply=True,
    )["volumes"][0]

    assert report["status"] == "needs_reextract"
    assert report["repairs"]["targets_quarantined"] == 1
    assert report["repairs"]["targets_cleared"] == 1
    assert report["issue_counts_after"]["unverified_target_evidence"] == 0
    assert report["issue_counts_after"]["material_refs_without_target"] == 1
    with sqlite3.connect(db_path) as con:
        quality_status, target_file = con.execute(
            """
            SELECT q.status, r.target_file
            FROM alphabetical_volume_quality q
            JOIN alphabetical_refs r ON 1 = 1
            WHERE volume_id = 'PL003'
            """
        ).fetchone()
    assert quality_status == "needs_reextract"
    assert target_file is None


def test_empty_volume_needs_evidence_before_it_is_valid(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL005")
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL005"],
        apply=True,
    )["volumes"][0]

    assert report["status"] == "needs_reextract"
    assert report["blocking_issue_counts"]["unverified_empty_extraction"] == 1


def test_scripture_entry_without_scripture_ref_needs_reextract(
    tmp_path: Path,
) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PO016")
        insert_section(
            con,
            volume_id="PO016",
            section_key="PO016:section:1",
            file_start="/corpus/PO016/text/scan-0300.txt",
            file_end="/corpus/PO016/text/scan-0310.txt",
        )
        insert_entry(
            con,
            section_key="PO016:section:1",
            entry_key="PO016:entry:1",
            entry_order=1,
        )
        insert_material_ref(
            con,
            entry_key="PO016:entry:1",
            ref_order=1,
            target_file=None,
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PO016"],
        apply=True,
    )["volumes"][0]

    assert report["status"] == "needs_reextract"
    assert (
        report["issue_counts_after"][
            "scripture_entries_without_scripture_refs"
        ]
        == 1
    )


def test_confirmed_empty_volume_is_valid(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL006")
        payload = {
            "coverage": {
                "entries_status": "no_index_section",
                "entries_status_reason": "No closing alphabetical index was found.",
                "evidence_files": ["/corpus/PL006/text/scan-0100.txt"],
            }
        }
        con.execute(
            """
            INSERT INTO alphabetical_runs (
                volume_id, status, started_at, finished_at, raw_json
            ) VALUES (
                'PL006', 'imported',
                '2026-07-29T00:00:00+00:00',
                '2026-07-29T00:00:00+00:00', ?
            )
            """,
            (json.dumps(payload),),
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL006"],
        apply=True,
    )["volumes"][0]

    assert report["status"] == "valid"
    assert report["blocking_issue_counts"]["unverified_empty_extraction"] == 0


def test_general_pipeline_sections_are_blocking_alpha_quality(
    tmp_path: Path,
) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL007")
        insert_section(
            con,
            volume_id="PL007",
            section_key="PL007:section:ordo",
            file_start="/corpus/PL007/text/scan-0100.txt",
            file_end="/corpus/PL007/text/scan-0101.txt",
            section_kind="ordo_rerum",
            heading_raw="ORDO RERUM",
        )
        insert_section(
            con,
            volume_id="PL007",
            section_key="PL007:section:closure",
            file_start="/corpus/PL007/text/scan-0102.txt",
            file_end="/corpus/PL007/text/scan-0103.txt",
            section_kind="editorial_closure",
            heading_raw="ADDENDA ET CORRIGENDA",
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL007"],
        apply=True,
    )["volumes"][0]

    assert report["issue_counts_after"]["ordo_rerum_sections"] == 1
    assert report["issue_counts_after"]["editorial_closure_sections"] == 1
    assert report["blocking_issue_counts"]["ordo_rerum_sections"] == 1
    assert report["blocking_issue_counts"]["editorial_closure_sections"] == 1
    assert report["status"] == "needs_reextract"

    with connect_db(db_path) as con:
        quality = con.execute(
            """SELECT status, forbidden_section_count
            FROM alphabetical_volume_quality
            WHERE volume_id = 'PL007'"""
        ).fetchone()
    assert dict(quality) == {
        "status": "needs_reextract",
        "forbidden_section_count": 2,
    }


def test_unrepairable_semantic_and_link_defects_are_reported(
    tmp_path: Path,
) -> None:
    db_path = make_db(tmp_path)
    with connect_db(db_path) as con:
        insert_volume(con, "PL004")
        insert_section(
            con,
            volume_id="PL004",
            section_key="PL004:section:1",
            file_start="/corpus/PL004/text/scan-0500.txt",
            file_end="/corpus/PL004/text/scan-0510.txt",
        )

        insert_entry(
            con,
            section_key="PL004:section:1",
            entry_key="PL004:entry:multi",
            entry_order=1,
        )
        insert_scripture_ref(
            con, entry_key="PL004:entry:multi", ref_order=1
        )
        insert_scripture_ref(
            con, entry_key="PL004:entry:multi", ref_order=2
        )
        insert_material_ref(
            con,
            entry_key="PL004:entry:multi",
            ref_order=1,
            target_file="/corpus/PL004/text/scan-0100.txt",
        )

        insert_entry(
            con,
            section_key="PL004:section:1",
            entry_key="PL004:entry:chapter",
            entry_order=2,
        )
        insert_scripture_ref(
            con,
            entry_key="PL004:entry:chapter",
            chapter_start=1,
            chapter_end=151,
        )
        insert_material_ref(
            con,
            entry_key="PL004:entry:chapter",
            ref_order=1,
            scripture_ref_order=1,
            target_file="/corpus/PL004/text/scan-0101.txt",
        )

        insert_entry(
            con,
            section_key="PL004:section:1",
            entry_key="PL004:entry:no-material",
            entry_order=3,
        )
        insert_scripture_ref(con, entry_key="PL004:entry:no-material")

        insert_entry(
            con,
            section_key="PL004:section:1",
            entry_key="PL004:entry:source-only-entry",
            entry_order=4,
            raw_json={"material_reference_mode": "source_only"},
        )
        insert_scripture_ref(
            con, entry_key="PL004:entry:source-only-entry"
        )

        insert_entry(
            con,
            section_key="PL004:section:1",
            entry_key="PL004:entry:dangling",
            entry_order=5,
        )
        insert_scripture_ref(con, entry_key="PL004:entry:dangling")
        insert_material_ref(
            con,
            entry_key="PL004:entry:dangling",
            ref_order=1,
            scripture_ref_order=1,
            target_file="/corpus/PL004/text/scan-0102.txt",
        )
        con.execute(
            """
            DELETE FROM alphabetical_scripture_refs
            WHERE entry_key = 'PL004:entry:dangling'
            """
        )

        insert_section(
            con,
            volume_id="PL004",
            section_key="PL004:section:source-only",
            file_start="/corpus/PL004/text/scan-0520.txt",
            file_end="/corpus/PL004/text/scan-0530.txt",
            raw_json={"material_reference_mode": "source_only"},
        )
        insert_entry(
            con,
            section_key="PL004:section:source-only",
            entry_key="PL004:entry:source-only-section",
            entry_order=1,
        )
        insert_scripture_ref(
            con, entry_key="PL004:entry:source-only-section"
        )
        con.commit()

    report = audit_database(
        db_path,
        volume_ids=["PL004"],
        apply=True,
    )["volumes"][0]

    counts = report["issue_counts_after"]
    assert counts["missing_scripture_links"] == 1
    assert counts["dangling_scripture_links"] == 1
    assert counts["multiple_scripture_refs"] == 1
    assert counts["implausible_chapters"] == 1
    assert counts["biblical_entries_without_material_refs"] == 1
    assert report["repairs"]["scripture_links_filled"] == 0
    assert report["status"] == "needs_reextract"
