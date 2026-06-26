#!/usr/bin/env python3
"""Gera embeddings para resumos (página/global) em batches.

Leitura: tabela `resumos` em data/patristica_resumos.db.
Saída: tabelas `resumo_pagina_embedding` e `resumo_global_embedding` (schema criado
pelos utilitários). Idempotente por (documento, pagina_num, model).

Uso típico:
    python tools/generate_resumo_embeddings.py --kind both --limit 1000 \
        --model qwen3-embedding:8b --batch-size 32

Retomada:
    python tools/generate_resumo_embeddings.py --start-after PL001:123 --kind page
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Tuple



# Garantir import do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))



from scripts.playgrounds.embedding_playground import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, embed_documents

from resumo_embedding_utils import (
    connect_db,
    ensure_resumo_embedding_tables,
    ensure_resumos_embedding_schema,
    fetch_pending,
    pick_global_text,
    pick_page_text,
    upsert_embedding,
)


def parse_start_after(arg: str | None) -> Tuple[str, int] | None:
    if not arg:
        return None
    if ":" not in arg:
        raise argparse.ArgumentTypeError("--start-after deve ser no formato <doc>:<page>")
    doc, page = arg.split(":", 1)
    try:
        page_num = int(page)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Página inválida em --start-after") from exc
    return doc, page_num


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gera embeddings para resumos (página/global).")
    p.add_argument("--db", type=Path, default=Path("data/patristica_resumos.db"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, help="Máximo de páginas a processar (0 = todas).")
    p.add_argument(
        "--start-after",
        type=str,
        help="Retomar após <doc>:<pagina>. Ordenação por (documento, pagina_num).",
    )
    p.add_argument(
        "--kind",
        choices=["page", "global", "both"],
        default="both",
        help="Tipo de embedding a gerar.",
    )
    return p


def slice_limit(rows: List[object], processed: int, limit: int | None) -> List[object]:
    if limit is None or limit <= 0:
        return rows
    remaining = max(limit - processed, 0)
    return rows[:remaining]


def choose_text(kind: str, row) -> str:
    return pick_page_text(row) if kind == "page" else pick_global_text(row)


def prompt_label(kind: str) -> str:
    if kind == "page":
        return "Document embedding – summary_page_clean fallback resumo_pagina"
    return "Document embedding – summary_global_clean fallback resumo_global"


def ingest_kind(
    con,
    kind: str,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit: int | None,
    start_after: Tuple[str, int] | None,
) -> Tuple[int, int]:
    total_pages = 0
    total_batches = 0
    while True:
        rows = fetch_pending(con, kind=kind, model=model, limit=batch_size, start_after=start_after)
        rows = slice_limit(rows, total_pages, limit)
        if not rows:
            break

        texts: List[str] = []
        metas: List[Tuple[str, int]] = []
        for r in rows:
            txt = choose_text(kind, r)
            if not txt:
                continue  # já filtrado, mas double-check defensivo
            texts.append(txt)
            metas.append((r["documento"], int(r["pagina_num"])))

        # Se todas as linhas retornadas estiverem vazias, avance o cursor para evitar loop infinito
        if not texts:
            last = rows[-1]
            start_after = (last["documento"], int(last["pagina_num"]))
            continue

        embeddings = embed_documents(texts=texts, model=model, ollama_url=ollama_url)
        if len(embeddings) != len(metas):
            raise RuntimeError(
                f"Embedding count mismatch: {len(embeddings)} vs {len(metas)} (kind={kind})"
            )

        dim_set = {len(vec) for vec in embeddings}
        if len(dim_set) != 1:
            raise RuntimeError(f"Dimensão inconsistente no batch: {sorted(dim_set)}")

        for (doc, page), vec in zip(metas, embeddings):
            upsert_embedding(
                con,
                kind=kind,
                documento=doc,
                pagina_num=page,
                model=model,
                vec=vec,
                prompt_text=prompt_label(kind),
            )
            start_after = (doc, page)
            total_pages += 1

        con.commit()
        total_batches += 1
        print(f"[{kind}] batch {total_batches} -> {total_pages} páginas (last {start_after})")

        if limit and total_pages >= limit:
            break

    return total_pages, total_batches


def main() -> None:
    args = build_parser().parse_args()
    start_after = parse_start_after(args.start_after)

    with connect_db(args.db) as con:
        ensure_resumos_embedding_schema(con)
        ensure_resumo_embedding_tables(con)

        kinds: Iterable[str]
        if args.kind == "both":
            kinds = ["page", "global"]
        else:
            kinds = [args.kind]

        for kind in kinds:
            pages, batches = ingest_kind(
                con=con,
                kind=kind,
                model=args.model,
                ollama_url=args.ollama_url,
                batch_size=args.batch_size,
                limit=args.limit,
                start_after=start_after,
            )
            print(f"{kind}: {pages} páginas processadas em {batches} batches")


if __name__ == "__main__":
    main()
