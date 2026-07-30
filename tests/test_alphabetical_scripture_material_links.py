from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from scripts.alphabetical_index_db import (  # noqa: E402
    ALPHABETICAL_DB_SCHEMA_VERSION,
    connect_db,
    get_volume_quality,
    init_schema,
)
from scripts.import_alphabetical_index_json import (  # noqa: E402
    ValidationErrors,
    build_validation_summary,
    import_payload,
)
from test_import_alphabetical_index_json import minimal_payload  # noqa: E402


def scripture_ref() -> dict:
    return {
        "entry_key": "PO009:entry:001",
        "ref_order": 1,
        "ref_role": "citation",
        "ref_raw": "Gen. I, 6",
        "ref_norm": "Gen 1:6",
        "book_raw": "Gen.",
        "book_norm": "Genesis",
        "chapter_start": 1,
        "verse_start": 6,
        "chapter_end": None,
        "verse_end": None,
        "is_range": 0,
        "confidence": 0.9,
        "raw_json": {},
    }


def material_ref(*, scripture_ref_order: int | None = 1) -> dict:
    return {
        "entry_key": "PO009:entry:001",
        "ref_order": 1,
        "scripture_ref_order": scripture_ref_order,
        "ref_kind": "editorial_page",
        "ref_raw": "169",
        "page_ref_raw": "169",
        "page_ref_int": 169,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": None,
        "range_end_raw": None,
        "target_file": "/tmp/po009/text/page-169.txt",
        "target_file_probability": 0.9,
        "section_start_file": "/tmp/po009/text/page-001.txt",
        "editorial_anchor_file": "/tmp/po009/text/page-001.txt",
        "confidence": 0.9,
        "raw_json": {
            "compact_locator": {
                "status": "resolved",
                "evidence": [{"kind": "editorial_header_match"}],
            }
        },
    }


def test_scripture_material_ref_requires_explicit_parent_order() -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    payload["refs"] = [material_ref(scripture_ref_order=None)]

    with pytest.raises(ValidationErrors, match="must explicitly set scripture_ref_order"):
        build_validation_summary(payload)


def test_scripture_material_ref_rejects_missing_parent() -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    payload["refs"] = [material_ref(scripture_ref_order=2)]

    with pytest.raises(ValidationErrors, match="does not identify a scripture_refs parent"):
        build_validation_summary(payload)


def test_non_scripture_material_ref_remains_compatible_with_null_link() -> None:
    payload = minimal_payload()
    payload["entries"][0]["entry_kind"] = "lemma"
    payload["sections"][0]["section_kind"] = "onomastic_person"
    payload["refs"] = [material_ref(scripture_ref_order=None)]

    assert build_validation_summary(payload)["status"] == "valid"


