#!/usr/bin/env python3
"""Mede alinhamento cross-lingual das unidades V2 grego-latim por pagina."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.playgrounds.embedding_playground import DEFAULT_MODEL
from tools.generate_ocr_embedding_experiment import DEFAULT_DB


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise ValueError("Vetor nulo")
    return np.asarray(vector / norm, dtype=np.float32)


def load_page_script_vectors(
    db_path: Path,
    *,
    volume_id: str,
    model: str,
    page_start: int,
    page_end: int,
) -> dict[tuple[int, str], np.ndarray]:
    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    clauses = ["c.variant_id='v2'", "c.volume_id=?", "e.model=?"]
    params: list[object] = [volume_id, model]
    if page_start > 0:
        clauses.append("c.anchor_page_num>=?")
        params.append(page_start)
    if page_end > 0:
        clauses.append("c.anchor_page_num<=?")
        params.append(page_end)
    rows = con.execute(
        f"""SELECT c.anchor_page_num,c.script,c.unit_key,e.embedding_dim,e.embedding
            FROM chunks c
            JOIN embeddings e ON e.chunk_key=c.chunk_key
            WHERE {' AND '.join(clauses)}
            ORDER BY c.anchor_page_num,c.script,c.chunk_index""",
        params,
    ).fetchall()
    con.close()
    by_unit: dict[tuple[int, str, str], list[np.ndarray]] = defaultdict(list)
    for row in rows:
        dimension = int(row["embedding_dim"])
        vector = np.frombuffer(row["embedding"], dtype=np.float32, count=dimension).copy()
        by_unit[(int(row["anchor_page_num"]), str(row["script"]), str(row["unit_key"]))].append(
            vector
        )
    result: dict[tuple[int, str], np.ndarray] = {}
    for (page_num, script, _unit_key), vectors in by_unit.items():
        result[(page_num, script)] = _normalize(
            np.mean(np.stack([_normalize(vector) for vector in vectors]), axis=0)
        )
    return result


def benchmark_parallel_pages(
    vectors: dict[tuple[int, str], np.ndarray],
    *,
    source_script: str = "greek",
    target_script: str = "latin",
) -> dict[str, object]:
    source_pages = sorted(page for page, script in vectors if script == source_script)
    target_pages = sorted(page for page, script in vectors if script == target_script)
    eligible = [page for page in source_pages if page in target_pages]
    ranks: list[int] = []
    margins: list[float] = []
    rows: list[dict[str, object]] = []
    for page in eligible:
        source = vectors[(page, source_script)]
        scores = sorted(
            (
                (float(np.dot(source, vectors[(target_page, target_script)])), target_page)
                for target_page in target_pages
            ),
            reverse=True,
        )
        rank = next(index for index, (_score, target_page) in enumerate(scores, start=1) if target_page == page)
        correct_score = next(score for score, target_page in scores if target_page == page)
        best_wrong = max((score for score, target_page in scores if target_page != page), default=-1.0)
        margin = correct_score - best_wrong
        ranks.append(rank)
        margins.append(margin)
        rows.append(
            {
                "page": page,
                "rank": rank,
                "correct_score": correct_score,
                "best_wrong_score": best_wrong,
                "margin": margin,
                "top_target_page": scores[0][1],
            }
        )
    count = len(ranks)
    return {
        "pairs": count,
        "source_script": source_script,
        "target_script": target_script,
        "recall_at_1": sum(rank <= 1 for rank in ranks) / count if count else 0.0,
        "recall_at_5": sum(rank <= 5 for rank in ranks) / count if count else 0.0,
        "mrr": sum(1.0 / rank for rank in ranks) / count if count else 0.0,
        "mean_margin": sum(margins) / count if count else 0.0,
        "rows": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--page-start", type=int, default=0)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--source-script", default="greek")
    parser.add_argument("--target-script", default="latin")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.db.is_file():
        raise SystemExit(f"DB nao encontrado: {args.db}")
    vectors = load_page_script_vectors(
        args.db,
        volume_id=args.volume,
        model=args.model,
        page_start=args.page_start,
        page_end=args.page_end,
    )
    report = benchmark_parallel_pages(
        vectors,
        source_script=args.source_script,
        target_script=args.target_script,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        f"pairs={report['pairs']} R@1={report['recall_at_1']:.4f} "
        f"R@5={report['recall_at_5']:.4f} MRR={report['mrr']:.4f} "
        f"mean_margin={report['mean_margin']:.5f}"
    )
    for row in report["rows"]:
        print(
            f"page={row['page']} rank={row['rank']} "
            f"score={row['correct_score']:.5f} margin={row['margin']:.5f} "
            f"top={row['top_target_page']}"
        )


if __name__ == "__main__":
    main()
