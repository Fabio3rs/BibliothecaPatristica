#!/usr/bin/env python3
"""Remove somente o estado summary-v2 dos cinco volumes substituídos.

O modo padrão é auditoria somente leitura. Para gravar, são obrigatórios
``--apply``, a confirmação exata e um relatório JSON. A limpeza usa os mesmos
locks por volume de ``resumo_serial.py`` e uma única transação SQLite.

Este utilitário não altera a tabela legada ``resumos``, o banco de keywords,
arquivos OCR, traduções de outros volumes nem metadados globais de clustering.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
AUTHORIZED_VOLUME_IDS = ("PG024", "PG031", "PG084", "PG116", "PL124")
CONFIRMATION = ",".join(AUTHORIZED_VOLUME_IDS)

REQUIRED_TABLES = frozenset(
    {
        "resumos",
        "resumo_runs",
        "resumo_generations",
        "resumo_translations",
        "resumo_generation_clusters",
        "resumo_review_queue",
        "resumo_context_anchors",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _placeholders() -> str:
    return ",".join("?" for _ in AUTHORIZED_VOLUME_IDS)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _connect_writable(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _assert_schema(con: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise RuntimeError("Tabelas summary-v2 ausentes: " + ", ".join(missing))


def _selected_generation_sql() -> str:
    return (
        "SELECT id FROM resumo_generations WHERE documento IN ("
        + _placeholders()
        + ")"
    )


def audit(con: sqlite3.Connection) -> dict[str, int]:
    """Conta todo estado v2 atribuível aos volumes autorizados."""
    _assert_schema(con)
    ph = _placeholders()
    params = AUTHORIZED_VOLUME_IDS
    generation_sql = _selected_generation_sql()
    doubled = params + params

    queries: dict[str, tuple[str, tuple[str, ...]]] = {
        "resumo_runs": (
            f"SELECT COUNT(*) FROM resumo_runs WHERE documento IN ({ph})",
            params,
        ),
        "resumo_generations": (
            f"SELECT COUNT(*) FROM resumo_generations WHERE documento IN ({ph})",
            params,
        ),
        "resumo_translations": (
            "SELECT COUNT(*) FROM resumo_translations WHERE generation_id IN ("
            f"{generation_sql})",
            params,
        ),
        "resumo_generation_clusters": (
            "SELECT COUNT(*) FROM resumo_generation_clusters "
            f"WHERE documento IN ({ph}) OR generation_id IN ({generation_sql})",
            doubled,
        ),
        "resumo_review_queue": (
            "SELECT COUNT(*) FROM resumo_review_queue "
            f"WHERE documento IN ({ph}) OR generation_id IN ({generation_sql})",
            doubled,
        ),
        "resumo_context_anchors": (
            f"SELECT COUNT(*) FROM resumo_context_anchors WHERE documento IN ({ph})",
            params,
        ),
        "previous_generation_links": (
            "SELECT COUNT(*) FROM resumo_generations WHERE previous_generation_id IN ("
            f"{generation_sql})",
            params,
        ),
        "legacy_resumos_preserved": (
            f"SELECT COUNT(*) FROM resumos WHERE documento IN ({ph})",
            params,
        ),
        "affected_cluster_runs": (
            "SELECT COUNT(DISTINCT cluster_run_id) FROM resumo_generation_clusters "
            f"WHERE documento IN ({ph}) OR generation_id IN ({generation_sql})",
            doubled,
        ),
        "cross_document_run_generations": (
            "SELECT COUNT(*) FROM resumo_generations g "
            "JOIN resumo_runs r ON r.id=g.run_id WHERE "
            f"(g.documento IN ({ph}) AND r.documento NOT IN ({ph})) OR "
            f"(g.documento NOT IN ({ph}) AND r.documento IN ({ph}))",
            params + params + params + params,
        ),
    }
    return {
        label: int(con.execute(sql, query_params).fetchone()[0])
        for label, (sql, query_params) in queries.items()
    }


def _acquire_volume_locks(db_path: Path) -> list[TextIO]:
    lock_dir = db_path.resolve().parent / ".resumo_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_db = re.sub(r"[^A-Za-z0-9_.-]+", "_", db_path.name)
    handles: list[TextIO] = []
    try:
        for volume_id in AUTHORIZED_VOLUME_IDS:
            lock_path = lock_dir / f"{safe_db}.{volume_id}.lock"
            handle = lock_path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise RuntimeError(
                    f"{volume_id} está sendo processado (lock: {lock_path})"
                ) from exc
            handles.append(handle)
    except BaseException:
        _release_volume_locks(handles)
        raise
    return handles


def _release_volume_locks(handles: list[TextIO]) -> None:
    for handle in reversed(handles):
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def purge(con: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    """Apaga o estado v2 selecionado; o chamador controla a transação."""
    before = audit(con)
    if before["cross_document_run_generations"]:
        raise RuntimeError(
            "Relações run/generation cruzam documentos; expurgo recusado"
        )

    ph = _placeholders()
    params = AUTHORIZED_VOLUME_IDS
    generation_sql = _selected_generation_sql()
    doubled = params + params
    statements: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "resumo_translations",
            "DELETE FROM resumo_translations WHERE generation_id IN ("
            f"{generation_sql})",
            params,
        ),
        (
            "resumo_generation_clusters",
            "DELETE FROM resumo_generation_clusters "
            f"WHERE documento IN ({ph}) OR generation_id IN ({generation_sql})",
            doubled,
        ),
        (
            "resumo_review_queue",
            "DELETE FROM resumo_review_queue "
            f"WHERE documento IN ({ph}) OR generation_id IN ({generation_sql})",
            doubled,
        ),
        (
            "resumo_context_anchors",
            f"DELETE FROM resumo_context_anchors WHERE documento IN ({ph})",
            params,
        ),
        (
            "previous_generation_links_cleared",
            "UPDATE resumo_generations SET previous_generation_id=NULL "
            f"WHERE previous_generation_id IN ({generation_sql})",
            params,
        ),
        (
            "resumo_generations",
            f"DELETE FROM resumo_generations WHERE documento IN ({ph})",
            params,
        ),
        (
            "resumo_runs",
            f"DELETE FROM resumo_runs WHERE documento IN ({ph})",
            params,
        ),
    ]
    changed: dict[str, int] = {}
    for label, sql, statement_params in statements:
        cursor = con.execute(sql, statement_params)
        changed[label] = max(0, int(cursor.rowcount))

    after = audit(con)
    cleanup_keys = (
        "resumo_runs",
        "resumo_generations",
        "resumo_translations",
        "resumo_generation_clusters",
        "resumo_review_queue",
        "resumo_context_anchors",
        "previous_generation_links",
        "cross_document_run_generations",
    )
    remaining = {key: after[key] for key in cleanup_keys if after[key]}
    if remaining:
        raise RuntimeError(f"Estado summary-v2 restante: {remaining}")
    if after["legacy_resumos_preserved"] != before["legacy_resumos_preserved"]:
        raise RuntimeError("A contagem da tabela legada resumos mudou")
    violations = con.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Violações de chave estrangeira: {violations[:20]}")
    return changed, after


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        help=f"Obrigatório com --apply; valor exato: {CONFIRMATION}",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Relatório JSON; obrigatório com --apply",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()
    if not db_path.is_file():
        raise SystemExit(f"Banco não encontrado: {db_path}")
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"Confirmação exigida: --confirm {CONFIRMATION}")
    if args.apply and args.report is None:
        raise SystemExit("--report é obrigatório com --apply")

    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "apply" if args.apply else "dry_run",
        "database": str(db_path),
        "volume_ids": list(AUTHORIZED_VOLUME_IDS),
        "started_at": _utc_now(),
    }
    locks: list[TextIO] = []
    try:
        if args.apply:
            locks = _acquire_volume_locks(db_path)
            con = _connect_writable(db_path)
            try:
                con.execute("BEGIN IMMEDIATE")
                report["selected_rows_before"] = audit(con)
                changed, after = purge(con)
                report["changed"] = changed
                report["selected_rows_after"] = after
                con.commit()
                report["status"] = "applied"
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()
        else:
            with _connect_readonly(db_path) as con:
                report["selected_rows_before"] = audit(con)
            report["status"] = "audited"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _release_volume_locks(locks)
        report["finished_at"] = _utc_now()
        if args.report is not None:
            _write_report(args.report.resolve(), report)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
