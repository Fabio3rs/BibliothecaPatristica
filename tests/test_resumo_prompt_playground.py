import json
import sqlite3

import pytest

from scripts.playgrounds.resumo_prompt_playground import (
    VARIANTS,
    build_structured_prompt,
    connect_readonly,
    parse_canonical,
    parse_page_spec,
    score_output,
)


def sample_case():
    hints = {
        "exact_start_candidates": [
            {
                "author_original": "S. CLEMENS I",
                "author_pt": "São Clemente I",
                "title_original": "Epistolae ad Virgines",
                "title_pt": "Epístolas às Virgens",
            }
        ],
        "containing_work_candidates": [],
    }
    return {
        "documento": "PG001",
        "pagina_num": 175,
        "ocr_raw": "EPISTOLAE DUAE AD VIRGINES " + "texto " * 100,
        "ocr_clean": "Epistolae duae ad virgines " + "texto " * 100,
        "previous_summary_global": "Segunda Epístola aos Coríntios",
        "previous_author": "São Clemente I",
        "previous_work": "Segunda Epístola aos Coríntios",
        "index_hints_json": json.dumps(hints, ensure_ascii=False),
    }


def test_parse_page_spec():
    assert parse_page_spec("pg001:175") == ("PG001", 175)


def test_index_hint_variant_renders_catalog_hints():
    prompt = build_structured_prompt(sample_case(), VARIANTS["structured_index_hint"])
    assert "<catalog_hints>" in prompt
    assert "Epístolas às Virgens" in prompt


def test_no_context_variant_omits_previous_summary():
    prompt = build_structured_prompt(
        sample_case(), VARIANTS["structured_index_hint_no_context"]
    )
    assert "Segunda Epístola aos Coríntios" not in prompt.split("<previous_context>", 1)[1].split(
        "</previous_context>", 1
    )[0]


def test_legacy_response_maps_to_canonical_fields():
    raw = json.dumps(
        {
            "autor": "São Clemente I",
            "obra": "Epístolas às Virgens",
            "traducao_compacta": "A dedicatória apresenta a nova edição das epístolas.",
            "sintese_acumulada": "Início da edição das Epístolas às Virgens.",
        },
        ensure_ascii=False,
    )
    parsed, error = parse_canonical(raw, VARIANTS["baseline_legacy"])
    assert error == ""
    assert parsed["summary_display"].startswith("A dedicatória")


def test_false_administrative_at_indexed_start_is_heavily_penalized():
    canonical = {
        "page_kind": "administrative",
        "author": "",
        "work": "",
        "summary_display": "Conteúdo administrativo",
        "retrieval_text": "Conteúdo administrativo",
        "cumulative_summary": "contexto anterior",
    }
    score, findings = score_output(sample_case(), canonical)
    assert score <= 45
    assert "administrativo_em_inicio_indexado" in findings


def test_production_connection_is_query_only(tmp_path):
    db = tmp_path / "source.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE sample (id INTEGER)")
    with connect_readonly(db) as con:
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO sample VALUES (1)")
