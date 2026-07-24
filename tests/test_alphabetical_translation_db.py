from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.alphabetical_index_db import (
    collect_pending_volume_translations,
    connect_db,
    init_schema,
    upsert_translation_rows,
    upsert_volume,
)


def seed_minimal_volume(db_path: Path) -> None:
    with connect_db(db_path) as con:
        init_schema(con)
        upsert_volume(
            con,
            volume_id="PG001",
            collection="PG",
            source_root="/tmp/pg001/text",
            volume_label="PG001",
            notes=None,
        )
        con.execute(
            """
            INSERT INTO alphabetical_sections (
                section_key, volume_id, section_kind, heading_raw, page_start, page_end, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("pg001_s1", "PG001", "alphabetical_general", "INDEX RERUM", 1, 2, "{}"),
        )
        con.execute(
            """
            INSERT INTO alphabetical_nodes (
                node_key, section_key, parent_node_key, node_order, node_kind,
                label_raw, label_norm, label_sort, node_level, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("pg001_n1", "pg001_s1", None, 1, "heading_group", "Sanctus Petrus", None, None, 1, None, "{}"),
        )
        con.execute(
            """
            INSERT INTO alphabetical_entries (
                entry_key, section_key, parent_node_key, entry_order, entry_kind,
                lemma_raw, lemma_display, lemma_norm, lemma_sort, entry_raw, context_raw,
                heading_letter, inferred_printed_page, section_start_file, editorial_anchor_file,
                target_file_best, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pg001_e1",
                "pg001_s1",
                "pg001_n1",
                1,
                "lemma",
                "Sanctus Petrus",
                "Sanctus Petrus",
                None,
                None,
                "Sanctus Petrus. 15.",
                "Sanctus Petrus apostolus princeps.",
                "S",
                15,
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
        con.execute(
            """
            INSERT INTO alphabetical_entries (
                entry_key, section_key, parent_node_key, entry_order, entry_kind,
                lemma_raw, lemma_display, lemma_norm, lemma_sort, entry_raw, context_raw,
                heading_letter, inferred_printed_page, section_start_file, editorial_anchor_file,
                target_file_best, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pg001_e2",
                "pg001_s1",
                "pg001_n1",
                2,
                "lemma",
                "Sanctus Petrus",
                "Sanctus Petrus",
                None,
                None,
                "Sanctus Petrus. 16.",
                "Sanctus Petrus in evangelio.",
                "S",
                16,
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
        con.commit()


def test_collect_pending_volume_translations_deduplicates_by_source_text(tmp_path: Path) -> None:
    db_path = tmp_path / "alphabetical.db"
    seed_minimal_volume(db_path)

    with connect_db(db_path) as con:
        pending = collect_pending_volume_translations(con, "PG001", ["en", "fr"])

    by_identity = {
        (item["source_text"], item["source_kind"], item["section_kind"]): item for item in pending
    }
    node_item = by_identity[("Sanctus Petrus", "node_label", "alphabetical_general")]
    assert node_item["missing_languages"] == ["en", "fr"]
    assert node_item["contexts"] == [
        "INDEX RERUM",
        "alphabetical_general",
    ]
    entry_item = by_identity[("Sanctus Petrus", "entry_title", "alphabetical_general")]
    assert entry_item["contexts"] == [
        "INDEX RERUM",
        "alphabetical_general",
        "Sanctus Petrus apostolus princeps.",
    ]


def test_upsert_translation_rows_preserves_created_at_and_updates_model(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "alphabetical.db"
    seed_minimal_volume(db_path)

    import scripts.alphabetical_index_db as db_mod

    monkeypatch.setattr(db_mod, "now_iso", lambda: "2026-07-23T12:00:00+00:00")
    with connect_db(db_path) as con:
        written = upsert_translation_rows(
            con,
            source_text="Sanctus Petrus",
            source_kind="node_label",
            section_kind="alphabetical_general",
            sample_context="INDEX RERUM",
            translations={"en": "Saint Peter"},
            model_name="gpt-5-mini",
        )
    assert written == 1

    monkeypatch.setattr(db_mod, "now_iso", lambda: "2026-07-23T12:30:00+00:00")
    with connect_db(db_path) as con:
        written = upsert_translation_rows(
            con,
            source_text="Sanctus Petrus",
            source_kind="node_label",
            section_kind="alphabetical_general",
            sample_context="INDEX RERUM",
            translations={"en": "St. Peter"},
            model_name="gpt-5.1",
        )
        row = con.execute(
            """
            SELECT source_text, language, source_kind, section_kind, sample_context,
                   translated_text, model_name, created_at, updated_at
            FROM alphabetical_translations
            WHERE source_text = ? AND language = ? AND source_kind = ? AND section_kind = ?
            """,
            ("Sanctus Petrus", "en", "node_label", "alphabetical_general"),
        ).fetchone()

    assert written == 1
    assert row["source_kind"] == "node_label"
    assert row["section_kind"] == "alphabetical_general"
    assert row["sample_context"] == "INDEX RERUM"
    assert row["translated_text"] == "St. Peter"
    assert row["model_name"] == "gpt-5.1"
    assert row["created_at"] == "2026-07-23T12:00:00+00:00"
    assert row["updated_at"] == "2026-07-23T12:30:00+00:00"


def test_collect_pending_volume_translations_only_requests_missing_languages(tmp_path: Path) -> None:
    db_path = tmp_path / "alphabetical.db"
    seed_minimal_volume(db_path)

    with connect_db(db_path) as con:
        upsert_translation_rows(
            con,
            source_text="Sanctus Petrus",
            source_kind="node_label",
            section_kind="alphabetical_general",
            sample_context="INDEX RERUM",
            translations={"en": "Saint Peter"},
            model_name="gpt-5-mini",
        )
        pending = collect_pending_volume_translations(con, "PG001", ["en", "fr"])

    by_identity = {
        (item["source_text"], item["source_kind"], item["section_kind"]): item for item in pending
    }
    assert by_identity[("Sanctus Petrus", "node_label", "alphabetical_general")]["missing_languages"] == ["fr"]
    assert by_identity[("Sanctus Petrus", "entry_title", "alphabetical_general")]["missing_languages"] == ["en", "fr"]


def test_init_schema_migrates_legacy_translation_table(tmp_path: Path) -> None:
    db_path = tmp_path / "alphabetical.db"
    with connect_db(db_path) as con:
        con.execute(
            """
            CREATE TABLE alphabetical_translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                language TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                model_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO alphabetical_translations (
                source_text, language, translated_text, model_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Passio", "en", "Passion", "gpt-5-mini", "2026-07-23T12:00:00+00:00", "2026-07-23T12:00:00+00:00"),
        )
        con.commit()
        init_schema(con)
        row = con.execute(
            """
            SELECT source_text, language, source_kind, section_kind, sample_context, translated_text
            FROM alphabetical_translations
            WHERE source_text = ? AND language = ?
            """,
            ("Passio", "en"),
        ).fetchone()

    assert row["source_kind"] == "legacy"
    assert row["section_kind"] == ""
    assert row["sample_context"] is None
    assert row["translated_text"] == "Passion"
