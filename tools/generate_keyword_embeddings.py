#!/usr/bin/env python3
"""
Gera embeddings para keywords e grava na tabela keyword_embedding (SQLite).
- Usa modelo e instruct do embedding_playground.
- Idempotente: UPSERT por (keyword_id, model); pode rodar em batches.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

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
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

KW_EMBED_COLS = (
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
        f"INSERT INTO {tmp_table} ({KW_EMBED_COLS}) "
        f"SELECT {KW_EMBED_COLS} FROM keyword_embedding"
    )
    con.execute("DROP TABLE keyword_embedding")
    con.execute(f"ALTER TABLE {tmp_table} RENAME TO keyword_embedding")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_kw_emb_model ON keyword_embedding(keyword_id, model)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_kw_emb_hash ON keyword_embedding(embedding_hash)")


def ensure_embedding_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_EMBED)
    if _table_exists(con, "keyword_embedding") and _has_unique_hash_index(con):
        _rebuild_kw_embedding_without_unique(con)
    con.commit()
    # Garante coluna hdbscan_group_id em keywords para migração de rascunho
    try:
        con.execute("ALTER TABLE keywords ADD COLUMN hdbscan_group_id INTEGER")
    except sqlite3.OperationalError:
        pass


def floats_to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def hash_blob(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_pending_keywords(
    con: sqlite3.Connection,
    model: str,
    limit: int,
    start_after: int | None,
) -> List[sqlite3.Row]:
    sql = """
        SELECT id, keyword_original
        FROM keywords k
        WHERE NOT EXISTS (
            SELECT 1 FROM keyword_embedding e
            WHERE e.keyword_id = k.id AND e.model = ?
        ) AND k.is_noise = 0
    """
    params: List[object] = [model]
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
    # Remover "(REVISÃO NECESSÁRIA)" e "REVISÃO NECESSÁRIA" para não sujar o espaço vetorial
    keywords = [k.replace("(REVISÃO NECESSÁRIA)", "").replace("REVISÃO NECESSÁRIA", "").strip() for k in keywords]

    payload = {
        "model": model,
        "input": [build_instruct_query(k) for k in keywords],
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
) -> None:
    dim = len(embedding)
    blob = floats_to_blob(embedding)
    emb_hash = hash_blob(blob)
    con.execute(
        """
        INSERT INTO keyword_embedding
            (keyword_id, model, embedding_dim, embedding, prompt_text, embedding_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(keyword_id, model) DO UPDATE SET
            embedding_dim=excluded.embedding_dim,
            embedding=excluded.embedding,
            prompt_text=excluded.prompt_text,
            embedding_hash=excluded.embedding_hash,
            updated_at=CURRENT_TIMESTAMP
        """,
        (keyword_id, model, dim, blob, prompt_text, emb_hash),
    )


def ingest_embeddings(
    con: sqlite3.Connection,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit: int | None,
    start_after: int | None,
) -> Tuple[int, int, int]:
    total_keywords = 0
    total_batches = 0
    last_id = start_after or 0

    while True:
        if limit is not None and total_keywords >= limit:
            break
        remaining = None if limit is None else max(limit - total_keywords, 0)
        fetch_size = batch_size if remaining is None else min(batch_size, remaining)
        rows = fetch_pending_keywords(con, model, fetch_size, last_id)
        if not rows:
            break

        keywords = [r["keyword_original"] for r in rows]
        print(f"[batch {total_batches+1}] pedindo {len(keywords)} embeddings...")
        embeddings = embed_batch(keywords, model, ollama_url)
        if len(embeddings) != len(rows):
            raise RuntimeError(
                f"Embedding count mismatch: got {len(embeddings)} for {len(rows)} keywords"
            )

        prompt_text = (
            "Instruct: Retrieve passages relevant to a keyword from patristic and "
            "theological texts. The keyword may be a person, work, theological theme, "
            "philosophical concept, or technical term."
        )

        for row, vec in zip(rows, embeddings):
            upsert_embedding(
                con,
                keyword_id=row["id"],
                model=model,
                embedding=vec,
                prompt_text=prompt_text,
            )
            last_id = row["id"]
            total_keywords += 1

        con.commit()
        total_batches += 1
        if total_batches % 10 == 0:
            print(
                f"[batch {total_batches}] total_keywords={total_keywords} last_id={last_id}"
            )

    return total_keywords, total_batches, last_id


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gera embeddings para keywords faltantes.")
    p.add_argument("--db", type=Path, default=Path("data/patristica_keywords.db"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, help="Máximo de keywords a processar.")
    p.add_argument(
        "--start-after",
        type=int,
        help="Recomeça após este keyword_id (para retomar jobs).",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
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
        )
        changes = con.total_changes - initial_changes

    print(
        f"Embeddings: {total_keywords} keywords, {total_batches} batches, "
        f"last_id={last_id}, db_changes={changes}"
    )


if __name__ == "__main__":
    main()
