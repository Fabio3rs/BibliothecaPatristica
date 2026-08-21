#!/usr/bin/env python3
"""Relatório somente-leitura de execução, qualidade e derivados dos resumos v2."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "patristica_resumos.db"


def scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(con.execute(sql, params).fetchone()[0] or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--documento")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resumo_generations'"
    ).fetchone()
    if not exists:
        raise SystemExit("O banco ainda não possui o schema de resumos v2")

    filters = []
    params: list[object] = []
    if args.documento:
        filters.append("documento=?")
        params.append(args.documento.upper())
    if args.run_id:
        filters.append("run_id=?")
        params.append(args.run_id)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    status_rows = con.execute(
        f"SELECT status,is_current,COUNT(*) total FROM resumo_generations {where} GROUP BY status,is_current ORDER BY status,is_current",
        params,
    ).fetchall()
    runs_where = []
    runs_params: list[object] = []
    if args.documento:
        runs_where.append("documento=?")
        runs_params.append(args.documento.upper())
    if args.run_id:
        runs_where.append("id=?")
        runs_params.append(args.run_id)
    runs_clause = f"WHERE {' AND '.join(runs_where)}" if runs_where else ""
    runs = [
        dict(row)
        for row in con.execute(
            f"""
            SELECT id,documento,status,next_page_num,blocking_page_num,blocking_reason,
                   provider,model,prompt_version,force_from_page,force_through_page,
                   created_at,updated_at
              FROM resumo_runs {runs_clause} ORDER BY id DESC LIMIT 100
            """,
            runs_params,
        )
    ]
    generation_ids_sql = f"SELECT id FROM resumo_generations {where}"
    report = {
        "filters": {"documento": args.documento, "run_id": args.run_id},
        "runs": runs,
        "generations": [
            {"status": row["status"], "current": bool(row["is_current"]), "total": row["total"]}
            for row in status_rows
        ],
        "pending_review": scalar(
            con,
            f"SELECT COUNT(*) FROM resumo_review_queue WHERE status='pending' AND generation_id IN ({generation_ids_sql})",
            tuple(params),
        ),
        "current_without_embedding": scalar(
            con,
            f"SELECT COUNT(*) FROM resumo_generations {where}{' AND' if where else ' WHERE'} is_current=1 AND embedding IS NULL",
            tuple(params),
        ),
        "completed_translations": scalar(
            con,
            f"SELECT COUNT(*) FROM resumo_translations WHERE status='completed' AND generation_id IN ({generation_ids_sql})",
            tuple(params),
        ),
        "cluster_runs_completed": scalar(
            con, "SELECT COUNT(*) FROM resumo_cluster_runs WHERE status='completed'"
        ),
    }
    con.close()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Runs:")
    for run in runs:
        blocker = f" bloqueio=p{run['blocking_page_num']} {run['blocking_reason']}" if run["blocking_page_num"] else ""
        print(
            f"  #{run['id']} {run['documento']} {run['status']} {run['provider']}/{run['model']}"
            f" next={run['next_page_num']}{blocker}"
        )
    print("Gerações:")
    for item in report["generations"]:
        print(f"  {item['status']:<20} {'current' if item['current'] else 'shadow':<7} {item['total']}")
    print(f"Revisões pendentes: {report['pending_review']}")
    print(f"Atuais sem embedding: {report['current_without_embedding']}")
    print(f"Traduções concluídas: {report['completed_translations']}")
    print(f"Cluster runs concluídos: {report['cluster_runs_completed']}")


if __name__ == "__main__":
    main()
