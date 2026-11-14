"""Offline testable batch runner for Qwen3-Embedding-8B residual queue.

Adheres strictly to the project's embedding protocol:
- Ollama endpoint: http://localhost:11434/api/embed
- Model: qwen3-embedding:8b
- Query format: Instruct: {task_description}\\nQuery: {query}
- Document format: bare text (no instruction prefix)
- Cache by hash in SQLite (qwen_embeddings.sqlite)
- Incremental execution and resumption
- Offline synthetic test mode (--synthetic-test) for end-to-end verification without live Ollama.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

import numpy as np

from tools.corpus_utils import PROJECT_ROOT

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/embed"
DEFAULT_MODEL = "qwen3-embedding:8b"
DEFAULT_DIMENSION = 4096
DEFAULT_TASK_INSTRUCTION = (
    "Retrieve passages relevant to a keyword from patristic and theological texts. "
    "The keyword may be a person, work, theological theme, philosophical concept, or technical term."
)


def format_instruct_query(query: str, instruction: str = DEFAULT_TASK_INSTRUCTION) -> str:
    return f"Instruct: {instruction}\nQuery: {query}"


def generate_synthetic_embedding(text: str, dimension: int = DEFAULT_DIMENSION) -> np.ndarray:
    """Generate deterministic, unit-normalized float32 vector for offline tests."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dimension).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def init_embeddings_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            hash_signature TEXT PRIMARY KEY,
            item_id TEXT,
            role TEXT NOT NULL,
            text_raw TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            model_name TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            vector_blob BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_embeddings_item ON embeddings(item_id);
        CREATE INDEX IF NOT EXISTS idx_embeddings_sha ON embeddings(text_sha256);
        CREATE INDEX IF NOT EXISTS idx_embeddings_role ON embeddings(role);

        CREATE TABLE IF NOT EXISTS runner_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            total_processed INTEGER NOT NULL,
            cache_hits INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            notes TEXT
        );
        """
    )
    conn.commit()
    return conn


def embed_batch_ollama(
    texts: list[str],
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    timeout: int = 300,
) -> list[np.ndarray]:
    import requests
    payload = {
        "model": model,
        "input": texts,
    }
    resp = requests.post(ollama_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    embeddings_raw = data.get("embeddings", [])
    vectors = []
    for vec_raw in embeddings_raw:
        arr = np.array(vec_raw, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr /= norm
        vectors.append(arr)
    return vectors


def run_embedding_batch(
    queue_db_path: Path,
    embeddings_db_path: Path,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    batch_size: int = 32,
    limit: int | None = None,
    synthetic_test: bool = False,
) -> dict[str, Any]:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    emb_conn = init_embeddings_db(embeddings_db_path)
    emb_cur = emb_conn.cursor()

    q_conn = sqlite3.connect(f"file:{queue_db_path}?mode=ro", uri=True)
    q_cur = q_conn.cursor()

    query_sql = "SELECT queue_id, item_id, volume, section, lemma, query_text, candidate_files_json, sha256_hash FROM residual_embedding_queue"
    if limit:
        query_sql += f" LIMIT {limit}"

    q_cur.execute(query_sql)
    queue_items = q_cur.fetchall()
    q_conn.close()

    print(f"Loaded {len(queue_items)} items from residual queue (synthetic_test={synthetic_test})")

    # Filter out items already cached
    pending_items = []
    cache_hits = 0
    for qid, iid, vol, sec, lemma, qtext, cand_json, hsig in queue_items:
        emb_cur.execute("SELECT 1 FROM embeddings WHERE hash_signature=?", (hsig,))
        if emb_cur.fetchone():
            cache_hits += 1
        else:
            pending_items.append((qid, iid, vol, sec, lemma, qtext, cand_json, hsig))

    print(f"Cache hits: {cache_hits}, Pending to embed: {len(pending_items)}")

    processed_count = 0
    insert_batch = []
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for i in range(0, len(pending_items), batch_size):
        chunk = pending_items[i : i + batch_size]
        texts = [item[5] for item in chunk]  # query_text

        if synthetic_test:
            vectors = [generate_synthetic_embedding(t) for t in texts]
        else:
            vectors = embed_batch_ollama(texts, ollama_url=ollama_url, model=model)

        for item, vec in zip(chunk, vectors):
            qid, iid, vol, sec, lemma, qtext, cand_json, hsig = item
            text_sha = hashlib.sha256(qtext.encode("utf-8")).hexdigest()
            vec_blob = vec.tobytes()
            dimension = len(vec)

            insert_batch.append((
                hsig,
                iid,
                "query",
                qtext,
                text_sha,
                model,
                dimension,
                vec_blob,
                now_iso,
            ))
            processed_count += 1

        if len(insert_batch) >= 100:
            emb_cur.executemany(
                """
                INSERT OR REPLACE INTO embeddings
                (hash_signature, item_id, role, text_raw, text_sha256, model_name, dimension, vector_blob, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                insert_batch,
            )
            emb_conn.commit()
            insert_batch = []

    if insert_batch:
        emb_cur.executemany(
            """
            INSERT OR REPLACE INTO embeddings
            (hash_signature, item_id, role, text_raw, text_sha256, model_name, dimension, vector_blob, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            insert_batch,
        )
        emb_conn.commit()

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mode_str = "synthetic_test" if synthetic_test else "ollama_live"
    emb_cur.execute(
        """
        INSERT INTO runner_runs(model_name, mode, total_processed, cache_hits, started_at, finished_at, notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        (model, mode_str, processed_count, cache_hits, started_at, finished_at, f"Processed {processed_count} vectors"),
    )
    emb_conn.commit()

    # Verification stats
    emb_cur.execute("SELECT count(*), avg(dimension) FROM embeddings")
    tot_stored, avg_dim = emb_cur.fetchone()

    emb_conn.close()

    result = {
        "status": "success",
        "mode": mode_str,
        "model": model,
        "dimension": int(avg_dim or DEFAULT_DIMENSION),
        "total_queued": len(queue_items),
        "cache_hits": cache_hits,
        "processed_now": processed_count,
        "total_embeddings_in_storage": tot_stored,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Qwen3-Embedding-8B Batch Runner for Residual Queue")
    parser.add_argument("--queue-db", type=Path, default=PROJECT_ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite")
    parser.add_argument("--embeddings-db", type=Path, default=PROJECT_ROOT / "data/index_catalog/qwen_embeddings.sqlite")
    parser.add_argument("--ollama-url", type=str, default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--synthetic-test", action="store_true", help="Generate synthetic deterministic embeddings for offline verification")
    args = parser.parse_args()

    res = run_embedding_batch(
        queue_db_path=args.queue_db,
        embeddings_db_path=args.embeddings_db,
        ollama_url=args.ollama_url,
        model=args.model,
        batch_size=args.batch_size,
        limit=args.limit,
        synthetic_test=args.synthetic_test,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
