#!/usr/bin/env python3
"""
Gera embeddings para keywords e grava na tabela keyword_embedding (SQLite).
- Usa modelo e instruct do embedding_playground.
- Idempotente: UPSERT por (keyword_id, model); pode rodar em batches.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import struct
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Sequence, Tuple

import requests

# Garantir import do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.playgrounds.embedding_playground import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    build_instruct_query,
)
from patristica_pipeline.vulgate_clementine import (
    DEFAULT_VULGATE_JSON,
    VULGATE_EMBEDDING_PROFILE,
    VulgateClementine,
    enrich_keyword,
)


PLAIN_EMBEDDING_PROFILE = "keyword-v1"
PROMPT_TEXT = (
    "Instruct: Retrieve passages relevant to a keyword from patristic and "
    "theological texts. The keyword may be a person, work, theological theme, "
    "philosophical concept, or technical term."
)


CitationParser = Callable[[str], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class PreparedEmbeddingInput:
    text: str
    input_hash: str
    input_profile: str
    enrichment_status: str


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


SCHEMA_EMBED = """
CREATE TABLE IF NOT EXISTS keyword_embedding (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    prompt_text TEXT,
    ranked_from_model INTEGER,
    embedding_hash TEXT,
    input_hash TEXT,
    input_profile TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kw_emb_model ON keyword_embedding(keyword_id, model);
CREATE INDEX IF NOT EXISTS idx_kw_emb_hash ON keyword_embedding(embedding_hash);
"""


KW_EMBED_TABLE_TEMPLATE = """
CREATE TABLE {table_name} (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    prompt_text TEXT,
    ranked_from_model INTEGER,
    embedding_hash TEXT,
    input_hash TEXT,
    input_profile TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

KW_EMBED_BASE_COLS = (
    "id, keyword_id, model, embedding_dim, embedding, "
    "prompt_text, ranked_from_model, embedding_hash, created_at, updated_at"
)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _has_unique_hash_index(con: sqlite3.Connection) -> bool:
    """Detecta esquema antigo com UNIQUE em embedding_hash."""
    for idx in con.execute("PRAGMA index_list('keyword_embedding')"):
        if not idx[2]:  # unique flag
            continue
        idx_name = idx[1]
        cols = [row[2] for row in con.execute(f"PRAGMA index_info('{idx_name}')")]
        if cols == ["embedding_hash"]:
            return True
    return False


def _rebuild_kw_embedding_without_unique(con: sqlite3.Connection) -> None:
    tmp_table = "keyword_embedding_tmp_mig"
    con.execute(f"DROP TABLE IF EXISTS {tmp_table}")
    con.execute(KW_EMBED_TABLE_TEMPLATE.format(table_name=tmp_table))
    con.execute(
        f"INSERT INTO {tmp_table} ({KW_EMBED_BASE_COLS}) "
        f"SELECT {KW_EMBED_BASE_COLS} FROM keyword_embedding"
    )
    con.execute("DROP TABLE keyword_embedding")
    con.execute(f"ALTER TABLE {tmp_table} RENAME TO keyword_embedding")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_kw_emb_model "
        "ON keyword_embedding(keyword_id, model)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_kw_emb_hash ON keyword_embedding(embedding_hash)")


def ensure_embedding_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_EMBED)
    if _table_exists(con, "keyword_embedding") and _has_unique_hash_index(con):
        _rebuild_kw_embedding_without_unique(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info('keyword_embedding')")}
    if "input_hash" not in columns:
        con.execute("ALTER TABLE keyword_embedding ADD COLUMN input_hash TEXT")
    if "input_profile" not in columns:
        con.execute("ALTER TABLE keyword_embedding ADD COLUMN input_profile TEXT")
    # Garante coluna hdbscan_group_id em keywords para migração de rascunho
    try:
        con.execute("ALTER TABLE keywords ADD COLUMN hdbscan_group_id INTEGER")
    except sqlite3.OperationalError:
        pass
    con.commit()


def floats_to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def hash_blob(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_embedding_text(text: str) -> str:
    return (
        text.replace("(REVISÃO NECESSÁRIA)", "")
        .replace("REVISÃO NECESSÁRIA", "")
        .strip()
    )


def build_embedding_prompt(text: str) -> str:
    return build_instruct_query(clean_embedding_text(text))


def hash_embedding_input(text: str) -> str:
    return hashlib.sha256(build_embedding_prompt(text).encode("utf-8")).hexdigest()


def _default_citation_parser(keyword: str) -> Sequence[Mapping[str, Any]]:
    from scripture_ref_normalizer import extract_citations_from_value_cached

    return extract_citations_from_value_cached(
        keyword,
        source_kind="keywords",
        source_path="embedding",
        support_mode=False,
    )


def prepare_embedding_input(
    keyword: str,
    *,
    is_scripture_citation: bool,
    bible: VulgateClementine | None,
    citation_parser: CitationParser | None = None,
) -> PreparedEmbeddingInput:
    text = keyword
    profile = PLAIN_EMBEDDING_PROFILE
    status = "plain"
    if is_scripture_citation and bible is not None:
        parser = citation_parser or _default_citation_parser
        records = parser(keyword)
        enrichment = enrich_keyword(keyword, records, bible)
        text = enrichment.embedding_text
        status = enrichment.status
        if enrichment.enriched:
            profile = VULGATE_EMBEDDING_PROFILE
        else:
            failure = enrichment.lookups[-1].status if enrichment.lookups else enrichment.status
            profile = f"{VULGATE_EMBEDDING_PROFILE}:{failure}"
    return PreparedEmbeddingInput(
        text=text,
        input_hash=hash_embedding_input(text),
        input_profile=profile,
        enrichment_status=status,
    )


def _keywords_has_scripture_flag(con: sqlite3.Connection) -> bool:
    return any(
        row[1] == "is_scripture_citation"
        for row in con.execute("PRAGMA table_info('keywords')")
    )


def fetch_embedding_candidates(
    con: sqlite3.Connection,
    model: str,
    limit: int,
    start_after: int | None,
    include_existing_scripture: bool,
) -> List[sqlite3.Row]:
    scripture_expr = (
        "COALESCE(k.is_scripture_citation, 0)"
        if _keywords_has_scripture_flag(con)
        else "0"
    )
    sql = """
        SELECT k.id, k.keyword_original,
               {scripture_expr} AS is_scripture_citation,
               e.id AS embedding_id,
               e.input_hash,
               e.input_profile
        FROM keywords k
        LEFT JOIN keyword_embedding e
          ON e.keyword_id = k.id AND e.model = ?
        WHERE k.is_noise = 0
          AND (e.id IS NULL OR (? = 1 AND {scripture_expr} = 1))
    """.format(scripture_expr=scripture_expr)
    params: List[object] = [model, int(include_existing_scripture)]
    if start_after is not None:
        sql += " AND k.id > ?"
        params.append(start_after)
    sql += " ORDER BY k.id LIMIT ?"
    params.append(limit)
    return list(con.execute(sql, params))


def embed_batch(
    keywords: Iterable[str],
    model: str,
    ollama_url: str,
) -> List[List[float]]:
    payload = {
        "model": model,
        "input": [build_embedding_prompt(keyword) for keyword in keywords],
    }
    resp = requests.post(ollama_url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings")
    if embeddings is None:
        raise RuntimeError("Resposta de embedding sem campo 'embeddings'")
    return embeddings


def upsert_embedding(
    con: sqlite3.Connection,
    keyword_id: int,
    model: str,
    embedding: List[float],
    prompt_text: str,
    input_hash: str,
    input_profile: str,
) -> None:
    dim = len(embedding)
    blob = floats_to_blob(embedding)
    emb_hash = hash_blob(blob)
    con.execute(
        """
        INSERT INTO keyword_embedding
            (keyword_id, model, embedding_dim, embedding, prompt_text, embedding_hash,
             input_hash, input_profile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(keyword_id, model) DO UPDATE SET
            embedding_dim=excluded.embedding_dim,
            embedding=excluded.embedding,
            prompt_text=excluded.prompt_text,
            embedding_hash=excluded.embedding_hash,
            input_hash=excluded.input_hash,
            input_profile=excluded.input_profile,
            updated_at=CURRENT_TIMESTAMP
        """,
        (keyword_id, model, dim, blob, prompt_text, emb_hash, input_hash, input_profile),
    )


def ingest_embeddings(
    con: sqlite3.Connection,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit: int | None,
    start_after: int | None,
    bible: VulgateClementine | None = None,
    citation_parser: CitationParser | None = None,
) -> Tuple[int, int, int]:
    total_keywords = 0
    total_batches = 0
    last_id = start_after or 0

    while True:
        if limit is not None and total_keywords >= limit:
            break
        remaining = batch_size if limit is None else min(batch_size, limit - total_keywords)
        prepared_rows: list[tuple[sqlite3.Row, PreparedEmbeddingInput]] = []
        exhausted = False
        while len(prepared_rows) < remaining:
            scan_size = max(batch_size * 4, 128)
            rows = fetch_embedding_candidates(
                con,
                model,
                scan_size,
                last_id,
                include_existing_scripture=bible is not None,
            )
            if not rows:
                exhausted = True
                break
            for row in rows:
                last_id = int(row["id"])
                prepared = prepare_embedding_input(
                    row["keyword_original"],
                    is_scripture_citation=bool(row["is_scripture_citation"]),
                    bible=bible,
                    citation_parser=citation_parser,
                )
                current = (
                    row["embedding_id"] is not None
                    and row["input_hash"] == prepared.input_hash
                    and row["input_profile"] == prepared.input_profile
                )
                if not current:
                    prepared_rows.append((row, prepared))
                if len(prepared_rows) >= remaining:
                    break
            if len(prepared_rows) >= remaining:
                break
            if len(rows) < scan_size:
                exhausted = True
                break
        if not prepared_rows:
            break

        embedding_texts = [prepared.text for _, prepared in prepared_rows]
        enriched = sum(prepared.enrichment_status == "enriched" for _, prepared in prepared_rows)
        print(
            f"[batch {total_batches+1}] pedindo {len(embedding_texts)} embeddings "
            f"({enriched} enriquecidos com Vulgata)..."
        )
        embeddings = embed_batch(embedding_texts, model, ollama_url)
        if len(embeddings) != len(prepared_rows):
            raise RuntimeError(
                f"Embedding count mismatch: got {len(embeddings)} for "
                f"{len(prepared_rows)} keywords"
            )

        for (row, prepared), vec in zip(prepared_rows, embeddings):
            upsert_embedding(
                con,
                keyword_id=row["id"],
                model=model,
                embedding=vec,
                prompt_text=PROMPT_TEXT,
                input_hash=prepared.input_hash,
                input_profile=prepared.input_profile,
            )
            total_keywords += 1

        con.commit()
        total_batches += 1
        if total_batches % 10 == 0:
            print(
                f"[batch {total_batches}] total_keywords={total_keywords} last_id={last_id}"
            )
        if exhausted:
            break

    return total_keywords, total_batches, last_id


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gera embeddings para keywords faltantes.")
    p.add_argument("--db", type=Path, default=Path("data/patristica_keywords.db"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, help="Máximo de keywords a processar.")
    p.add_argument(
        "--bible-json",
        type=Path,
        default=DEFAULT_VULGATE_JSON,
        help="JSON limpo da Vulgata Clementina usado para enriquecer keywords bíblicas.",
    )
    p.add_argument(
        "--no-scripture-enrichment",
        action="store_true",
        help="Desativa a concatenação do texto Clementino e mantém o comportamento antigo.",
    )
    p.add_argument(
        "--start-after",
        type=int,
        help="Recomeça após este keyword_id (para retomar jobs).",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    bible = None
    if not args.no_scripture_enrichment:
        bible = VulgateClementine.from_json(args.bible_json)
    with connect_db(args.db) as con:
        ensure_embedding_schema(con)
        initial_changes = con.total_changes
        total_keywords, total_batches, last_id = ingest_embeddings(
            con=con,
            model=args.model,
            ollama_url=args.ollama_url,
            batch_size=args.batch_size,
            limit=args.limit,
            start_after=args.start_after,
            bible=bible,
        )
        changes = con.total_changes - initial_changes

    print(
        f"Embeddings: {total_keywords} keywords, {total_batches} batches, "
        f"last_id={last_id}, db_changes={changes}"
    )


if __name__ == "__main__":
    main()
