from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.export_indices_from_db import (
    TRANSLATION_LANGUAGES,
    apply_translations,
    audit_translations,
    choose_physical_page,
    collect_translatable_texts,
    compact_runtime_payload,
    compute_coverage,
    fetch_translation_map,
    merge_manifest_items,
    public_general_sections,
)


def seed_translation_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE index_strings (
            id INTEGER PRIMARY KEY,
            source_text TEXT NOT NULL UNIQUE
        );
        CREATE TABLE index_translations (
            id INTEGER PRIMARY KEY,
            string_id INTEGER NOT NULL,
            language TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            model_name TEXT NOT NULL,
            UNIQUE(string_id, language)
        );
        """
    )
    strings = {
        1: "Augustinus",
        2: "De civitate Dei",
        3: "INDEX CAPITUM",
        4: "De gratia",
        5: "vide etiam",
    }
    con.executemany(
        "INSERT INTO index_strings(id, source_text) VALUES (?, ?)",
        strings.items(),
    )
    rows = []
    for string_id, source_text in strings.items():
        for language in TRANSLATION_LANGUAGES:
            rows.append(
                (
                    string_id,
                    language,
                    f"{source_text} [{language}]",
                    "test-model",
                )
            )
    con.executemany(
        """
        INSERT INTO index_translations(
            string_id, language, translated_text, model_name
        ) VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()
    return con


def test_physical_page_never_falls_back_to_editorial_number() -> None:
    assert choose_physical_page("teste/PG001/text/page-123.txt", 599) == 123
    assert choose_physical_page(None, 599, "col. 601") is None


def test_coverage_separates_editorial_references_from_viewer_targets() -> None:
    sections = [
        {
            "entries": [
                {
                    "page_ref_raw": "III, 599",
                    "page_ref_int": 599,
                    "page_ref_col": None,
                    "editorial_reference_page": 599,
                    "target_file": None,
                    "reference_page": None,
                },
                {
                    "page_ref_raw": "col. 15",
                    "page_ref_int": None,
                    "page_ref_col": 15,
                    "editorial_reference_page": 15,
                    "target_file": "teste/PG001/text/page-012.txt",
                    "reference_page": 12,
                },
                {
                    "page_ref_raw": None,
                    "page_ref_int": None,
                    "page_ref_col": None,
                    "editorial_reference_page": None,
                    "target_file": None,
                    "reference_page": None,
                },
            ]
        }
    ]

    coverage = compute_coverage([], sections)

    assert coverage["entries_with_editorial_reference"] == 2
    assert coverage["entries_with_resolved_target_page"] == 1
    assert coverage["editorial_reference_coverage"] == 0.6667
    assert coverage["target_coverage"] == 0.3333
    assert coverage["completeness"] == coverage["target_coverage"]


def test_translation_export_enriches_every_frontend_display_field() -> None:
    con = seed_translation_db()
    works = [
        {
            "author_raw": "Augustinus",
            "title_raw": "De  civitate\nDei",
            "title_norm": "de-civitate-dei",
        }
    ]
    sections = [
        {
            "heading_raw": "INDEX CAPITUM",
            "heading_norm": "index-capitum",
            "entries": [
                {
                    "entry_raw": "De gratia",
                    "target_raw": None,
                    "normalized_target": "de-gratia",
                    "note_raw": "vide etiam",
                }
            ],
        }
    ]

    source_texts = collect_translatable_texts(works, sections)
    translation_map = fetch_translation_map(con, source_texts)
    apply_translations(works, sections, translation_map)

    assert works[0]["author_display"]["translation"]["pt-br"] == "Augustinus [pt-br]"
    assert works[0]["title_display"]["translation"]["en"] == "De civitate Dei [en]"
    assert sections[0]["heading_display"]["translation"]["fr"] == "INDEX CAPITUM [fr]"
    entry = sections[0]["entries"][0]
    assert entry["target_display"]["original"] == "De gratia"
    assert entry["target_display"]["translation"]["it"] == "De gratia [it]"
    assert entry["note_display"]["translation"]["pt-br"] == "vide etiam [pt-br]"
    con.close()


def test_translation_preflight_reports_complete_metadata() -> None:
    con = seed_translation_db()
    audit = audit_translations(con)

    assert audit.languages == TRANSLATION_LANGUAGES
    assert audit.strings_total == 5
    assert audit.rows_total == 20
    assert audit.models == ("test-model",)
    con.close()


