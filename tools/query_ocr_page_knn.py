#!/usr/bin/env python3
"""Consulta KNN pagina-pagina nos centroides do experimento de OCR."""
from __future__ import annotations

import argparse
import heapq
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.playgrounds.embedding_playground import DEFAULT_MODEL
from tools.generate_ocr_embedding_experiment import DEFAULT_DB


def parse_locator(value: str) -> tuple[str, int]:
    volume, separator, page = value.strip().partition(":")
    if not separator or not volume or not page.isdigit() or int(page) <= 0:
        raise argparse.ArgumentTypeError("Use o locator VOLUME:PAGINA, por exemplo PG026:72")
    return volume, int(page)


def page_knn(
    db_path: Path,
    *,
    anchor_volume: str,
    anchor_page: int,
    variant_id: str,
    model: str,
    top_k: int,
    volumes: Sequence[str] = (),
    exclude_nearby: int = 0,
    exclude_same_volume: bool = False,
) -> list[dict[str, object]]:
    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    anchor = con.execute(
        """SELECT embedding_dim,embedding FROM page_embeddings
           WHERE volume_id=? AND page_num=? AND variant_id=? AND model=?""",
        (anchor_volume, anchor_page, variant_id, model),
    ).fetchone()
    if anchor is None:
        con.close()
        raise ValueError(
            f"Centroide ausente: {anchor_volume}:{anchor_page} "
            f"variant={variant_id} model={model}"
        )
    dimension = int(anchor["embedding_dim"])
    query = np.frombuffer(anchor["embedding"], dtype=np.float32, count=dimension)
    norm = float(np.linalg.norm(query))
    if norm == 0.0:
        con.close()
        raise ValueError("Centroide ancora nulo")
    query = query / norm

    clauses = ["pe.variant_id=?", "pe.model=?"]
    params: list[object] = [variant_id, model]
    if volumes:
        placeholders = ",".join("?" for _ in volumes)
        clauses.append(f"pe.volume_id IN ({placeholders})")
        params.extend(volumes)
    cursor = con.execute(
        f"""SELECT pe.volume_id,pe.page_num,pe.embedding_dim,pe.embedding,
                   pe.component_unit_count,pe.component_chunk_count,p.source_file
            FROM page_embeddings pe
            JOIN pages p ON p.volume_id=pe.volume_id AND p.page_num=pe.page_num
            WHERE {' AND '.join(clauses)}
            ORDER BY pe.volume_id,pe.page_num""",
        params,
    )
    heap: list[tuple[float, str, int, dict[str, object]]] = []
    for row in cursor:
        volume_id = str(row["volume_id"])
        page_num = int(row["page_num"])
        if volume_id == anchor_volume and page_num == anchor_page:
            continue
        if exclude_same_volume and volume_id == anchor_volume:
            continue
        if (
            exclude_nearby > 0
            and volume_id == anchor_volume
            and abs(page_num - anchor_page) <= exclude_nearby
        ):
            continue
        row_dimension = int(row["embedding_dim"])
        if row_dimension != dimension:
            con.close()
            raise ValueError(
                f"Dimensao incompativel em {volume_id}:{page_num}: "
                f"{row_dimension} != {dimension}"
            )
        vector = np.frombuffer(row["embedding"], dtype=np.float32, count=dimension)
        vector_norm = float(np.linalg.norm(vector))
        if vector_norm == 0.0:
            continue
        score = float(np.dot(query, vector / vector_norm))
        item: dict[str, object] = {
            "score": score,
            "volume_id": volume_id,
            "page_num": page_num,
            "source_file": row["source_file"],
            "component_units": int(row["component_unit_count"]),
            "component_chunks": int(row["component_chunk_count"]),
        }
        ranked = (score, volume_id, page_num, item)
        if len(heap) < top_k:
            heapq.heappush(heap, ranked)
        elif ranked[:3] > heap[0][:3]:
            heapq.heapreplace(heap, ranked)
    con.close()
    return [item for _score, _volume, _page, item in sorted(heap, reverse=True)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("anchor", type=parse_locator)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--variant", choices=("v0", "v1", "v2"), default="v2")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--volumes", nargs="*", default=[])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--exclude-nearby", type=int, default=0)
    parser.add_argument("--exclude-same-volume", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.db.is_file():
        raise SystemExit(f"DB nao encontrado: {args.db}")
    if args.top_k <= 0 or args.exclude_nearby < 0:
        raise SystemExit("--top-k deve ser positivo e --exclude-nearby nao negativo")
    volume, page = args.anchor
    try:
        results = page_knn(
            args.db,
            anchor_volume=volume,
            anchor_page=page,
            variant_id=args.variant,
            model=args.model,
            top_k=args.top_k,
            volumes=args.volumes,
            exclude_nearby=args.exclude_nearby,
            exclude_same_volume=args.exclude_same_volume,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print(f"anchor={volume}:{page} variant={args.variant} model={args.model}")
    for rank, item in enumerate(results, start=1):
        print(
            f"{rank:02d}. score={item['score']:.5f} "
            f"{item['volume_id']}:{item['page_num']} "
            f"units={item['component_units']} chunks={item['component_chunks']} "
            f"source={item['source_file']}"
        )


if __name__ == "__main__":
    main()
