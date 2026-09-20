import sqlite3

import pytest

from tools.export_indices_from_db import audit_translations, fetch_translation_map


LANGUAGES = ("pt-br", "en", "it", "fr")


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
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
            model_name TEXT NOT NULL
        );
        INSERT INTO index_strings VALUES (1, 'Texto completo'), (2, 'Fallback latino');
        """
    )
    conn.executemany(
        """
        INSERT INTO index_translations(
            string_id, language, translated_text, model_name
        ) VALUES (1, ?, ?, 'test-model')
        """,
        [(language, f"{language}: texto") for language in LANGUAGES],
    )
    conn.commit()
    return conn


def test_translation_audit_accepts_whole_string_fallback():
    conn = make_db()

    audit = audit_translations(conn, LANGUAGES)
    translations = fetch_translation_map(
        conn, ["Texto completo", "Fallback latino"], LANGUAGES
    )

    assert audit.fallback_strings == 1
    assert audit.rows_total == 4
    assert set(translations) == {"Texto completo"}


def test_translation_audit_rejects_partial_language_set():
    conn = make_db()
    conn.execute(
        """
        INSERT INTO index_translations(
            string_id, language, translated_text, model_name
        ) VALUES (2, 'it', 'testo contaminato', 'test-model')
        """
    )

    with pytest.raises(RuntimeError, match="traduções parciais"):
        audit_translations(conn, LANGUAGES)
