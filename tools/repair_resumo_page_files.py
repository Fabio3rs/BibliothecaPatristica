#!/usr/bin/env python3
"""Sincroniza ``pagina_file`` do banco de resumos com o corpus atual.

Os nomes são resolvidos exclusivamente pela chave lógica ``(documento,
pagina_num)`` usando o mesmo parser do restante do pipeline. O comando é
dry-run por padrão e, ao aplicar, grava um manifesto reversível antes de
confirmar a transação SQLite.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.corpus_utils import discover_unique_page_map


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_CORPUS_ROOT = PROJECT_ROOT / "teste"
DEFAULT_MANIFEST_ROOT = PROJECT_ROOT / "data" / "resumo_page_file_repairs"
SUPPORTED_TABLES = (
    "resumos",
    "resumo_generations",
    "resumo_context_anchors",
)


@dataclass(frozen=True)
class PageFileChange:
    table: str
    row_id: int
    documento: str
    pagina_num: int
    old_file: str
    new_file: str


@dataclass(frozen=True)
class UnresolvedPage:
    table: str
    row_id: int
    documento: str
    pagina_num: int
    old_file: str


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"Banco não encontrado: {db_path}")
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _validate_table(con: sqlite3.Connection, table: str) -> None:
    if table not in SUPPORTED_TABLES:
        raise ValueError(f"Tabela não suportada: {table}")
    columns = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}
    required = {"id", "documento", "pagina_num", "pagina_file"}
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        raise RuntimeError(f"{table}: colunas obrigatórias ausentes: {missing}")


def build_plan(
    *,
    db_path: Path,
    corpus_root: Path,
    tables: Sequence[str] = SUPPORTED_TABLES,
) -> tuple[list[PageFileChange], list[UnresolvedPage]]:
    con = connect_read_only(db_path)
    changes: list[PageFileChange] = []
    unresolved: list[UnresolvedPage] = []
    page_maps: dict[str, dict[int, Path]] = {}
    try:
        for table in tables:
            _validate_table(con, table)
            rows = con.execute(
                f"SELECT id, documento, pagina_num, pagina_file "
                f"FROM {table} ORDER BY documento, pagina_num, id"
            )
            for row in rows:
                documento = str(row["documento"])
                pagina_num = int(row["pagina_num"])
                old_file = str(row["pagina_file"])
                if documento not in page_maps:
                    text_dir = corpus_root / documento / "text"
                    page_maps[documento] = (
                        discover_unique_page_map(text_dir, volume_id=documento)
                        if text_dir.is_dir()
                        else {}
                    )
                target = page_maps[documento].get(pagina_num)
                if target is None:
                    unresolved.append(
                        UnresolvedPage(
                            table=table,
                            row_id=int(row["id"]),
                            documento=documento,
                            pagina_num=pagina_num,
                            old_file=old_file,
                        )
                    )
                elif old_file != target.name:
                    changes.append(
                        PageFileChange(
                            table=table,
                            row_id=int(row["id"]),
                            documento=documento,
                            pagina_num=pagina_num,
                            old_file=old_file,
                            new_file=target.name,
                        )
                    )
    finally:
        con.close()
    return changes, unresolved


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def apply_plan(
    *,
    db_path: Path,
    corpus_root: Path,
    changes: Sequence[PageFileChange],
    unresolved: Sequence[UnresolvedPage],
    manifest_root: Path,
) -> Path:
    if unresolved:
        raise RuntimeError(
            f"Aplicação recusada: {len(unresolved)} linha(s) sem página no corpus"
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = manifest_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    pending_manifest = run_dir / "manifest.pending.json"
    manifest = run_dir / "manifest.json"
    payload = {
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(db_path),
        "corpus_root": str(corpus_root),
        "policy": "resolve pagina_file by exact (documento, pagina_num)",
        "changes": [asdict(item) for item in changes],
    }
    _write_json(pending_manifest, payload)

    con = sqlite3.connect(db_path, timeout=30.0)
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        con.execute("BEGIN IMMEDIATE")
        for item in changes:
            changed = con.execute(
                f"UPDATE {item.table} SET pagina_file=? "
                "WHERE id=? AND documento=? AND pagina_num=? AND pagina_file=?",
                (
                    item.new_file,
                    item.row_id,
                    item.documento,
                    item.pagina_num,
                    item.old_file,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError(
                    f"{item.table}:{item.row_id}: linha mudou após o dry-run"
                )
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()

    payload["status"] = "complete"
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest, payload)
    pending_manifest.unlink()
    return manifest


def summarize(
    changes: Sequence[PageFileChange], unresolved: Sequence[UnresolvedPage]
) -> None:
    for table in SUPPORTED_TABLES:
        table_changes = [item for item in changes if item.table == table]
        if table_changes:
            documents = len({item.documento for item in table_changes})
            print(f"{table}: corrigir={len(table_changes)} documentos={documents}")
    print(f"TOTAL: corrigir={len(changes)} sem_correspondencia={len(unresolved)}")
    for item in unresolved[:20]:
        print(
            f"SEM_CORRESPONDENCIA {item.table}:{item.row_id} "
            f"{item.documento}:{item.pagina_num} {item.old_file}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=SUPPORTED_TABLES,
        default=list(SUPPORTED_TABLES),
    )
    parser.add_argument("--apply", action="store_true", help="aplica; sem isso é dry-run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    changes, unresolved = build_plan(
        db_path=args.db,
        corpus_root=args.corpus_root,
        tables=args.tables,
    )
    summarize(changes, unresolved)
    if unresolved:
        return 2
    if not args.apply:
        print("Dry-run: nenhuma alteração aplicada.")
        return 0
    manifest = apply_plan(
        db_path=args.db,
        corpus_root=args.corpus_root,
        changes=changes,
        unresolved=unresolved,
        manifest_root=args.manifest_root,
    )
    print(f"Manifesto: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
