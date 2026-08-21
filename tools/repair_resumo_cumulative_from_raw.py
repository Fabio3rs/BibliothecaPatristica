#!/usr/bin/env python3
"""Restaura cumulative_summary v2 a partir de raw_response sem chamar o LLM.

O comando é dry-run por padrão. Com ``--apply``, recusa gerações promovidas,
cria um backup SQLite compacto e atualiza sínteses, statuses, hashes da cadeia,
traduções dependentes e a fila de revisão numa única transação.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from resumo_v2 import (  # noqa: E402
    CUMULATIVE_SUMMARY_REVIEW_LIMIT,
    extract_json_object_response,
    normalize_whitespace,
    sha256_text,
)

DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
CONTEXTUAL_ISSUES = {
    "summary_too_short",
    "missing_cumulative_summary",
    "false_administrative_risk",
}
LEGACY_TRUNCATION_ISSUE = "cumulative_summary_too_long"
EXCESSIVE_ISSUE = "cumulative_summary_excessive"


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _status_for_issues(issues: list[str]) -> str:
    if CONTEXTUAL_ISSUES & set(issues):
        return "context_provisional"
    return "metadata_pending" if issues else "valid"


def plan_repair(
    con: sqlite3.Connection, *, run_id: int
) -> tuple[sqlite3.Row, list[dict[str, Any]], dict[str, Any]]:
    run = con.execute("SELECT * FROM resumo_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"Run inexistente: {run_id}")
    rows = con.execute(
        "SELECT * FROM resumo_generations WHERE run_id=? ORDER BY pagina_num", (run_id,)
    ).fetchall()
    if not rows:
        raise ValueError(f"Run {run_id} não possui gerações")
    if any(int(row["is_current"] or 0) for row in rows):
        raise ValueError("O reparador recusa gerações já promovidas")

    queue_rows = con.execute(
        """
        SELECT q.generation_id, q.issue_kind, q.status
          FROM resumo_review_queue q
          JOIN resumo_generations g ON g.id=q.generation_id
         WHERE g.run_id=?
        """,
        (run_id,),
    ).fetchall()
    queue = {
        (int(row["generation_id"]), str(row["issue_kind"])): str(row["status"])
        for row in queue_rows
    }

    previous_chain = str(rows[0]["previous_chain_hash"] or "")
    changes: list[dict[str, Any]] = []
    status_before = Counter(str(row["status"]) for row in rows)
    status_after: Counter[str] = Counter()
    issues_before: Counter[str] = Counter()
    issues_after: Counter[str] = Counter()
    restored_chars = 0
    max_raw_chars = 0
    queue_insertions = 0
    queue_resolutions = 0

    for row in rows:
        raw_response = str(row["raw_response"] or "")
        if not raw_response.strip():
            raise ValueError(
                f"p{row['pagina_num']} não possui raw_response; restauração não é segura"
            )
        try:
            payload = extract_json_object_response(raw_response)
        except Exception as exc:
            raise ValueError(f"p{row['pagina_num']} raw_response inválido: {exc}") from exc
        if "cumulative_summary" not in payload:
            raise ValueError(f"p{row['pagina_num']} não contém cumulative_summary no raw")

        raw_cumulative = normalize_whitespace(payload.get("cumulative_summary"))
        stored_cumulative = str(row["cumulative_summary"] or "")
        max_raw_chars = max(max_raw_chars, len(raw_cumulative))
        if raw_cumulative != stored_cumulative:
            restored_chars += max(0, len(raw_cumulative) - len(stored_cumulative))

        old_issues = [str(item) for item in _json_list(row["validation_issues_json"])]
        issues_before.update(old_issues)
        new_issues = [item for item in old_issues if item != LEGACY_TRUNCATION_ISSUE]
        if len(raw_cumulative) > CUMULATIVE_SUMMARY_REVIEW_LIMIT:
            if EXCESSIVE_ISSUE not in new_issues:
                new_issues.append(EXCESSIVE_ISSUE)
        else:
            new_issues = [item for item in new_issues if item != EXCESSIVE_ISSUE]
        new_issues = list(dict.fromkeys(new_issues))
        issues_after.update(new_issues)
        status = _status_for_issues(new_issues)
        status_after[status] += 1

        segments = _json_list(row["segments_json"])
        output_hash = sha256_text(
            row["summary_display_pt"],
            raw_cumulative,
            json.dumps(segments, ensure_ascii=False, sort_keys=True),
        )
        chain_hash = sha256_text(previous_chain, row["source_hash"], output_hash)
        cumulative_changed = raw_cumulative != stored_cumulative
        changed = any(
            (
                cumulative_changed,
                new_issues != old_issues,
                status != str(row["status"]),
                output_hash != str(row["output_hash"] or ""),
                previous_chain != str(row["previous_chain_hash"] or ""),
                chain_hash != str(row["chain_hash"] or ""),
            )
        )

        generation_id = int(row["id"])
        for issue in new_issues:
            if (generation_id, issue) not in queue:
                queue_insertions += 1
        if (
            LEGACY_TRUNCATION_ISSUE in old_issues
            and queue.get((generation_id, LEGACY_TRUNCATION_ISSUE)) == "pending"
        ):
            queue_resolutions += 1

        changes.append(
            {
                "id": generation_id,
                "pagina_num": int(row["pagina_num"]),
                "changed": changed,
                "cumulative_changed": cumulative_changed,
                "cumulative_summary": raw_cumulative,
                "status": status,
                "validation_issues": new_issues,
                "validation_issues_json": json.dumps(new_issues, ensure_ascii=False),
                "output_hash": output_hash,
                "previous_chain_hash": previous_chain,
                "chain_hash": chain_hash,
            }
        )
        previous_chain = chain_hash

    report = {
        "run_id": run_id,
        "documento": str(run["documento"]),
        "generations": len(rows),
        "restored_summaries": sum(item["cumulative_changed"] for item in changes),
        "restored_chars": restored_chars,
        "max_raw_chars": max_raw_chars,
        "changed_generations": sum(item["changed"] for item in changes),
        "chain_rows_changed": sum(
            item["chain_hash"] != str(row["chain_hash"] or "")
            for item, row in zip(changes, rows)
        ),
        "status_before": dict(sorted(status_before.items())),
        "status_after": dict(sorted(status_after.items())),
        "issues_before": dict(sorted(issues_before.items())),
        "issues_after": dict(sorted(issues_after.items())),
        "review_rows_to_insert": queue_insertions,
        "review_rows_to_resolve": queue_resolutions,
    }
    return run, changes, report


def create_compact_backup(
    db_path: Path, *, run_id: int, documento: str, backup_path: Path
) -> None:
    if backup_path.exists():
        raise FileExistsError(f"Backup já existe: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(backup_path)
    try:
        backup.execute("ATTACH DATABASE ? AS source", (str(db_path.resolve()),))
        backup.execute(
            "CREATE TABLE resumo_runs AS SELECT * FROM source.resumo_runs WHERE id=?",
            (run_id,),
        )
        backup.execute(
            "CREATE TABLE resumo_generations AS "
            "SELECT * FROM source.resumo_generations WHERE run_id=?",
            (run_id,),
        )
        backup.execute(
            "CREATE TABLE resumo_review_queue AS "
            "SELECT q.* FROM source.resumo_review_queue q "
            "JOIN source.resumo_generations g ON g.id=q.generation_id WHERE g.run_id=?",
            (run_id,),
        )
        backup.execute(
            "CREATE TABLE resumo_translations AS "
            "SELECT t.* FROM source.resumo_translations t "
            "JOIN source.resumo_generations g ON g.id=t.generation_id WHERE g.run_id=?",
            (run_id,),
        )
        backup.execute("CREATE TABLE repair_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        backup.executemany(
            "INSERT INTO repair_metadata VALUES (?,?)",
            [
                ("source_db", str(db_path.resolve())),
                ("run_id", str(run_id)),
                ("documento", documento),
                ("created_at", datetime.now(timezone.utc).isoformat()),
            ],
        )
        backup.commit()
    finally:
        backup.close()


def apply_repair(
    con: sqlite3.Connection,
    *,
    run_id: int,
    documento: str,
    changes: list[dict[str, Any]],
) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        for item in changes:
            if item["changed"]:
                con.execute(
                    """
                    UPDATE resumo_generations
                       SET cumulative_summary=?, status=?, validation_issues_json=?,
                           output_hash=?, previous_chain_hash=?, chain_hash=?,
                           updated_at=CURRENT_TIMESTAMP
                     WHERE id=? AND run_id=? AND is_current=0
                    """,
                    (
                        item["cumulative_summary"],
                        item["status"],
                        item["validation_issues_json"],
                        item["output_hash"],
                        item["previous_chain_hash"],
                        item["chain_hash"],
                        item["id"],
                        run_id,
                    ),
                )
                if con.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError(f"Geração {item['id']} não foi atualizada")
            if item["cumulative_changed"]:
                con.execute(
                    "UPDATE resumo_translations SET status='stale' "
                    "WHERE generation_id=? AND status!='stale'",
                    (item["id"],),
                )

            for issue in item["validation_issues"]:
                severity = "context" if item["status"] == "context_provisional" else "metadata"
                con.execute(
                    """
                    INSERT INTO resumo_review_queue
                        (generation_id, documento, pagina_num, issue_kind, severity, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(generation_id, issue_kind) DO UPDATE SET
                        severity=excluded.severity,
                        evidence_json=excluded.evidence_json
                    """,
                    (
                        item["id"],
                        documento,
                        item["pagina_num"],
                        issue,
                        severity,
                        json.dumps(
                            {"source": "repair_resumo_cumulative_from_raw"},
                            ensure_ascii=False,
                        ),
                    ),
                )
            con.execute(
                """
                UPDATE resumo_review_queue
                   SET status='resolved',
                       resolution_json=?,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE generation_id=? AND issue_kind=? AND status='pending'
                """,
                (
                    json.dumps(
                        {
                            "resolution": "full_cumulative_restored_from_raw_response",
                            "review_limit": CUMULATIVE_SUMMARY_REVIEW_LIMIT,
                        },
                        ensure_ascii=False,
                    ),
                    item["id"],
                    LEGACY_TRUNCATION_ISSUE,
                ),
            )
        con.execute(
            "UPDATE resumo_runs SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,)
        )
        con.commit()
    except Exception:
        con.rollback()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    uri = f"file:{args.db.resolve()}?mode={'rw' if args.apply else 'ro'}"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    if not args.apply:
        con.execute("PRAGMA query_only=ON")
    volume_lock = None
    try:
        if args.apply:
            from resumo_serial import acquire_volume_processing_lock

            run_identity = con.execute(
                "SELECT documento FROM resumo_runs WHERE id=?", (args.run_id,)
            ).fetchone()
            if run_identity is None:
                raise ValueError(f"Run inexistente: {args.run_id}")
            volume_lock = acquire_volume_processing_lock(
                args.db, str(run_identity["documento"])
            )
        run, changes, report = plan_repair(con, run_id=args.run_id)
        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = args.backup or (
                PROJECT_ROOT
                / "data"
                / "repair_backups"
                / f"resumo-run-{args.run_id}-cumulative-raw-{stamp}.sqlite"
            )
            create_compact_backup(
                args.db,
                run_id=args.run_id,
                documento=str(run["documento"]),
                backup_path=backup_path,
            )
            apply_repair(
                con,
                run_id=args.run_id,
                documento=str(run["documento"]),
                changes=changes,
            )
            report["applied"] = True
            report["backup"] = str(backup_path)
        else:
            report["applied"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if volume_lock is not None:
            from resumo_serial import release_volume_processing_lock

            release_volume_processing_lock(volume_lock)
        con.close()


if __name__ == "__main__":
    main()
