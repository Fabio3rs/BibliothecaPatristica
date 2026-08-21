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
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.resumo_embedding_utils import (
        connect_db,
        ensure_resumo_embedding_tables,
        ensure_resumos_embedding_schema,
        load_embeddings,
    )
except ImportError:
    from resumo_embedding_utils import (  # type: ignore[no-redef]
        connect_db,
        ensure_resumo_embedding_tables,
        ensure_resumos_embedding_schema,
        load_embeddings,
    )
from resumo_v2 import init_v2_schema, sha256_text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clustering UMAP+HDBSCAN para embeddings de resumos."
    )
    p.add_argument("--db", type=Path, default=Path("data/patristica_resumos.db"))
    p.add_argument(
        "--model", help="Filtra embeddings por modelo (ex.: qwen3-embedding:8b)"
    )
    p.add_argument("--chunksize", type=int, default=50000)
    p.add_argument(
        "--limit", type=int, default=0, help="Limite opcional de embeddings (0 = todos)"
    )
    p.add_argument("--kind", choices=["page", "global", "both", "v2"], default="both")
    p.add_argument("--umap-components-page", type=int, default=10)
    p.add_argument("--umap-components-global", type=int, default=10)
    p.add_argument("--umap-components-v2", type=int, default=15)
    p.add_argument("--umap-neighbors", type=int, default=70)
    p.add_argument(
        "--min-cluster-size",
        type=int,
        default=10,
        help="Fallback se específico não for informado.",
    )
    p.add_argument(
        "--min-cluster-size-page",
        type=int,
        default=3,
        help="Override para resumo_pagina.",
    )
    p.add_argument(
        "--cluster-selection-epsilon",
        type=float,
        default=0.1,
        help="Epsilon para seleção de cluster.",
    )
    p.add_argument(
        "--min-cluster-size-global",
        type=int,
        default=5,
        help="Override para resumo_global.",
    )
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument(
        "--low-memory", action="store_true", help="Passa low_memory=True ao UMAP"
    )
    p.add_argument(
        "--save-umap",
        action="store_true",
        default=True,
        help="Salva embeddings UMAP reduzidos em resumo_*_embedding_reduced (padrão: ativo).",
    )
    p.add_argument(
        "--no-save-umap",
        dest="save_umap",
        action="store_false",
        help="Desativa gravação dos embeddings UMAP reduzidos.",
    )
    return p


def run_umap(
    X: np.ndarray, n_components: int, n_neighbors: int, low_memory: bool
) -> np.ndarray:
    try:
        import umap
    except ImportError as exc:
        raise RuntimeError("Instale umap-learn para executar o clustering") from exc
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        metric="cosine",
        low_memory=low_memory,
        n_jobs=-1,
        verbose=True,
    )
    return reducer.fit_transform(X)


