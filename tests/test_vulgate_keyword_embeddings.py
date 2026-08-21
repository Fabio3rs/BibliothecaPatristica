from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.vulgate_clementine import (
    VULGATE_EMBEDDING_PROFILE,
    VulgateClementine,
    enrich_keyword,
)
from scripts.audit_keyword_scripture_enrichment import audit_keywords
from tools import generate_keyword_embeddings as embeddings_module


def _write_bible_json(path: Path) -> Path:
    payload = {
        "books": [
            {
                "name": "Psalms",
                "chapters": [
                    {
                        "chapter": 42,
                        "verses": [
                            {"verse": 1, "text": "Psalmus David. Judica me, Deus."},
                            {"verse": 2, "text": "Quia tu es, Deus, fortitudo mea."},
                            {"verse": 3, "text": "Emitte lucem tuam et veritatem tuam."},
                        ],
                    },
                    {
                        "chapter": 109,
                        "verses": [
                            {"verse": 1, "text": "Dixit Dominus Domino meo."},
                        ],
                    },
                ],
            },
            {
                "name": "I Maccabees",
                "chapters": [
                    {
                        "chapter": 1,
                        "verses": [{"verse": 1, "text": "Et factum est postquam."}],
                    }
                ],
            },
            {
                "name": "Isaiah",
                "chapters": [
                    {
                        "chapter": 9,
                        "verses": [
                            {"verse": 1, "text": "Primo tempore alleviata est."},
                            {"verse": 2, "text": "Populus qui ambulabat in tenebris."},
                        ],
                    }
                ],
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bible(tmp_path: Path) -> VulgateClementine:
    return VulgateClementine.from_json(_write_bible_json(tmp_path / "bible.json"))


def _record(
    book: str,
    chapter: int,
    verse: str | None = None,
    alt_number: int | None = None,
) -> dict:
    return {
        "book_canonical": book,
        "number": chapter,
        "verse": verse,
        "alt_number": alt_number,
        "normalized": f"{book} {chapter}",
    }


def test_lookup_returns_full_chapter_and_exact_verse_range(tmp_path: Path) -> None:
    bible = _bible(tmp_path)

    chapter = bible.lookup("Salmos", 42)
    verses = bible.lookup("Salmos", 42, "2-3")

    assert chapter.ok
    assert chapter.text == (
        "Psalmus David. Judica me, Deus. "
        "Quia tu es, Deus, fortitudo mea. "
        "Emitte lucem tuam et veritatem tuam."
    )
    assert verses.text == (
        "Quia tu es, Deus, fortitudo mea. "
        "Emitte lucem tuam et veritatem tuam."
    )


def test_lookup_uses_explicit_names_instead_of_parser_book_index(tmp_path: Path) -> None:
    bible = _bible(tmp_path)

    result = bible.lookup_record(_record("I Macabeus", 1, "1"))

    assert result.ok
    assert result.book == "I Maccabees"
    assert result.text == "Et factum est postquam."


def test_psalm_parenthetical_number_is_the_clementine_chapter(tmp_path: Path) -> None:
    bible = _bible(tmp_path)

    result = bible.lookup_record(_record("Salmos", 110, alt_number=109))

    assert result.ok
    assert result.chapter == 109
    assert result.text == "Dixit Dominus Domino meo."


def test_keyword_enrichment_is_all_or_nothing(tmp_path: Path) -> None:
    bible = _bible(tmp_path)
    records = [_record("Salmos", 42, "2"), _record("Isaías", 9, "1-3")]

    result = enrich_keyword("Salmos 42,2; Isaías 9,1-3", records, bible)

    assert not result.enriched
    assert result.embedding_text == "Salmos 42,2; Isaías 9,1-3"
    assert result.lookups[-1].status == "verse_missing"


def _keyword_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY,
            keyword_original TEXT NOT NULL,
            is_noise INTEGER DEFAULT 0,
            is_scripture_citation INTEGER DEFAULT 0,
            hdbscan_group_id INTEGER
        );
        """
    )
    embeddings_module.ensure_embedding_schema(con)
    return con


def test_embedding_refresh_is_selective_and_idempotent(tmp_path: Path, monkeypatch) -> None:
    con = _keyword_db()
    con.executemany(
        "INSERT INTO keywords (id, keyword_original, is_scripture_citation) VALUES (?, ?, ?)",
        [
            (1, "Salmos 42", 1),
            (2, "Cristologia", 0),
            (3, "Isaías 9,1", 1),
            (4, "Trindade", 0),
        ],
    )
    old_blob = embeddings_module.floats_to_blob([9.0, 9.0])
    con.executemany(
        """
        INSERT INTO keyword_embedding
            (keyword_id, model, embedding_dim, embedding, prompt_text, embedding_hash)
        VALUES (?, 'model', 2, ?, 'old', 'old-hash')
        """,
        [(1, old_blob), (2, old_blob)],
    )
    con.commit()

    def parser(keyword: str):
        return {
            "Salmos 42": [_record("Salmos", 42)],
            "Isaías 9,1": [_record("Isaías", 9, "1")],
        }.get(keyword, [])

    calls: list[list[str]] = []

    def fake_embed_batch(texts, model, ollama_url):
        batch = list(texts)
        calls.append(batch)
        return [[float(index), 1.0] for index, _ in enumerate(batch, start=1)]

    monkeypatch.setattr(embeddings_module, "embed_batch", fake_embed_batch)
    bible = _bible(tmp_path)

    total, batches, _ = embeddings_module.ingest_embeddings(
        con,
        model="model",
        ollama_url="unused",
        batch_size=10,
        limit=None,
        start_after=None,
        bible=bible,
        citation_parser=parser,
    )

    assert (total, batches) == (3, 1)
    assert calls == [
        [
            "Salmos 42\nPsalmus David. Judica me, Deus. "
            "Quia tu es, Deus, fortitudo mea. "
            "Emitte lucem tuam et veritatem tuam.",
            "Isaías 9,1\nPrimo tempore alleviata est.",
            "Trindade",
        ]
    ]
    rows = {
        row["keyword_id"]: row
        for row in con.execute(
            "SELECT keyword_id, input_hash, input_profile FROM keyword_embedding"
        )
    }
    assert rows[1]["input_profile"] == VULGATE_EMBEDDING_PROFILE
    assert rows[2]["input_hash"] is None
    assert rows[3]["input_profile"] == VULGATE_EMBEDDING_PROFILE
    assert rows[4]["input_profile"] == embeddings_module.PLAIN_EMBEDDING_PROFILE

    second = embeddings_module.ingest_embeddings(
        con,
        model="model",
        ollama_url="unused",
        batch_size=10,
        limit=None,
        start_after=None,
        bible=bible,
        citation_parser=parser,
    )
    assert second[0:2] == (0, 0)
    assert len(calls) == 1


def test_embedding_prompt_contains_only_keyword_and_literal_text(tmp_path: Path) -> None:
    prepared = embeddings_module.prepare_embedding_input(
        "Salmos 42",
        is_scripture_citation=True,
        bible=_bible(tmp_path),
        citation_parser=lambda _: [_record("Salmos", 42)],
    )

    prompt = embeddings_module.build_embedding_prompt(prepared.text)

    assert prompt.endswith(
        "Query: Salmos 42\n"
        "Psalmus David. Judica me, Deus. "
        "Quia tu es, Deus, fortitudo mea. "
        "Emitte lucem tuam et veritatem tuam."
    )
    assert "Texto da Vulgata" not in prompt


def test_schema_migration_preserves_legacy_rows_and_adds_input_metadata() -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE keywords (id INTEGER PRIMARY KEY, hdbscan_group_id INTEGER);
        CREATE TABLE keyword_embedding (
            id INTEGER PRIMARY KEY,
            keyword_id INTEGER NOT NULL REFERENCES keywords(id),
            model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            prompt_text TEXT,
            ranked_from_model INTEGER,
            embedding_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX legacy_unique_hash ON keyword_embedding(embedding_hash);
        INSERT INTO keywords (id) VALUES (1);
        INSERT INTO keyword_embedding
            (keyword_id, model, embedding_dim, embedding, embedding_hash)
        VALUES (1, 'model', 1, X'00000000', 'legacy');
        """
    )

    embeddings_module.ensure_embedding_schema(con)

    columns = {row[1] for row in con.execute("PRAGMA table_info('keyword_embedding')")}
    assert {"input_hash", "input_profile"} <= columns
    assert con.execute("SELECT COUNT(*) FROM keyword_embedding").fetchone()[0] == 1
    assert not embeddings_module._has_unique_hash_index(con)


def test_audit_reports_parser_and_lookup_failures_without_writes(tmp_path: Path) -> None:
    con = _keyword_db()
    con.executemany(
        "INSERT INTO keywords (id, keyword_original, is_scripture_citation) VALUES (?, ?, ?)",
        [
            (1, "Salmos 42", 1),
            (2, "Isaías 9,3", 1),
            (3, "Isaías 9,1", 0),
            (4, "Cristologia", 0),
        ],
    )
    con.commit()

    def parser(keyword: str):
        return {
            "Salmos 42": [_record("Salmos", 42)],
            "Isaías 9,3": [_record("Isaías", 9, "3")],
            "Isaías 9,1": [_record("Isaías", 9, "1")],
        }.get(keyword, [])

    before = con.total_changes
    output = StringIO()
    summary = audit_keywords(
        con,
        _bible(tmp_path),
        citation_parser=parser,
        output=output,
    )

    assert con.total_changes == before
    assert summary == {
        "checked": 4,
        "flagged": 2,
        "parser_positive": 3,
        "resolved": 2,
        "failure_rows": 2,
        "flag_mismatches": 1,
        "status_counts": {
            "lookup_failed": 1,
            "not_scripture": 1,
            "parser_positive_flag_false": 1,
            "resolved": 2,
        },
        "lookup_status_counts": {"verse_missing": 1},
        "only_flagged": False,
    }
    reports = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [report["keyword_id"] for report in reports] == [2, 3]