def test_translation_preflight_rejects_serialized_object() -> None:
    con = seed_translation_db()
    con.execute(
        """
        UPDATE index_translations
        SET translated_text = ?
        WHERE string_id = 4 AND language = 'pt-br'
        """,
        ('{"pt-br":"Sobre a graça"}',),
    )
    con.commit()

    with pytest.raises(RuntimeError, match="dados estruturados serializados"):
        audit_translations(con)
    con.close()


def test_merge_manifest_items_replaces_selected_volume(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"volumes":[{"volume_id":"PG001","works_total":1},'
        '{"volume_id":"PL001","works_total":2}]}',
        encoding="utf-8",
    )

    merged = merge_manifest_items(
        manifest_path,
        [{"volume_id": "PG001", "works_total":3}],
    )

    assert merged == [
        {"volume_id": "PG001", "works_total": 3},
        {"volume_id": "PL001", "works_total": 2},
    ]


def test_runtime_payload_removes_audit_and_derived_search_fields() -> None:
    payload = {
        "schema_version": 3,
        "generated_at": "2026-08-23T00:00:00Z",
        "coverage": {"entries_total": 1},
        "relations": [],
        "render_hints": {"show_translations": True},
        "volume": {
            "volume_id": "PG001",
            "source_root": "teste/PG001",
            "created_at": "old",
            "updated_at": "new",
            "display": {"original": "PG001", "search": "pg001"},
        },
        "works": [
            {
                "work_key": "w1",
                "work_order": 1,
                "author_raw": "Clemens",
                "author_display": {"original": "Clemens"},
                "title_raw": "Epistola",
                "title_norm": "epistola",
                "title_display": {"original": "Epistola", "search": "epistola"},
                "start_file": "teste/PG001/text/page-001.txt",
                "end_file": "teste/PG001/text/page-002.txt",
                "source_section_key": "s1",
                "raw_json": {"scope_note": "Testimonium antiquum"},
            }
        ],
        "sections": [
            {
                "section_key": "s1",
                "heading_raw": "Index",
                "heading_norm": "index",
                "heading_display": {"original": "Index", "search": "index"},
                "file_start": "page-001.txt",
                "file_end": "page-002.txt",
                "raw_json": {"visible_heading": "Ordo rerum"},
                "entries": [
                    {
                        "id": 1,
                        "section_key": "s1",
                        "entry_raw": "De gratia, p. 10",
                        "target_raw": "De gratia",
                        "target_display": {
                            "original": "De gratia",
                            "search": "de-gratia",
                            "translation": {"pt-br": "Sobre a graça"},
                        },
                        "target_file": "page-010.txt",
                        "page_ref_int": 10,
                        "page_ref_col": None,
                        "note_raw": "vide",
                        "note_display": {"original": "vide"},
                        "normalized_target": "de-gratia",
                        "raw_json": {"source_line": "DE GRATIA X"},
                    }
                ],
            }
        ],
    }

    compact_runtime_payload(payload)

    assert "coverage" not in payload
    assert "render_hints" not in payload
    assert "source_root" not in payload["volume"]
    work = payload["works"][0]
    assert "raw_json" not in work
    assert "search" not in work["title_display"]
    section = payload["sections"][0]
    assert "raw_json" not in section
    assert "search" not in section["heading_display"]
    entry = section["entries"][0]
    assert "target_file" not in entry
    assert "page_ref_int" not in entry
    assert "raw_json" not in entry
    assert "normalized_target" not in entry
    assert "page_ref_col" not in entry
    assert entry["entry_raw"] == "De gratia, p. 10"
    assert "search" not in entry["target_display"]


def test_public_export_excludes_alphabetical_and_onomastic_sections() -> None:
    sections = [
        {
            "section_key": "works",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
            "raw_json": {},
        },
        {
            "section_key": "alphabetical",
            "scope_kind": "volume_front",
            "index_kind": "INDEX GENERALIS",
            "heading_raw": "INDEX IN OMNIA OPERA SANCTI AUGUSTINI",
            "raw_json": {},
        },
        {
            "section_key": "onomastic",
            "scope_kind": "volume_end",
            "index_kind": "INDEX NOMINUM",
            "heading_raw": "INDEX NOMINUM",
            "raw_json": {},
        },
    ]

    assert [item["section_key"] for item in public_general_sections(sections)] == ["works"]