def test_import_inserts_scripture_parent_before_material_link_and_records_quality(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()

        linked = con.execute(
            """SELECT r.scripture_ref_order, sr.book_key
            FROM alphabetical_refs r
            JOIN alphabetical_scripture_refs sr
              ON sr.entry_key = r.entry_key
             AND sr.ref_order = r.scripture_ref_order
            WHERE r.entry_key = 'PO009:entry:001'"""
        ).fetchone()
        quality = con.execute(
            """SELECT db_schema_version, status, linked_material_ref_count,
                      unlinked_scripture_material_ref_count,
                      dangling_scripture_link_count
            FROM alphabetical_volume_quality
            WHERE volume_id = 'PO009'"""
        ).fetchone()
        schema_version = con.execute(
            """SELECT meta_value
            FROM alphabetical_schema_meta
            WHERE meta_key = 'schema_version'"""
        ).fetchone()

    assert linked["scripture_ref_order"] == 1
    assert linked["book_key"] == "genesis"
    assert dict(quality) == {
        "db_schema_version": ALPHABETICAL_DB_SCHEMA_VERSION,
        "status": "valid",
        "linked_material_ref_count": 1,
        "unlinked_scripture_material_ref_count": 0,
        "dangling_scripture_link_count": 0,
    }
    assert schema_version["meta_value"] == str(ALPHABETICAL_DB_SCHEMA_VERSION)
    with connect_db(db_path) as con:
        con.execute("DELETE FROM alphabetical_volume_quality WHERE volume_id = 'PO009'")
        con.commit()
    assert get_volume_quality(db_path, "PO009")["status"] == "valid"


def test_import_uses_vulgate_numbering_for_migne_book_key(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["volume"]["collection"] = "PL"
    ref = scripture_ref()
    ref["ref_raw"] = "I Regum III, 4"
    ref["ref_norm"] = None
    ref["book_raw"] = "I Regum"
    ref["book_norm"] = None
    payload["scripture_refs"] = [ref]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()
        row = con.execute(
            "SELECT book_key FROM alphabetical_scripture_refs"
        ).fetchone()

    assert row["book_key"] == "1 samuel"


def test_import_uses_old_french_vulgate_numbering_for_po_book_key(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    ref = scripture_ref()
    ref["ref_raw"] = "I Rois 32"
    ref["ref_norm"] = None
    ref["book_raw"] = "I Rois"
    ref["book_norm"] = "I Rois"
    payload["scripture_refs"] = [ref]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()
        row = con.execute(
            """SELECT q.status, q.unknown_scripture_book_count, sr.book_key
            FROM alphabetical_volume_quality q
            JOIN alphabetical_scripture_refs sr ON 1 = 1
            WHERE q.volume_id = 'PO009'"""
        ).fetchone()

    assert dict(row) == {
        "status": "valid",
        "unknown_scripture_book_count": 0,
        "book_key": "1 samuel",
    }


def test_import_uses_section_local_old_english_numbering_for_po(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["sections"][0]["raw_json"] = {
        "scripture_numbering_profile": "po_old_english"
    }
    ref = scripture_ref()
    ref["ref_raw"] = "I Kings II, 10"
    ref["ref_norm"] = None
    ref["book_raw"] = "I Kings"
    ref["book_norm"] = "I Kings"
    payload["scripture_refs"] = [ref]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()
        row = con.execute(
            "SELECT book_key FROM alphabetical_scripture_refs"
        ).fetchone()

    assert row["book_key"] == "1 samuel"


def test_historical_noncanonical_esdras_is_not_an_unknown_book(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    ref = scripture_ref()
    ref["ref_raw"] = "III Esdras IV, 1"
    ref["ref_norm"] = None
    ref["book_raw"] = "III Esdras"
    ref["book_norm"] = None
    payload["scripture_refs"] = [ref]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()
        row = con.execute(
            """SELECT q.status, q.unknown_scripture_book_count, sr.book_key,
                      json_extract(
                          sr.raw_json, '$.historical_book_key'
                      ) AS historical_book_key
            FROM alphabetical_volume_quality q
            JOIN alphabetical_scripture_refs sr ON 1 = 1
            WHERE q.volume_id = 'PO009'"""
        ).fetchone()

    assert dict(row) == {
        "status": "valid",
        "unknown_scripture_book_count": 0,
        "book_key": None,
        "historical_book_key": "3 esdras",
    }


def test_imported_partial_locator_is_not_treated_as_skip_done_quality(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    unresolved_ref = material_ref()
    unresolved_ref["target_file"] = None
    unresolved_ref["target_file_probability"] = None
    unresolved_ref["raw_json"] = {
        "compact_locator": {
            "status": "ambiguous",
            "reason": "Two physical pages remain equally plausible.",
        }
    }
    payload["refs"] = [unresolved_ref]
    payload["entries"][0]["target_file_best"] = None
    payload["coverage"] = {
        "locator_status": "partial",
        "locator_total_refs": 1,
        "locator_resolved_refs": 0,
        "entries_status": "partial_extraction",
        "entries_status_reason": "One material citation remains ambiguous.",
    }
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()

    quality = get_volume_quality(db_path, "PO009")
    assert quality is not None
    assert quality["status"] == "partial"
    assert quality["unresolved_material_ref_count"] == 1
    assert quality["locator_partial"] == 1


def test_unrecoverable_empty_extraction_requires_reextract_quality(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["entries"] = []
    payload["refs"] = []
    payload["scripture_refs"] = []
    payload["coverage"] = {
        "entries_status": "unrecoverable_ocr",
        "entries_status_reason": "The index rows are illegible after OCR recovery.",
        "evidence_files": ["/tmp/po009/text/page-001.txt"],
    }
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.commit()

    quality = get_volume_quality(db_path, "PO009")
    assert quality is not None
    assert quality["status"] == "needs_reextract"


def test_schema_upgrade_recomputes_stale_quality_without_external_auditor(
    tmp_path: Path,
) -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    unresolved_ref = material_ref()
    unresolved_ref["target_file"] = None
    unresolved_ref["target_file_probability"] = None
    payload["refs"] = [unresolved_ref]
    payload["entries"][0]["target_file_best"] = None
    payload["coverage"] = {
        "locator_status": "partial",
        "entries_status": "partial_extraction",
        "entries_status_reason": "The locator is incomplete.",
    }
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        con.execute(
            """UPDATE alphabetical_volume_quality
            SET db_schema_version = 2,
                status = 'valid',
                unresolved_material_ref_count = 0,
                locator_partial = 0
            WHERE volume_id = 'PO009'"""
        )
        con.commit()

    with connect_db(db_path) as con:
        init_schema(con)
        row = con.execute(
            """SELECT db_schema_version, status,
                      unresolved_material_ref_count, locator_partial
            FROM alphabetical_volume_quality
            WHERE volume_id = 'PO009'"""
        ).fetchone()

    assert dict(row) == {
        "db_schema_version": ALPHABETICAL_DB_SCHEMA_VERSION,
        "status": "partial",
        "unresolved_material_ref_count": 1,
        "locator_partial": 1,
    }


def test_database_trigger_rejects_dangling_scripture_parent(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["scripture_refs"] = [scripture_ref()]
    payload["refs"] = [material_ref()]
    db_path = tmp_path / "alphabetical.db"

    with connect_db(db_path) as con:
        init_schema(con)
        import_payload(con, payload, replace=False)
        with pytest.raises(sqlite3.IntegrityError, match="scripture parent not found"):
            con.execute(
                """UPDATE alphabetical_refs
                SET scripture_ref_order = 2
                WHERE entry_key = 'PO009:entry:001'"""
            )


def test_existing_database_is_altered_and_single_scripture_parent_is_backfilled(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE alphabetical_refs (
            ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL,
            ref_order INTEGER NOT NULL,
            ref_kind TEXT NOT NULL,
            ref_raw TEXT NOT NULL,
            page_ref_raw TEXT,
            page_ref_int INTEGER,
            page_ref_col TEXT,
            line_ref_raw TEXT,
            range_start_raw TEXT,
            range_end_raw TEXT,
            target_file TEXT,
            target_file_probability REAL,
            section_start_file TEXT,
            editorial_anchor_file TEXT,
            confidence REAL,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE alphabetical_scripture_refs (
            scripture_ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL,
            ref_order INTEGER NOT NULL,
            ref_role TEXT NOT NULL,
            ref_raw TEXT NOT NULL,
            ref_norm TEXT,
            book_raw TEXT,
            book_norm TEXT,
            chapter_start INTEGER,
            verse_start INTEGER,
            chapter_end INTEGER,
            verse_end INTEGER,
            is_range INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            raw_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_alpha_scripture_entry_order_role
            ON alphabetical_scripture_refs(entry_key, ref_order, ref_role);
        INSERT INTO alphabetical_refs (
            entry_key, ref_order, ref_kind, ref_raw, page_ref_raw, raw_json
        ) VALUES ('legacy:entry:1', 1, 'editorial_page', '10', '10', '{}');
        INSERT INTO alphabetical_scripture_refs (
            entry_key, ref_order, ref_role, ref_raw, raw_json
        ) VALUES ('legacy:entry:1', 1, 'citation', 'Gen. 1, 1', '{}');
        """
    )

    init_schema(con)

    columns = {
        row["name"] for row in con.execute("PRAGMA table_info(alphabetical_refs)").fetchall()
    }
    link = con.execute(
        "SELECT scripture_ref_order FROM alphabetical_refs WHERE entry_key = 'legacy:entry:1'"
    ).fetchone()
    old_index = con.execute(
        """SELECT 1 FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_alpha_scripture_entry_order_role'"""
    ).fetchone()
    new_index = con.execute(
        """SELECT 1 FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_alpha_scripture_entry_order'"""
    ).fetchone()
    con.close()

    assert "scripture_ref_order" in columns
    assert link["scripture_ref_order"] == 1
    assert old_index is None
    assert new_index is not None
