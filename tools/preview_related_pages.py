#!/usr/bin/env python3
"""Preview de páginas relacionadas via KNN nos embeddings UMAP reduzidos.

Carrega os vetores de resumo_*_embedding_reduced, monta um KDTree (ou
NearestNeighbors sklearn) e exibe os top-K vizinhos para páginas de exemplo,
permitindo avaliar a qualidade antes de emitir no JSON de publicação.

Uso:
    python tools/preview_related_pages.py \
        --db data/patristica_resumos.db \
        --kind global \
        --queries PL096:379 PG099:848 PL192:505 PL125:471 \
        --topk 8

    # ou amostra aleatória de N páginas:
    python tools/preview_related_pages.py --db data/patristica_resumos.db --random 5
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------

def load_reduced_embeddings(
    db_path: Path, kind: str
) -> Tuple[List[Tuple[str, int]], np.ndarray]:
    """Retorna (rows_meta, X) onde X tem shape (N, n_components) float32."""
    table = (
        "resumo_pagina_embedding_reduced"
        if kind == "page"
        else "resumo_global_embedding_reduced"
    )
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout = 30000;")

    rows = con.execute(
        f"SELECT documento, pagina_num, n_components, embedding FROM {table} "
        f"ORDER BY documento, pagina_num"
    ).fetchall()
    con.close()

    if not rows:
        raise RuntimeError(f"Tabela {table} está vazia ou não existe.")

    n_components = rows[0][2]
    rows_meta: List[Tuple[str, int]] = [(r[0], r[1]) for r in rows]
    X = np.frombuffer(
        b"".join(r[3] for r in rows), dtype=np.float32
    ).reshape(len(rows), n_components)
    return rows_meta, X


def load_resumos(
    db_path: Path, keys: List[Tuple[str, int]]
) -> Dict[Tuple[str, int], str]:
    """Carrega resumos para as chaves (documento, pagina_num) pedidas."""
    if not keys:
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    placeholders = ",".join("(?,?)" for _ in keys)
    flat = [x for k in keys for x in k]
    rows = con.execute(
        f"SELECT documento, pagina_num, resumo_pagina, resumo_global "
        f"FROM resumos WHERE (documento, pagina_num) IN (VALUES {placeholders})",
        flat,
    ).fetchall()
    con.close()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


# ---------------------------------------------------------------------------
# KNN
# ---------------------------------------------------------------------------

def build_index(X: np.ndarray):
    """Monta sklearn NearestNeighbors (euclidean no espaço UMAP reduzido)."""
    try:
        from sklearn.neighbors import NearestNeighbors
        nn = NearestNeighbors(n_neighbors=20, metric="euclidean", algorithm="auto", n_jobs=-1)
        nn.fit(X)
        return ("sklearn", nn)
    except ImportError:
        pass
    # fallback: scipy KDTree
    from scipy.spatial import KDTree
    return ("scipy", KDTree(X))


def query_knn(index_obj, X: np.ndarray, query_idx: int, topk: int):
    """Retorna lista de (idx_vizinho, distância), excluindo o próprio ponto."""
    kind_idx, idx = index_obj
    k = topk + 1  # +1 para descartar o próprio ponto
    if kind_idx == "sklearn":
        dists, idxs = idx.kneighbors(X[query_idx : query_idx + 1], n_neighbors=k)
        pairs = list(zip(idxs[0], dists[0]))
    else:
        dists, idxs = idx.query(X[query_idx], k=k)
        pairs = list(zip(idxs, dists))
    return [(i, d) for i, d in pairs if i != query_idx][:topk]


# ---------------------------------------------------------------------------
# CLI / display
# ---------------------------------------------------------------------------

def fmt_resumo(text: Optional[str], width: int = 110) -> str:
    if not text:
        return "(sem resumo)"
    text = text.replace("\n", " ").strip()
    return text[:width] + ("…" if len(text) > width else "")


def parse_queries(specs: List[str]) -> List[Tuple[str, int]]:
    out = []
    for s in specs:
        doc, pg = s.rsplit(":", 1)
        out.append((doc.strip(), int(pg.strip())))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Preview top-K páginas relacionadas via KNN UMAP.")
    ap.add_argument("--db", type=Path, default=Path("data/patristica_resumos.db"))
    ap.add_argument(
        "--kind",
        choices=["page", "global", "both"],
        default="global",
        help="Qual embedding usar: resumo de página, global ou ambos (side-by-side).",
    )
    ap.add_argument(
        "--queries",
        nargs="*",
        metavar="DOC:PAGINA",
        help="Páginas a consultar, ex: PL096:379 PG099:848",
    )
    ap.add_argument(
        "--random",
        type=int,
        default=0,
        metavar="N",
        help="Usar N páginas aleatórias como consulta (ignora --queries).",
    )
    ap.add_argument("--topk", type=int, default=6, help="Número de vizinhos a exibir.")
    args = ap.parse_args()

    kinds = ["page", "global"] if args.kind == "both" else [args.kind]

    # Carrega embeddings (pode ser pesado; ~22 MB por tabela)
    data: Dict[str, Tuple[List[Tuple[str, int]], np.ndarray, object]] = {}
    for k in kinds:
        print(f"[{k}] Carregando embeddings reduzidos...", end=" ", flush=True)
        rows_meta, X = load_reduced_embeddings(args.db, k)
        print(f"{len(rows_meta)} vetores ({X.shape[1]}d float32)")
        print(f"[{k}] Construindo índice KNN...", end=" ", flush=True)
        idx_obj = build_index(X)
        print(f"pronto ({idx_obj[0]}).")
        data[k] = (rows_meta, X, idx_obj)

    # Mapa posição → (doc, pg) para lookup rápido
    pos_map: Dict[str, Dict[Tuple[str, int], int]] = {}
    for k, (rows_meta, _, _) in data.items():
        pos_map[k] = {key: i for i, key in enumerate(rows_meta)}

    # Escolher queries
    if args.random > 0:
        ref_rows = data[kinds[0]][0]
        chosen_idxs = np.random.choice(len(ref_rows), size=min(args.random, len(ref_rows)), replace=False)
        queries = [ref_rows[i] for i in chosen_idxs]
    elif args.queries:
        queries = parse_queries(args.queries)
    else:
        ap.error("Forneça --queries DOC:PAGINA ou --random N.")

    # Coletar todos os keys que precisam de resumo
    all_keys: set = set(queries)

    # Primeira passada: descobrir vizinhos para coletar resumos em batch
    neighbor_results: Dict[str, Dict[Tuple[str, int], List[Tuple[int, float, Tuple[str, int]]]]] = {}
    for k, (rows_meta, X, idx_obj) in data.items():
        neighbor_results[k] = {}
        pm = pos_map[k]
        for q in queries:
            qi = pm.get(q)
            if qi is None:
                continue
            nbrs = query_knn(idx_obj, X, qi, args.topk)
            nbr_keys = [(rows_meta[ni], d) for ni, d in nbrs]
            neighbor_results[k][q] = [(ni, d, rows_meta[ni]) for ni, d in nbrs]
            for nk, _ in nbr_keys:
                all_keys.add(nk)

    # Batch load de resumos
    resumos = load_resumos(args.db, list(all_keys))

    # Exibição
    sep = "─" * 120
    for q in queries:
        q_resumos = resumos.get(q, ("", ""))
        print(f"\n{'═' * 120}")
        print(f"  QUERY  {q[0]}  pág. {q[1]}")
        print(f"  [page] {fmt_resumo(q_resumos[0])}")
        print(f"  [glob] {fmt_resumo(q_resumos[1])}")

        for k in kinds:
            nbrs = neighbor_results.get(k, {}).get(q)
            if nbrs is None:
                print(f"\n  [{k}] (página não encontrada na tabela de embeddings)")
                continue
            print(f"\n  ── TOP-{args.topk} vizinhos [{k}] ──────────────────────────────────────")
            for rank, (ni, dist, nkey) in enumerate(nbrs, start=1):
                nr = resumos.get(nkey, ("", ""))
                r_text = nr[0] if k == "page" else nr[1]
                same_doc = "  [mesmo doc]" if nkey[0] == q[0] else ""
                print(f"  {rank:>2}. {nkey[0]:>8}  pág.{nkey[1]:>5}  dist={dist:.4f}{same_doc}")
                print(f"      {fmt_resumo(r_text)}")

    print(f"\n{'═' * 120}")


if __name__ == "__main__":
    main()