def save_umap_reduced(
    con: sqlite3.Connection,
    kind: str,
    rows_meta: List[Tuple[str, int]],
    X_reduced: np.ndarray,
    n_components: int,
    umap_neighbors: int,
) -> None:
    """Salva embeddings UMAP reduzidos em resumo_{page|global}_embedding_reduced.

    Esquema:
        documento TEXT, pagina_num INTEGER  — chave primária
        n_components INTEGER               — dimensionalidade do UMAP
        umap_neighbors INTEGER             — n_neighbors usado
        embedding BLOB                     — vetor float32 serializado (numpy tobytes)

    Útil para calcular top-K vizinhos mais tarde sem reprocessar os 4096-d.
    Tamanho estimado: 288k × n_components × 4 bytes ≈ 5-6 MB para n_components=5.
    """
    table = (
        "resumo_pagina_embedding_reduced"
        if kind == "page"
        else "resumo_global_embedding_reduced"
    )

    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} (
            documento      TEXT    NOT NULL,
            pagina_num     INTEGER NOT NULL,
            n_components   INTEGER NOT NULL,
            umap_neighbors INTEGER NOT NULL,
            embedding      BLOB    NOT NULL,
            PRIMARY KEY (documento, pagina_num)
        )
        """
    )
    # Converte para float32 para economizar espaço (4 bytes/dim vs 8 de float64)
    X_f32 = X_reduced.astype(np.float32)
    data = [
        (doc, int(pg), n_components, umap_neighbors, X_f32[i].tobytes())
        for i, (doc, pg) in enumerate(rows_meta)
    ]
    con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?)", data)
    print(
        f"[save_umap_reduced] {table}: {len(data)} linhas gravadas ({n_components}d float32)."
    )


def run_hdbscan(
    X_reduced: np.ndarray,
    min_cluster_size: int,
    min_samples: int,
    cluster_selection_epsilon: float = 0.0,
) -> HDBSCAN:
    try:
        from hdbscan import HDBSCAN
    except ImportError as exc:
        raise RuntimeError("Instale hdbscan para executar o clustering") from exc
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
    meta_table = (
        "resumo_pagina_cluster_meta" if kind == "page" else "resumo_global_cluster_meta"
    )

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
    cluster_meta = [
        (int(idx), float(p)) for idx, p in enumerate(cluster_persistence.tolist())
    ]
    con.executemany(f"INSERT INTO {meta_table} VALUES (?, ?)", cluster_meta)


def update_resumos_clusters(
    con: sqlite3.Connection,
    kind: str,
    rows_meta: List[Tuple[str, int]],
    labels: np.ndarray,
) -> None:
    column = (
        "resumo_pagina_hdbscan_group_id"
        if kind == "page"
        else "resumo_global_hdbscan_group_id"
    )
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
    print(
        f"{kind}: total={total} noise={noise} ({noise/total:.2%}) clusters={len(set(labels)) - (1 if -1 in labels else 0)}"
    )
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
    components = (
        args.umap_components_page if kind == "page" else args.umap_components_global
    )

    t0 = time.time()
    X_reduced = run_umap(
        X,
        n_components=components,
        n_neighbors=args.umap_neighbors,
        low_memory=args.low_memory,
    )
    t1 = time.time()

    if args.save_umap:
        save_umap_reduced(
            con, kind, rows_meta, X_reduced, components, args.umap_neighbors
        )
        con.commit()

    if kind == "page":
        min_cluster_size = args.min_cluster_size_page or args.min_cluster_size
    else:
        min_cluster_size = args.min_cluster_size_global or args.min_cluster_size
    clusterer = run_hdbscan(
        X_reduced,
        min_cluster_size=min_cluster_size,
        min_samples=args.min_samples,
        cluster_selection_epsilon=args.cluster_selection_epsilon,
    )
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


def load_v2_embeddings(
    con: sqlite3.Connection, model: str | None, limit: int
) -> tuple[list[sqlite3.Row], np.ndarray]:
    sql = """
        SELECT id,documento,pagina_num,embedding_dim,embedding,
               embedding_model,embedding_source_hash
          FROM resumo_generations
         WHERE is_current=1 AND status IN ('valid','metadata_pending')
           AND embedding IS NOT NULL
    """
    params: list[object] = []
    if model:
        sql += " AND embedding_model=?"
        params.append(model)
    sql += " ORDER BY documento,pagina_num"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = list(con.execute(sql, params))
    if not rows:
        raise RuntimeError("Nenhum embedding v2 promovido disponível")
    dimensions = {int(row["embedding_dim"]) for row in rows}
    models = {str(row["embedding_model"]) for row in rows}
    if len(dimensions) != 1 or len(models) != 1:
        raise RuntimeError(
            f"Embeddings v2 incompatíveis: dimensões={dimensions}, modelos={models}"
        )
    dim = next(iter(dimensions))
    matrix = np.stack(
        [np.frombuffer(row["embedding"], dtype=np.float32, count=dim) for row in rows]
    )
    return rows, matrix


def save_v2_cluster_run(
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    reduced: np.ndarray,
    clusterer: HDBSCAN,
    args: argparse.Namespace,
) -> int:
    params = {
        "umap_components": args.umap_components_v2,
        "umap_neighbors": args.umap_neighbors,
        "min_cluster_size": args.min_cluster_size_page or args.min_cluster_size,
        "min_samples": args.min_samples,
        "cluster_selection_epsilon": args.cluster_selection_epsilon,
        "low_memory": bool(args.low_memory),
    }
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    set_hash = sha256_text(
        *(
            f"{row['id']}:{row['embedding_source_hash']}:{row['embedding_model']}"
            for row in rows
        )
    )
    model = str(rows[0]["embedding_model"])
    params_hash = sha256_text(params_json)
    con.execute(
        """
        INSERT INTO resumo_cluster_runs
            (kind,embedding_model,embedding_set_hash,params_json,params_hash,status)
        VALUES ('v2-page',?,?,?,?, 'running')
        ON CONFLICT(kind,embedding_model,embedding_set_hash,params_hash)
        DO UPDATE SET status='running', params_json=excluded.params_json
        """,
        (model, set_hash, params_json, params_hash),
    )
    run = con.execute(
        """
        SELECT id FROM resumo_cluster_runs
         WHERE kind='v2-page' AND embedding_model=? AND embedding_set_hash=? AND params_hash=?
        """,
        (model, set_hash, params_hash),
    ).fetchone()
    run_id = int(run["id"])
    con.execute("DELETE FROM resumo_generation_clusters WHERE cluster_run_id=?", (run_id,))
    reduced_f32 = reduced.astype(np.float32)
    con.executemany(
        """
        INSERT INTO resumo_generation_clusters
            (cluster_run_id,generation_id,documento,pagina_num,cluster_id,
             membership_probability,outlier_score,reduced_dim,reduced_embedding)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                run_id,
                int(row["id"]),
                row["documento"],
                int(row["pagina_num"]),
                None if int(label) == -1 else int(label),
                float(probability),
                float(outlier),
                reduced_f32.shape[1],
                reduced_f32[index].tobytes(),
            )
            for index, (row, label, probability, outlier) in enumerate(
                zip(
                    rows,
                    clusterer.labels_.tolist(),
                    clusterer.probabilities_.tolist(),
                    clusterer.outlier_scores_.tolist(),
                )
            )
        ],
    )
    con.execute("UPDATE resumo_cluster_runs SET status='completed' WHERE id=?", (run_id,))
    con.commit()
    return run_id


def process_v2(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows, matrix = load_v2_embeddings(con, args.model, args.limit)
    t0 = time.time()
    reduced = run_umap(
        matrix,
        n_components=args.umap_components_v2,
        n_neighbors=args.umap_neighbors,
        low_memory=args.low_memory,
    )
    clusterer = run_hdbscan(
        reduced,
        min_cluster_size=args.min_cluster_size_page or args.min_cluster_size,
        min_samples=args.min_samples,
        cluster_selection_epsilon=args.cluster_selection_epsilon,
    )
    run_id = save_v2_cluster_run(con, rows, reduced, clusterer, args)
    summarize(clusterer.labels_, "v2")
    print(f"v2: cluster_run={run_id}, páginas={len(rows)}, tempo={time.time()-t0:.1f}s")


def main() -> None:
    args = build_parser().parse_args()
    with connect_db(args.db) as con:
        if args.kind == "v2":
            init_v2_schema(con)
            process_v2(con, args)
            return
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
