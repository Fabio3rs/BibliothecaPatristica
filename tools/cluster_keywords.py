#!/usr/bin/env python3
"""
Clusterização de keywords usando HDBSCAN com ranking RRF.
Fluxo:
 1) Garante schema atualizado (keywords/occurrence/views).
 2) Gera embeddings ausentes para keywords (usa modelo/URL padrão do embedding_playground).
 3) Roda HDBSCAN nos embeddings.
 4) Usa view_global_keyword_score para rotular clusters e outliers relevantes.
 5) Atualiza cluster_id e cluster_label na tabela keywords.
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import hdbscan  # type: ignore

# Importa helpers existentes
PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ingest_keywords_from_resumos import ensure_schema  # noqa: E402
from tools.generate_keyword_embeddings import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    embed_batch,
    floats_to_blob,
)


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def blob_to_list(blob: bytes) -> List[float]:
    if not blob:
        return []
    cnt = len(blob) // 4
    return list(struct.unpack(f"<{cnt}f", blob))


def fetch_keywords(con: sqlite3.Connection) -> List[sqlite3.Row]:
    sql = """
        SELECT id, word, embedding, cluster_id, cluster_label
        FROM keywords
        WHERE word IS NOT NULL AND TRIM(word) != ''
    """
    return list(con.execute(sql))


def generate_missing_embeddings(
    con: sqlite3.Connection,
    rows: List[sqlite3.Row],
    model: str,
    ollama_url: str,
    batch_size: int,
) -> int:
    missing = [r for r in rows if not r["embedding"]]
    print(f"Total de keywords sem embedding: {len(missing)}")
    total = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        words = [r["word"] for r in batch]
        embeddings = embed_batch(words, model=model, ollama_url=ollama_url)
        for r, vec in zip(batch, embeddings):
            blob = floats_to_blob(vec)
            con.execute(
                "UPDATE keywords SET embedding=? WHERE id=?",
                (blob, r["id"]),
            )
            total += 1
        con.commit()
    return total


def load_embeddings(rows: List[sqlite3.Row]) -> Tuple[np.ndarray, List[int], Dict[int, str]]:
    vectors: List[List[float]] = []
    ids: List[int] = []
    id_to_word: Dict[int, str] = {}
    for r in rows:
        vec = blob_to_list(r["embedding"])
        if not vec:
            continue
        kw_id = int(r["id"])
        vectors.append(vec)
        ids.append(kw_id)
        id_to_word[kw_id] = r["word"]
    if not vectors:
        return np.zeros((0, 0)), [], {}
    return np.array(vectors, dtype=np.float32), ids, id_to_word


def fetch_importance_scores(con: sqlite3.Connection) -> Dict[int, float]:
    scores: Dict[int, float] = {}
    for row in con.execute("SELECT keyword_id, importance_score FROM view_global_keyword_score"):
        scores[int(row["keyword_id"])] = float(row["importance_score"] or 0.0)
    return scores


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    arr = np.array(values)
    return float(np.percentile(arr, p))


def update_clusters(
    con: sqlite3.Connection,
    ids: List[int],
    labels: np.ndarray,
    importance: Dict[int, float],
    p80: float,
    id_to_word: Dict[int, str],
) -> Tuple[int, int, int]:
    by_cluster: Dict[int, List[int]] = {}
    for kw_id, lbl in zip(ids, labels):
        by_cluster.setdefault(int(lbl), []).append(kw_id)

    # Escolhe label do cluster pelo maior importance_score
    cluster_label_map: Dict[int, str] = {}
    for cid, kw_ids in by_cluster.items():
        if cid < 0:
            continue
        best_kw = max(kw_ids, key=lambda k: importance.get(k, 0.0))
        cluster_label_map[cid] = id_to_word.get(best_kw, f"cluster_{cid}")

    outlier_relevantes = 0
    ruidos = 0
    clusters = len([c for c in by_cluster if c >= 0])

    for kw_id, lbl in zip(ids, labels):
        lbl_int = int(lbl)
        if lbl_int == -1:
            imp = importance.get(kw_id, 0.0)
            label = "Outlier_Relevante" if imp >= p80 else "Ruído"
            if label == "Outlier_Relevante":
                outlier_relevantes += 1
            else:
                ruidos += 1
            con.execute(
                "UPDATE keywords SET cluster_id=?, cluster_label=? WHERE id=?",
                (lbl_int, label, kw_id),
            )
            continue
        label = cluster_label_map.get(lbl_int, f"Cluster_{lbl_int}")
        con.execute(
            "UPDATE keywords SET cluster_id=?, cluster_label=? WHERE id=?",
            (lbl_int, label, kw_id),
        )
    con.commit()
    return clusters, outlier_relevantes, ruidos


def main() -> None:
    ap = argparse.ArgumentParser(description="Clusteriza keywords com HDBSCAN.")
    ap.add_argument("--db", type=Path, default=Path("data/patristica_keywords.db"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--min-cluster-size", type=int, default=5)
    ap.add_argument("--min-samples", type=int, default=None)
    args = ap.parse_args()

    t0 = time.time()
    con = connect_db(args.db)
    ensure_schema(con)

    rows = fetch_keywords(con)
    print(f"Total de keywords: {len(rows)}")
    generated = generate_missing_embeddings(
        con, rows, model=args.model, ollama_url=args.ollama_url, batch_size=args.batch_size
    )
    if generated:
        rows = fetch_keywords(con)  # reload with embeddings

    matrix, ids, id_to_word = load_embeddings(rows)
    if matrix.shape[0] == 0:
        print("Nenhum embedding disponível para clusterizar.")
        return

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=args.min_cluster_size,
        min_samples=args.min_samples,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(matrix)

    importance = fetch_importance_scores(con)
    p80 = percentile(list(importance.values()), 80.0)

    clusters, outliers, noise = update_clusters(con, ids, labels, importance, p80, id_to_word)

    elapsed = time.time() - t0
    print(
        f"Clusterização concluída: clusters={clusters}, outliers_relevantes={outliers}, ruido={noise}, tempo={elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
