#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reprocessa páginas com avaliações ruins e reexecuta o LLM judge.

Fluxo:
- Seleciona no data/ocr_eval.db páginas com fidelidade/usabilidade baixa ou descartar.
- Regera OCR (reprocess=True) com o pipeline já existente.
- Reexecuta o LLM judge (judge_force=True) e registra novas linhas em evaluations.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import List, Dict, Iterable, Tuple
from datetime import datetime
import os

# Habilita imports do projeto
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main2
import evaluation_db


def parse_dt(value: str | None) -> str | None:
    if not value:
        return None
    # aceita formatos YYYY-MM-DD ou ISO completo
    try:
        return datetime.fromisoformat(value).isoformat(sep=" ")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Data inválida: {value}")


def query_bad_evals(
    db_path: Path,
    since: str | None,
    until: str | None,
    provider: str | None,
    model: str | None,
    limit: int | None,
) -> List[Dict]:
    con = evaluation_db.connect_eval_db(db_path)
    params: list = []

    where_filters = []
    if since:
        where_filters.append("created_at >= ?")
        params.append(since)
    if until:
        where_filters.append("created_at <= ?")
        params.append(until)
    if provider:
        where_filters.append("provider = ?")
        params.append(provider)
    if model:
        where_filters.append("model = ?")
        params.append(model)

    where_sql = " AND ".join(where_filters)
    if where_sql:
        where_sql = "AND " + where_sql

    sql = f"""
        WITH latest AS (
            SELECT image_path, MAX(created_at) AS ts
            FROM evaluations
            GROUP BY image_path
        ),
        bad_latest AS (
            SELECT e.*
            FROM evaluations e
            JOIN latest l
              ON e.image_path = l.image_path AND e.created_at = l.ts
            WHERE e.fidelidade IN ('baixa','descartar')
               OR e.usabilidade IN ('baixa','descartar')
        )
        SELECT image_path, provider, model, created_at
        FROM bad_latest
        WHERE 1=1 {where_sql}
        ORDER BY created_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows = con.execute(sql, params).fetchall()
    out: List[Dict] = []
    seen = set()
    for r in rows:
        img = Path(r["image_path"])
        if img in seen:
            continue
        seen.add(img)
        out.append(
            {
                "image_path": img,
                "provider": r["provider"],
                "model": r["model"],
                "created_at": r["created_at"],
            }
        )
    return out


def group_by_text_dir(images: Iterable[Path]) -> Dict[Path, List[Path]]:
    groups: Dict[Path, List[Path]] = {}
    for img in images:
        text_dir = img.parent.parent / "text"
        groups.setdefault(text_dir, []).append(img)
    return groups


def chunked(seq: List[Path], size: int) -> Iterable[List[Path]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def run_batch(
    images: List[Path],
    text_dir: Path,
    args: argparse.Namespace,
    prompt_version: str,
) -> Tuple[List[Path], List[Path]]:
    # reprocess OCR
    main2.ocr_images_to_text_parallel(
        images,
        text_dir,
        lang=args.lang,
        processes=args.procs,
        omp_threads_per_proc=args.omp_threads,
        chunksize=args.chunksize,
        maxtasksperchild=args.maxtasksperchild,
        algorithm=args.algorithm,
        llm_model=args.llm_model,
        ollama_url=args.ollama_url,
        openai_base_url=args.openai_base_url,
        openai_api_key=args.openai_api_key,
        save_all_text_path=None,
        reprocess_reason="Reprocessamento devido a avaliação baixa/descartar",
    )

    failures = main2.judge_all_parallel(
        images,
        text_dir,
        eval_db_path=args.eval_db,
        lang=args.lang,
        processes=args.procs,
        omp_threads_per_proc=args.omp_threads,
        chunksize=args.chunksize,
        maxtasksperchild=args.maxtasksperchild,
        algorithm=args.algorithm,
        llm_model=args.llm_model,
        ollama_url=args.ollama_url,
        openai_base_url=args.openai_base_url,
        openai_api_key=args.openai_api_key,
        prompt_version=prompt_version,
        judge_force=True,
    )
    return failures, images


def main():
    ap = argparse.ArgumentParser(description="Reprocessa páginas com avaliação baixa/descartar e reroda LLM judge.")
    ap.add_argument("--since", type=parse_dt, default=None, help="Data mínima (YYYY-MM-DD ou ISO).")
    ap.add_argument("--until", type=parse_dt, default=None, help="Data máxima (YYYY-MM-DD ou ISO).")
    ap.add_argument("--provider", type=str, default=None, help="Filtrar provider (ex.: ollama, openai).")
    ap.add_argument("--model", type=str, default=None, help="Filtrar modelo (ex.: qwen3.5:397b-cloud).")
    ap.add_argument("--limit", type=int, default=None, help="Limitar quantidade de páginas.")
    ap.add_argument("--eval-db", type=Path, default=Path("data/ocr_eval.db"), help="Caminho do banco evaluations.")

    ap.add_argument(
        "--algorithm",
        choices=list(main2.VLM_ALGORITHMS),
        default="ollama",
        help="Provedor VLM usado no reprocessamento e no judge.",
    )
    ap.add_argument("--llm-model", type=str, default=main2.DEFAULT_LLM_MODEL)
    ap.add_argument("--ollama-url", type=str, default=main2.DEFAULT_OLLAMA_URL)
    ap.add_argument("--openai-base-url", type=str, default=main2.DEFAULT_OPENAI_BASE_URL)
    ap.add_argument("--openai-api-key", type=str, default=None)
    ap.add_argument("--lang", type=str, default=main2.DEFAULT_LANG)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--omp-threads", type=int, default=2)
    ap.add_argument("--chunksize", type=int, default=2)
    ap.add_argument("--maxtasksperchild", type=int, default=1000)
    ap.add_argument("--batch-size", type=int, default=32, help="Processar em lotes para equilibrar memória.")
    ap.add_argument("--dry-run", action="store_true", help="Apenas lista as páginas-alvo.")

    args = ap.parse_args()

    prompt_version = evaluation_db.compute_prompt_version(main2.PROMPT_LLM_JUDGE)

    if args.openai_api_key is None:
        args.openai_api_key = os.getenv("OPENAI_API_KEY")

    rows = query_bad_evals(
        db_path=args.eval_db,
        since=args.since,
        until=args.until,
        provider=args.provider,
        model=args.model,
        limit=args.limit,
    )

    images = [r["image_path"] for r in rows if r["image_path"].exists()]
    missing = [r["image_path"] for r in rows if not r["image_path"].exists()]

    print(f"Selecionadas: {len(rows)}; com arquivo: {len(images)}; faltando: {len(missing)}")
    if missing:
        for m in missing:
            print(f"[WARN] imagem ausente: {m}")

    if args.dry_run or not images:
        return

    grouped = group_by_text_dir(images)
    total_fail = []
    for text_dir, imgs in grouped.items():
        for batch in chunked(imgs, args.batch_size):
            print(f"[RUN] text_dir={text_dir} batch={len(batch)}")
            fail, _ = run_batch(batch, text_dir, args, prompt_version)
            total_fail.extend(fail)

    if total_fail:
        print(f"Falhas no parse do judge: {[p.name for p in total_fail]}")
    print("Concluído.")


if __name__ == "__main__":
    main()
