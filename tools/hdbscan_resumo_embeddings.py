#!/usr/bin/env python3
"""UMAP + HDBSCAN para embeddings dos resumos (página/global).

- Lê embeddings das tabelas resumo_*_embedding
- Reduz dimensionalidade com UMAP (cosine)
- Agrupa com HDBSCAN
- Grava clusters auxiliares e scores (probabilidade de membership, outlier_score) e
  metadados de persistência por cluster. Atualiza colunas *_hdbscan_group_id na tabela resumos.

Exemplo:
    python tools/hdbscan_resumo_embeddings.py --kind both --model qwen3-embedding:8b \
        --umap-components-page 15 --umap-components-global 5 --min-cluster-size 100
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import numpy as np
import pandas as pd
import tqdm
import umap
from hdbscan import HDBSCAN

from resumo_embedding_utils import (
    connect_db,
    ensure_resumo_embedding_tables,
    ensure_resumos_embedding_schema,
    load_embeddings,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Clustering UMAP+HDBSCAN para embeddings de resumos.")
    p.add_argument("--db", type=Path, default=Path("data/patristica_resumos.db"))
    p.add_argument("--model", help="Filtra embeddings por modelo (ex.: qwen3-embedding:8b)")
    p.add_argument("--chunksize", type=int, default=50000)
    p.add_argument("--limit", type=int, default=0, help="Limite opcional de embeddings (0 = todos)")
    p.add_argument("--kind", choices=["page", "global", "both"], default="both")
    p.add_argument("--umap-components-page", type=int, default=10)
    p.add_argument("--umap-components-global", type=int, default=10)
    p.add_argument("--umap-neighbors", type=int, default=100)
    p.add_argument("--min-cluster-size", type=int, default=10, help="Fallback se específico não for informado.")
    p.add_argument("--min-cluster-size-page", type=int, default=3, help="Override para resumo_pagina.")
    p.add_argument("--cluster-selection-epsilon", type=float, default=0.1, help="Epsilon para seleção de cluster.")
    p.add_argument("--min-cluster-size-global", type=int, default=10, help="Override para resumo_global.")
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument("--low-memory", action="store_true", help="Passa low_memory=True ao UMAP")
    return p


def run_umap(X: np.ndarray, n_components: int, n_neighbors: int, low_memory: bool) -> np.ndarray:
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        metric="cosine",
        low_memory=low_memory,
        n_jobs=-1,
        verbose=True,
    )
    return reducer.fit_transform(X)


def run_hdbscan(X_reduced: np.ndarray, min_cluster_size: int, min_samples: int, cluster_selection_epsilon: float = 0.0) -> HDBSCAN:
    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        core_dist_n_jobs=-1,
        cluster_selection_epsilon=cluster_selection_epsilon,
    )
    print(clusterer.get_params())
    clusterer.fit(X_reduced)
    return clusterer


def save_cluster_table(
    con: sqlite3.Connection,
    kind: str,
    rows_meta: List[Tuple[str, int]],
    labels: np.ndarray,
    probabilities: np.ndarray,
    outlier_scores: np.ndarray,
    cluster_persistence: np.ndarray,
) -> None:
    table = "resumo_pagina_clusters" if kind == "page" else "resumo_global_clusters"
    meta_table = "resumo_pagina_cluster_meta" if kind == "page" else "resumo_global_cluster_meta"

    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} (
            documento TEXT NOT NULL,
            pagina_num INTEGER NOT NULL,
            cluster_id INTEGER,
            membership_probability REAL,
            outlier_score REAL,
            PRIMARY KEY(documento, pagina_num)
        )
        """
    )
    data = [
        (doc, int(pg), int(lbl), float(prob), float(outlier))
        for (doc, pg), lbl, prob, outlier in zip(
            rows_meta, labels.tolist(), probabilities.tolist(), outlier_scores.tolist()
        )
    ]
    con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?)", data)

    con.execute(f"DROP TABLE IF EXISTS {meta_table}")
    con.execute(
        f"""
        CREATE TABLE {meta_table} (
            cluster_id INTEGER PRIMARY KEY,
            cluster_persistence REAL
        )
        """
    )
    cluster_meta = [(int(idx), float(p)) for idx, p in enumerate(cluster_persistence.tolist())]
    con.executemany(f"INSERT INTO {meta_table} VALUES (?, ?)", cluster_meta)


def update_resumos_clusters(con: sqlite3.Connection, kind: str, rows_meta: List[Tuple[str, int]], labels: np.ndarray) -> None:
    column = "resumo_pagina_hdbscan_group_id" if kind == "page" else "resumo_global_hdbscan_group_id"
    payload = []
    for (doc, pg), lbl in zip(rows_meta, labels.tolist()):
        val = None if lbl == -1 else int(lbl)
        payload.append((val, doc, int(pg)))
    con.executemany(
        f"UPDATE resumos SET {column} = ? WHERE documento = ? AND pagina_num = ?",
        payload,
    )


def summarize(labels: np.ndarray, kind: str) -> None:
    total = len(labels)
    noise = int(np.sum(labels == -1))
    print(f"{kind}: total={total} noise={noise} ({noise/total:.2%}) clusters={len(set(labels)) - (1 if -1 in labels else 0)}")
    # Top clusters
    counts = {}
    for lbl in labels.tolist():
        counts[lbl] = counts.get(lbl, 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"{kind}: top clusters -> {top}")


def process_kind(
    con: sqlite3.Connection,
    kind: str,
    args: argparse.Namespace,
) -> None:
    print(f"=== {kind} ===")
    rows_meta, X = load_embeddings(
        con,
        kind=kind,
        model=args.model,
        chunksize=args.chunksize,
        limit=args.limit,
    )
    components = args.umap_components_page if kind == "page" else args.umap_components_global

    t0 = time.time()
    X_reduced = run_umap(X, n_components=components, n_neighbors=args.umap_neighbors, low_memory=args.low_memory)
    t1 = time.time()
    if kind == "page":
        min_cluster_size = args.min_cluster_size_page or args.min_cluster_size
    else:
        min_cluster_size = args.min_cluster_size_global or args.min_cluster_size
    clusterer = run_hdbscan(X_reduced, min_cluster_size=min_cluster_size, min_samples=args.min_samples, cluster_selection_epsilon=args.cluster_selection_epsilon)
    t2 = time.time()

    save_cluster_table(
        con,
        kind,
        rows_meta,
        clusterer.labels_,
        clusterer.probabilities_,
        clusterer.outlier_scores_,
        clusterer.cluster_persistence_,
    )
    update_resumos_clusters(con, kind, rows_meta, clusterer.labels_)
    con.commit()

    summarize(clusterer.labels_, kind)
    print(f"{kind}: UMAP {t1 - t0:.1f}s | HDBSCAN {t2 - t1:.1f}s")


def main() -> None:
    args = build_parser().parse_args()
    with connect_db(args.db) as con:
        ensure_resumos_embedding_schema(con)
        ensure_resumo_embedding_tables(con)

        kinds: Iterable[str]
        if args.kind == "both":
            kinds = ["page", "global"]
        else:
            kinds = [args.kind]

        for kind in kinds:
            process_kind(con, kind, args)


if __name__ == "__main__":
    main()
