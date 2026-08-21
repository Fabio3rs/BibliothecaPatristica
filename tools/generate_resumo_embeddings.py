#!/usr/bin/env python3
"""Gera embeddings para resumos legados ou gerações v2 em batches.

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

try:
    from tools.resumo_embedding_utils import (
        connect_db,
        ensure_resumo_embedding_tables,
        ensure_resumos_embedding_schema,
        fetch_pending,
        floats_to_blob,
        pick_global_text,
        pick_page_text,
        upsert_embedding,
    )
except ImportError:  # execução direta: python tools/generate_resumo_embeddings.py
    from resumo_embedding_utils import (  # type: ignore[no-redef]
        connect_db,
        ensure_resumo_embedding_tables,
        ensure_resumos_embedding_schema,
        fetch_pending,
        floats_to_blob,
        pick_global_text,
        pick_page_text,
        upsert_embedding,
    )
from resumo_v2 import init_v2_schema, sha256_text


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
        choices=["page", "global", "both", "v2"],
        default="both",
        help="Tipo: page/global/both legados ou v2 na própria geração.",
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


def ingest_v2(
    con,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit: int | None,
    start_after: Tuple[str, int] | None,
) -> Tuple[int, int]:
    total_pages = 0
    total_batches = 0
    cursor = start_after
    while True:
        sql = """
            SELECT id, documento, pagina_num, embedding_text, embedding,
                   embedding_model, embedding_source_hash
              FROM resumo_generations
             WHERE is_current=1
               AND status IN ('valid','metadata_pending')
               AND COALESCE(embedding_text,'')!=''
        """
        params: List[object] = []
        if cursor:
            sql += " AND (documento>? OR (documento=? AND pagina_num>?))"
            params.extend([cursor[0], cursor[0], cursor[1]])
        sql += " ORDER BY documento,pagina_num LIMIT ?"
        params.append(batch_size)
        candidates = list(con.execute(sql, params))
        rows = []
        for row in candidates:
            expected = sha256_text(model, row["embedding_text"])
            if row["embedding"] is not None and row["embedding_model"] == model and row["embedding_source_hash"] == expected:
                cursor = (row["documento"], int(row["pagina_num"]))
                continue
            rows.append((row, expected))
        if limit and total_pages + len(rows) > limit:
            rows = rows[: max(0, limit - total_pages)]
        if not rows:
            if candidates:
                last = candidates[-1]
                cursor = (last["documento"], int(last["pagina_num"]))
                continue
            break
        texts = [str(row["embedding_text"]) for row, _expected in rows]
        embeddings = embed_documents(texts=texts, model=model, ollama_url=ollama_url)
        if len(embeddings) != len(rows):
            raise RuntimeError(f"Embedding count mismatch v2: {len(embeddings)} vs {len(rows)}")
        for (row, expected), vector in zip(rows, embeddings):
            con.execute(
                """
                UPDATE resumo_generations
                   SET embedding=?, embedding_dim=?, embedding_model=?,
                       embedding_source_hash=?, embedding_updated_at=CURRENT_TIMESTAMP
                 WHERE id=? AND embedding_text=?
                """,
                (
                    floats_to_blob(vector), len(vector), model, expected,
                    row["id"], row["embedding_text"],
                ),
            )
            cursor = (row["documento"], int(row["pagina_num"]))
            total_pages += 1
        con.commit()
        total_batches += 1
        print(f"[v2] batch {total_batches} -> {total_pages} páginas (last {cursor})")
        if limit and total_pages >= limit:
            break
    return total_pages, total_batches


def main() -> None:
    args = build_parser().parse_args()
    start_after = parse_start_after(args.start_after)

    with connect_db(args.db) as con:
        if args.kind == "v2":
            init_v2_schema(con)
            pages, batches = ingest_v2(
                con=con,
                model=args.model,
                ollama_url=args.ollama_url,
                batch_size=args.batch_size,
                limit=args.limit,
                start_after=start_after,
            )
            print(f"v2: {pages} páginas processadas em {batches} batches")
            return
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
