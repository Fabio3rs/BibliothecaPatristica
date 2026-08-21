import json
import sqlite3

import pytest

from keyword_sanitization import (
    keyword_normalization_key,
    sanitize_keyword_item,
)
from keywords_serial import clean_keywords_structure, normalize_keywords_payload
from tools.ingest_keywords_from_resumos import ensure_schema, ingest


@pytest.mark.parametrize(
    ("raw", "expected", "issue"),
    [
        (
            "Bendizer|change_to|Abençoar",
            "Abençoar",
            "repaired_change_to_protocol",
        ),
        (
            "Castor e Pólux|Castor e Pólux|Castor e Pólux",
            "Castor e Pólux",
            "collapsed_repeated_protocol",
        ),
        ("Temas:: graça", "graça", "normalized_response_prefix"),
        ("Afetuo\u00adsas", "Afetuosas", "normalized_unicode_format"),
        ("$ Si vero", "Si vero", "normalized_leading_ocr_symbol"),
        ("num. VI", None, "removed_editorial_locator"),
        ("$Y3I", None, "removed_suspect_ocr_symbol"),
        ("Pai | Filho", None, "removed_ambiguous_pipe"),
        (
            "Trindade (REVISÃO NECESSÁRIA)",
            None,
            "removed_requires_review",
        ),
        (
            "Trinitas (não aplicável)",
            None,
            "removed_meta_placeholder",
        ),
    ],
)
def test_sanitize_keyword_item(raw, expected, issue):
    sanitized = sanitize_keyword_item(raw)

    assert sanitized.value == expected
    assert issue in sanitized.issues


def test_normalization_key_folds_ligatures_but_display_does_not():
    sanitized = sanitize_keyword_item("Pœnitentia dæmonis")

    assert sanitized.value == "Pœnitentia dæmonis"
    assert keyword_normalization_key(sanitized.value) == "poenitentia daemonis"


def test_clean_structure_deduplicates_ligature_and_expanded_forms():
    cleaned, issues = clean_keywords_structure(
        {"keywords": ["Pœnitentia dæmonis", "Poenitentia daemonis"]}
    )

    assert cleaned["keywords"] == ["Pœnitentia dæmonis"]
    assert "removed_duplicate" in issues


def test_clean_structure_preserves_non_searchable_notes():
    parsed, parse_issue = normalize_keywords_payload(
        json.dumps(
            {
                "keywords": ["Graça"],
                "notas": {"Graça": "Conferir\u200b no fac-símile."},
            },
            ensure_ascii=False,
        )
    )

    cleaned, issues = clean_keywords_structure(parsed)

    assert parse_issue is None
    assert cleaned["keywords"] == ["Graça"]
    assert cleaned["notas"] == {"Graça": "Conferir no fac-símile."}
    assert issues == []


def _source_connection(payload: dict) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE resumos (
            id INTEGER PRIMARY KEY,
            documento TEXT NOT NULL,
            pagina_num INTEGER NOT NULL,
            keywords_json TEXT NOT NULL,
            keywords_source TEXT NOT NULL,
            keywords_modelo TEXT NOT NULL
        )
        """
    )
    con.execute(
        "INSERT INTO resumos VALUES (1, 'PGTEST', 1, ?, 'tudo', 'test-model')",
        (json.dumps(payload, ensure_ascii=False),),
    )
    return con


@pytest.mark.parametrize("parse_in_sql", [False, True])
def test_ingest_routes_apply_the_same_shared_sanitization(parse_in_sql):
    src = _source_connection(
        {
            "keywords": [
                "Temas: Graça",
                "Afetuo\u00adsas",
                "Bendizer|change_to|Abençoar",
                "Trindade (REVISÃO NECESSÁRIA)",
                "$Y3I",
            ],
            "categorias": {
                "Temas": ["(não há)", "Pœnitentia"],
            },
        }
    )
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    ensure_schema(dst)

    ingest(
        src_con=src,
        dst_con=dst,
        doc=None,
        limit_pages=1,
        max_keywords_per_page=40,
        rows_batch=10,
        commit_every_batch=1,
        parse_in_sql=parse_in_sql,
        fast_unsafe=False,
        max_cache_size=None,
    )

    stored = {
        row["keyword_norm"]: row["keyword_original"]
        for row in dst.execute(
            "SELECT keyword_norm, keyword_original FROM keywords WHERE is_noise=0"
        )
    }
    assert stored == {
        "abencoar": "Abençoar",
        "afetuosas": "Afetuosas",
        "graca": "Graça",
        "poenitentia": "Pœnitentia",
    }


@pytest.mark.parametrize("parse_in_sql", [False, True])
def test_ingest_applies_keyword_limit_after_sanitization(parse_in_sql):
    src = _source_connection(
        {"keywords": ["$Y3I", "(não há)", "Graça", "Amor", "Esperança"]}
    )
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    ensure_schema(dst)

    ingest(
        src_con=src,
        dst_con=dst,
        doc=None,
        limit_pages=1,
        max_keywords_per_page=2,
        rows_batch=10,
        commit_every_batch=1,
        parse_in_sql=parse_in_sql,
        fast_unsafe=False,
        max_cache_size=None,
    )

    stored = {
        row["keyword_norm"]
        for row in dst.execute("SELECT keyword_norm FROM keywords WHERE is_noise=0")
    }
    assert stored == {"amor", "graca"}
