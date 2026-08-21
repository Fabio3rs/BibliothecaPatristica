from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.translation_augmentation import (
    LatinLexiconTool,
    build_latin_lexicon_tools,
    run_tool_guided_chat,
)


def _seed_faria(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE entry (
                lemma TEXT,
                lemma_sort TEXT,
                morph_render TEXT,
                definicao TEXT,
                notas TEXT,
                conf TEXT,
                needs_review INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO entry VALUES (
                'virtus', 'virtus', 's. fem. 3ª (-is)',
                'força; coragem; excelência moral; virtude',
                'o contexto decide o sentido', 'conf:high', 0
            )
            """
        )


def _seed_superdb(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, lang_main TEXT);
            CREATE TABLE entry (
                id INTEGER PRIMARY KEY,
                source_id INTEGER,
                lemma TEXT,
                lemma_norm TEXT,
                pos_std TEXT,
                pos_raw TEXT,
                gender_std TEXT,
                gender_raw TEXT,
                morph_class_std TEXT,
                morph_class_raw TEXT,
                head_raw TEXT,
                notes TEXT,
                needs_review INTEGER
            );
            CREATE TABLE entry_form (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER,
                form TEXT,
                form_norm TEXT
            );
            CREATE TABLE sense (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER,
                gloss TEXT
            );
            CREATE TABLE translation (
                id INTEGER PRIMARY KEY,
                sense_id INTEGER,
                lang TEXT,
                gloss TEXT
            );
            INSERT INTO source VALUES (1, 'retificado_v2', 'la');
            INSERT INTO entry VALUES (
                1, 1, 'virtus', 'virtus', 'NOUN', NULL, 'F', NULL,
                '3rd declension', NULL, 'virtus, -utis', NULL, 0
            );
            INSERT INTO entry_form VALUES (1, 1, 'virtutis', 'virtutis');
            INSERT INTO sense VALUES (1, 1, 'coragem; excelência moral; virtude');
            INSERT INTO translation VALUES (1, 1, 'pt', 'virtude');
            """
        )


def test_latin_lexicon_tool_reads_supported_dictionary(tmp_path: Path) -> None:
    _seed_faria(tmp_path / "retificado_v2.db")
    tool = LatinLexiconTool(tmp_path)

    result = tool({"lemmas": ["virtūs", "virtus"]})

    assert result["status"] == "ok"
    assert result["sources"] == ["faria"]
    assert len(result["results"]) == 1
    entry = result["results"][0]["entries"][0]
    assert entry["source"] == "faria"
    assert entry["lemma"] == "virtus"
    assert "excelência moral" in entry["definition_pt"]


def test_latin_lexicon_tool_prefers_superdb_and_resolves_inflected_form(
    tmp_path: Path,
) -> None:
    _seed_superdb(tmp_path / "superdb.sqlite")
    _seed_faria(tmp_path / "retificado_v2.db")

    result = LatinLexiconTool(tmp_path)({"lemmas": ["virtutis"]})

    assert result["sources"] == ["superdb"]
    query = result["results"][0]
    assert query["resolved_lemmas"] == ["virtutis", "virtus"]
    assert query["entries"][0]["source"] == "retificado_v2"
    assert query["entries"][0]["translations"] == "pt:virtude"


def test_build_latin_lexicon_tools_is_optional_when_no_db_exists(
    tmp_path: Path,
) -> None:
    tools, handlers = build_latin_lexicon_tools(tmp_path)
    assert tools == []
    assert handlers == {}


def test_guided_chat_executes_tool_and_returns_final_content() -> None:
    requests: list[dict] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup_latin_lexicon",
                                        "arguments": json.dumps({"lemmas": ["virtus"]}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"pt-br":"virtude"}',
                        }
                    }
                ]
            },
        ]
    )

    def request_chat(payload: dict) -> dict:
        requests.append(payload)
        return next(responses)

    result = run_tool_guided_chat(
        request_chat=request_chat,
        payload={"model": "test-model"},
        messages=[{"role": "user", "content": "virtus"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup_latin_lexicon",
                    "parameters": {"type": "object"},
                },
            }
        ],
        handlers={
            "lookup_latin_lexicon": lambda arguments: {
                "definition": "virtude",
                "query": arguments["lemmas"][0],
            }
        },
        max_tool_rounds=1,
    )

    assert result.content == '{"pt-br":"virtude"}'
    assert result.tool_calls == (
        {
            "name": "lookup_latin_lexicon",
            "arguments": {"lemmas": ["virtus"]},
            "status": "ok",
        },
    )
    assert requests[0]["tool_choice"] == "auto"
    assert "tools" not in requests[1]
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert json.loads(requests[1]["messages"][-1]["content"])["definition"] == "virtude"
