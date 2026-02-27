#!/usr/bin/env python3
"""
Backfill de campos limpos na tabela `resumos`:
- Adiciona colunas de limpeza/marcação se faltarem.
- Percorre registros (texto plano ou XML) e preenche:
    * ocr_clean_search
    * summary_page_clean
    * summary_global_clean
    * author_detected
    * work_detected
    * cleaning_version

Uso típico:
    python tools/backfill_clean_resumos.py --limit 1000 --batch-size 200
    python tools/backfill_clean_resumos.py --doc PL001

Flags:
    --dry-run         : não grava, apenas conta/processa.
    --migrate-only    : só cria colunas/índices e sai.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Garantir import do projeto (para acessar test_limpeza_ocr.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_limpeza_ocr import (  # type: ignore
    clean_ocr_text_optimized,
    clean_summary_page_for_embedding,
    clean_summary_global_for_search,
    extract_global_summary_fields,
)


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
CLEANING_VERSION = "clean-v1"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def ensure_clean_columns(con: sqlite3.Connection) -> None:
    """Adiciona colunas de limpeza se faltarem (idempotente)."""
    stmts = [
        ("ocr_clean_search", "TEXT NOT NULL DEFAULT ''"),
        ("summary_page_clean", "TEXT NOT NULL DEFAULT ''"),
        ("summary_global_clean", "TEXT NOT NULL DEFAULT ''"),
        ("author_detected", "TEXT NOT NULL DEFAULT ''"),
        ("work_detected", "TEXT NOT NULL DEFAULT ''"),
        ("page_kind", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("drop_original_embedding", "INTEGER NOT NULL DEFAULT 0"),
        ("drop_summary_embedding", "INTEGER NOT NULL DEFAULT 0"),
        ("drop_pagefind", "INTEGER NOT NULL DEFAULT 0"),
        ("drop_similarity", "INTEGER NOT NULL DEFAULT 0"),
        ("drop_keywords_export", "INTEGER NOT NULL DEFAULT 0"),
        ("drop_reason", "TEXT NOT NULL DEFAULT ''"),
        ("cleaning_version", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, typedef in stmts:
        if column_exists(con, "resumos", name):
            continue
        try:
            con.execute(f"ALTER TABLE resumos ADD COLUMN {name} {typedef}")
        except sqlite3.OperationalError:
            # se concorrente adicionou, segue
            pass
    con.commit()


def ensure_clean_indexes(con: sqlite3.Connection) -> None:
    """Cria índices auxiliares (idempotente)."""
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_resumos_kind_doc_page
            ON resumos(page_kind, documento, pagina_num);
        CREATE INDEX IF NOT EXISTS idx_resumos_author_work
            ON resumos(author_detected, work_detected);
        CREATE INDEX IF NOT EXISTS idx_resumos_pagefind_live
            ON resumos(documento, pagina_num) WHERE drop_pagefind = 0;
        CREATE INDEX IF NOT EXISTS idx_resumos_similarity_live
            ON resumos(documento, pagina_num) WHERE drop_similarity = 0;
        CREATE INDEX IF NOT EXISTS idx_resumos_original_embedding_live
            ON resumos(documento, pagina_num) WHERE drop_original_embedding = 0;
        CREATE INDEX IF NOT EXISTS idx_resumos_summary_embedding_live
            ON resumos(documento, pagina_num) WHERE drop_summary_embedding = 0;
        """
    )
    con.commit()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def fetch_batch(
    con: sqlite3.Connection,
    offset: int,
    batch_size: int,
    doc: str | None,
) -> List[sqlite3.Row]:
    sql = """
        SELECT id, documento, pagina_num, pagina_texto, resumo_pagina, resumo_global
        FROM resumos
    """
    params: List[object] = []
    if doc:
        sql += " WHERE documento = ?"
        params.append(doc)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([batch_size, offset])
    con.row_factory = sqlite3.Row
    return list(con.execute(sql, params))


def process_batch(
    con: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
    dry_run: bool,
) -> int:
    updates = []
    for r in rows:
        pagina_texto = r["pagina_texto"] or ""
        resumo_pagina = r["resumo_pagina"] or ""
        resumo_global = r["resumo_global"] or ""

        ocr_clean, _meta = clean_ocr_text_optimized(pagina_texto)
        resumo_pagina_clean = clean_summary_page_for_embedding(resumo_pagina)
        resumo_global_clean = clean_summary_global_for_search(resumo_global)
        global_fields = extract_global_summary_fields(resumo_global)

        updates.append(
            (
                ocr_clean,
                resumo_pagina_clean,
                resumo_global_clean,
                global_fields["author"],
                global_fields["work"],
                CLEANING_VERSION,
                r["id"],
            )
        )

    if dry_run or not updates:
        return len(updates)

    con.executemany(
        """
        UPDATE resumos
        SET
            ocr_clean_search = ?,
            summary_page_clean = ?,
            summary_global_clean = ?,
            author_detected = ?,
            work_detected = ?,
            cleaning_version = ?
        WHERE id = ?
        """,
        updates,
    )
    con.commit()
    return len(updates)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backfill de campos limpos na tabela resumos.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--doc", help="Filtrar por documento (ex.: PL001)")
    p.add_argument("--limit", type=int, default=0, help="Limite total de linhas (0 = todas)")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--dry-run", action="store_true", help="Não grava; apenas processa/conta.")
    p.add_argument("--migrate-only", action="store_true", help="Só cria colunas/índices e sai.")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB não encontrado: {args.db}")

    with sqlite3.connect(args.db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA busy_timeout = 30000")
        con.execute("PRAGMA foreign_keys = ON")

        ensure_clean_columns(con)
        ensure_clean_indexes(con)

        if args.migrate_only:
            print("Migração concluída; nenhum dado processado (--migrate-only).")
            return

        processed = 0
        offset = 0
        while True:
            batch = fetch_batch(con, offset=offset, batch_size=args.batch_size, doc=args.doc)
            if not batch:
                break

            # Respeita limite total
            if args.limit and processed + len(batch) > args.limit:
                batch = batch[: max(0, args.limit - processed)]

            count = process_batch(con, batch, dry_run=args.dry_run)
            processed += count
            offset += len(batch)

            print(f"Batch offset={offset - len(batch)} size={len(batch)} -> updated={count} total={processed}")

            if args.limit and processed >= args.limit:
                break

        print(f"Concluído. Linhas processadas: {processed} (dry_run={args.dry_run}).")


if __name__ == "__main__":
    main()

