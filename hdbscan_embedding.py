#!/usr/bin/env python3
"""
Clustering de keywords por embeddings:
- Lê embeddings do SQLite (tabela keyword_embedding), opcionais filtros por modelo/limit.
- Reduz dimensionalidade com UMAP.
- Agrupa com HDBSCAN.
- Grava resultados em:
    * tabela keyword_clusters (keyword_id, cluster_id, membership_probability, outlier_score)
      substituída a cada run
    * tabela keyword_cluster_meta (cluster_id, cluster_persistence) substituída a cada run
    * coluna keywords.hdbscan_group_id (ALTER já existente nos scripts de ingest/embedding)

Uso:
  python hdbscan_embedding.py \
    --db data/patristica_keywords.db \
    --model qwen3-embedding:8b \
    --chunksize 50000 \
    --limit 0 \
    --umap-components 20 \
    --min-cluster-size 100 \
    --min-samples 15
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
import tqdm
import umap
from hdbscan import HDBSCAN


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def load_embeddings(
    con: sqlite3.Connection,
    model: str | None,
    chunksize: int,
    limit: int,
) -> Tuple[np.ndarray, np.ndarray]:
    sql = "SELECT keyword_id, embedding FROM keyword_embedding"
    params: List[object] = []
    if model:
        sql += " WHERE model = ?"
        params.append(model)
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"

    chunks = []
    print("1) Lendo embeddings do DB...")
    for chunk in tqdm.tqdm(
        pd.read_sql_query(sql, con, params=params, chunksize=chunksize),
        desc="Chunks",
    ):
        chunks.append(chunk)
    if not chunks:
        raise RuntimeError("Nenhum embedding encontrado para os filtros informados.")

    df = pd.concat(chunks, ignore_index=True)
    ids = df["keyword_id"].to_numpy(dtype=np.int64)
    print(f"Total embeddings carregados: {len(ids)}")

    print("2) Convertendo BLOB -> float32 matriz...")
    X = np.stack([np.frombuffer(b, dtype="float32") for b in df["embedding"].values])
    return ids, X


def run_umap(X: np.ndarray, n_components: int, n_neighbors: int, low_memory: bool = False) -> np.ndarray:
    print("3) UMAP (redução de dimensionalidade)...")
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        metric="cosine",
        low_memory=low_memory,
        n_jobs=-1,
        verbose=True,
    )
    return reducer.fit_transform(X)


def run_hdbscan(
    X_reduced: np.ndarray, min_cluster_size: int, min_samples: int, cluster_selection_epsilon: float = 0.0
) -> HDBSCAN:
    print("4) HDBSCAN (clustering)...")
    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        core_dist_n_jobs=-1,
       # copy=False,  # explícito: permite uso in-place para economizar memória (default atual)
       cluster_selection_epsilon=cluster_selection_epsilon,
    )
    clusterer.fit(X_reduced)
    return clusterer


def save_results(
    con: sqlite3.Connection,
    ids: np.ndarray,
    labels: np.ndarray,
    probabilities: np.ndarray,
    outlier_scores: np.ndarray,
    cluster_persistence: np.ndarray,
) -> None:
    print("5) Gravando resultados no SQLite...")
    cursor = con.cursor()
    cursor.execute("DROP TABLE IF EXISTS keyword_clusters")
    cursor.execute(
        """
        CREATE TABLE keyword_clusters (
            keyword_id INTEGER PRIMARY KEY,
            cluster_id INTEGER,
            membership_probability REAL,
            outlier_score REAL
        )
        """
    )
    data_to_save = [
        (
            int(k),
            int(lbl),
            float(prob),
            float(outlier),
        )
        for k, lbl, prob, outlier in zip(
            ids.tolist(), labels.tolist(), probabilities.tolist(), outlier_scores.tolist()
        )
    ]
    cursor.executemany(
        "INSERT INTO keyword_clusters (keyword_id, cluster_id, membership_probability, outlier_score) "
        "VALUES (?, ?, ?, ?)",
        data_to_save,
    )

    cursor.execute("DROP TABLE IF EXISTS keyword_cluster_meta")
    cursor.execute(
        """
        CREATE TABLE keyword_cluster_meta (
            cluster_id INTEGER PRIMARY KEY,
            cluster_persistence REAL
        )
        """
    )
    cluster_meta = [(int(idx), float(p)) for idx, p in enumerate(cluster_persistence.tolist())]
    cursor.executemany("INSERT INTO keyword_cluster_meta VALUES (?, ?)", cluster_meta)

    # Atualiza coluna em keywords (mantém NULL para noise = -1)
    cursor.execute(
        "UPDATE keywords SET hdbscan_group_id = NULL"
    )
    cursor.executemany(
        "UPDATE keywords SET hdbscan_group_id = ? WHERE id = ?",
        [(int(c) if c >= 0 else None, int(k)) for k, c, _, _ in data_to_save],
    )

    con.commit()
    print(
        "Concluído: keyword_clusters + keyword_cluster_meta recriadas e keywords.hdbscan_group_id atualizado."
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Clustering de keywords via HDBSCAN.")
    p.add_argument("--db", type=Path, default=Path("data/patristica_keywords.db"))
    p.add_argument("--model", help="Filtra embeddings por modelo (ex.: qwen3-embedding:8b)")
    p.add_argument("--chunksize", type=int, default=50000, help="Tamanho do chunk de leitura do DB.")
    p.add_argument("--limit", type=int, default=0, help="Limite opcional de embeddings (0 = todos).")
    p.add_argument("--umap-components", type=int, default=5, help="Número de componentes para UMAP.")
    p.add_argument("--umap-neighbors", type=int, default=100)
    p.add_argument("--min-cluster-size", type=int, default=50)
    p.add_argument("--min-samples", type=int, default=10)
    p.add_argument("--cluster-selection-epsilon", type=float, default=0.01, help="Epsilon para seleção de cluster (HDBSCAN).")
    return p


def main() -> None:
    args = build_parser().parse_args()
    with connect_db(args.db) as con:
        ids, X = load_embeddings(con, model=args.model, chunksize=args.chunksize, limit=args.limit)
        X_reduced = run_umap(X, n_components=args.umap_components, n_neighbors=args.umap_neighbors)
        clusterer = run_hdbscan(
            X_reduced,
            min_cluster_size=args.min_cluster_size,
            min_samples=args.min_samples,
            cluster_selection_epsilon=args.cluster_selection_epsilon,
        )
        save_results(
            con,
            ids,
            clusterer.labels_,
            clusterer.probabilities_,
            clusterer.outlier_scores_,
            clusterer.cluster_persistence_,
        )


if __name__ == "__main__":
    main()
