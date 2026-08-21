"""Utilitários compartilhados para embeddings e clustering dos resumos.

- Conexão SQLite com PRAGMAs seguros.
- Criação de tabelas de embeddings (página e global) com índices/triggers.
- Migração das colunas de cluster em `resumos`.
- Funções auxiliares de serialização e fetch/upsert em batch.
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_resumos_embedding_schema(con: sqlite3.Connection) -> None:
    """Garante colunas HDBSCAN na tabela resumos.

    Mantém comportamento idempotente ignorando OperationalError quando
    a coluna já existe.
    """

    for col in [
        "resumo_global_hdbscan_group_id",
        "resumo_pagina_hdbscan_group_id",
    ]:
        try:
            con.execute(f"ALTER TABLE resumos ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass


def _embedding_table_name(kind: str) -> str:
    if kind == "page":
        return "resumo_pagina_embedding"
    if kind == "global":
        return "resumo_global_embedding"
    raise ValueError(f"kind inválido: {kind}")


def ensure_resumo_embedding_tables(con: sqlite3.Connection) -> None:
    """Cria/atualiza tabelas de embedding (página/global) com índices e triggers.

    A partir de julho/2024 removemos a UNIQUE constraint em embedding_hash, pois
    embeddings idênticos podem ocorrer entre modelos ou páginas diferentes. O
    schema agora usa um índice normal em embedding_hash e faz migração automática
    se encontrar o índice único antigo.
    """

    for name in ["resumo_pagina_embedding", "resumo_global_embedding"]:
        _ensure_embedding_table(con, name)

    con.commit()


def _embedding_table_sql(table_name: str, if_not_exists: bool = True) -> str:
    clause = "IF NOT EXISTS " if if_not_exists else ""
    return f"""
    CREATE TABLE {clause}{table_name} (
        id INTEGER PRIMARY KEY,
        documento TEXT NOT NULL,
        pagina_num INTEGER NOT NULL,
        model TEXT NOT NULL,
        embedding_dim INTEGER NOT NULL,
        embedding BLOB NOT NULL,
        prompt_text TEXT,
        embedding_hash TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(documento, pagina_num, model)
    );
    """


def _ensure_embedding_indexes(con: sqlite3.Connection, table_name: str) -> None:
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_doc_page_model
            ON {table_name}(documento, pagina_num, model)
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_embedding_hash
            ON {table_name}(embedding_hash)
        """
    )
    con.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_{table_name}_updated
            AFTER UPDATE ON {table_name}
            BEGIN
                UPDATE {table_name}
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END;
        """
    )


def _has_unique_hash_index(con: sqlite3.Connection, table_name: str) -> bool:
    for idx in con.execute(f"PRAGMA index_list('{table_name}')"):
        if not idx[2]:  # unique flag
            continue
        idx_name = idx[1]
        cols = [row[2] for row in con.execute(f"PRAGMA index_info('{idx_name}')")]
        if cols == ["embedding_hash"]:
            return True
    return False


def _rebuild_without_unique_hash(con: sqlite3.Connection, table_name: str) -> None:
    temp_table = f"{table_name}_tmp_mig"
    con.execute(f"DROP TABLE IF EXISTS {temp_table}")
    con.execute(_embedding_table_sql(temp_table, if_not_exists=False))

    cols = (
        "id, documento, pagina_num, model, embedding_dim, embedding, "
        "prompt_text, embedding_hash, created_at, updated_at"
    )
    con.execute(
        f"INSERT INTO {temp_table} ({cols}) SELECT {cols} FROM {table_name}"
    )

    con.execute(f"DROP TABLE {table_name}")
    con.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")


def _ensure_embedding_table(con: sqlite3.Connection, table_name: str) -> None:
    # Cria tabela se não existir
    con.execute(_embedding_table_sql(table_name))

    # Migra DBs antigos que tinham UNIQUE em embedding_hash
    if _has_unique_hash_index(con, table_name):
        _rebuild_without_unique_hash(con, table_name)

    _ensure_embedding_indexes(con, table_name)


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------


def floats_to_blob(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def hash_blob(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Seleção de texto
# ---------------------------------------------------------------------------


def pick_page_text(row: sqlite3.Row) -> str:
    def _get(key: str) -> str:
        try:
            return row[key]  # sqlite3.Row suporta indexação por chave
        except (KeyError, IndexError, TypeError):
            return ""

    txt_clean = (_get("summary_page_clean") or "").strip()
    if txt_clean:
        return txt_clean
    return (_get("resumo_pagina") or "").strip()


def pick_global_text(row: sqlite3.Row) -> str:
    def _get(key: str) -> str:
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return ""

    author = (_get("author_detected") or "").strip()
    work = (_get("work_detected") or "").strip()
    txt_clean = (_get("summary_global_clean") or "").strip()

    if txt_clean:
        prefix_parts = []
        if author:
            prefix_parts.append(f"Autor: {author}")
        if work:
            prefix_parts.append(f"Obra: {work}")
        prefix = ". ".join(prefix_parts)
        if prefix:
            return f"{prefix}. {txt_clean}".strip()
        return txt_clean

    return (_get("resumo_global") or "").strip()


# ---------------------------------------------------------------------------
# Fetch / Upsert
# ---------------------------------------------------------------------------


def fetch_pending(
    con: sqlite3.Connection,
    kind: str,
    model: str,
    limit: int,
    start_after: tuple[str, int] | None,
) -> List[sqlite3.Row]:
    table = _embedding_table_name(kind)
    # Campos mínimos para escolher o texto
    sql = f"""
        SELECT documento, pagina_num, resumo_pagina, resumo_global,
               summary_page_clean, summary_global_clean
        FROM resumos r
        WHERE NOT EXISTS (
            SELECT 1 FROM {table} e
            WHERE e.documento = r.documento
              AND e.pagina_num = r.pagina_num
              AND e.model = ?
			  AND r.criado_em < e.updated_at
        )
    """
    params: List[object] = [model]

    if start_after:
        doc, page = start_after
        sql += " AND (r.documento > ? OR (r.documento = ? AND r.pagina_num > ?))"
        params.extend([doc, doc, page])

    # Evita enviar strings vazias para embedder
    if kind == "page":
        sql += " AND (COALESCE(summary_page_clean, '') != '' OR COALESCE(resumo_pagina, '') != '')"
    else:
        sql += " AND (COALESCE(summary_global_clean, '') != '' OR COALESCE(resumo_global, '') != '')"

    sql += " ORDER BY r.documento, r.pagina_num LIMIT ?"
    params.append(limit)

    return list(con.execute(sql, params))


def upsert_embedding(
    con: sqlite3.Connection,
    kind: str,
    documento: str,
    pagina_num: int,
    model: str,
    vec: Sequence[float],
    prompt_text: str | None,
) -> None:
    table = _embedding_table_name(kind)
    dim = len(vec)
    blob = floats_to_blob(vec)
    emb_hash = hash_blob(blob)
    con.execute(
        f"""
        INSERT INTO {table}
            (documento, pagina_num, model, embedding_dim, embedding, prompt_text, embedding_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(documento, pagina_num, model) DO UPDATE SET
            embedding_dim=excluded.embedding_dim,
            embedding=excluded.embedding,
            prompt_text=excluded.prompt_text,
            embedding_hash=excluded.embedding_hash,
            updated_at=CURRENT_TIMESTAMP
        """,
        (documento, pagina_num, model, dim, blob, prompt_text, emb_hash),
    )


# ---------------------------------------------------------------------------
# Leitura para clustering
# ---------------------------------------------------------------------------


def load_embeddings(
    con: sqlite3.Connection,
    kind: str,
    model: str | None,
    chunksize: int,
    limit: int,
) -> Tuple[List[tuple[str, int]], np.ndarray]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Instale pandas para carregar embeddings legados") from exc
    table = _embedding_table_name(kind)
    sql = f"SELECT documento, pagina_num, embedding FROM {table}"
    params: List[object] = []
    if model:
        sql += " WHERE model = ?"
        params.append(model)
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"

    rows_meta: List[tuple[str, int]] = []
    matrices: List[np.ndarray] = []

    for chunk in pd.read_sql_query(sql, con, params=params, chunksize=chunksize):
        rows_meta.extend(list(zip(chunk["documento"], chunk["pagina_num"].astype(int))))
        mats = [np.frombuffer(b, dtype="float32") for b in chunk["embedding"].values]
        matrices.append(np.stack(mats))

    if not matrices:
        raise RuntimeError("Nenhum embedding encontrado para os filtros informados.")

    X = np.concatenate(matrices, axis=0)

    # Verifica consistência dimensional
    dims = {vec.shape[0] for vec in X}
    if len(dims) != 1:
        raise RuntimeError(f"Dimensões inconsistentes nos embeddings: {sorted(dims)}")

    return rows_meta, X


__all__ = [
    "connect_db",
    "ensure_resumos_embedding_schema",
    "ensure_resumo_embedding_tables",
    "pick_page_text",
    "pick_global_text",
    "fetch_pending",
    "upsert_embedding",
    "load_embeddings",
]
