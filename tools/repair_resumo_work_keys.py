#!/usr/bin/env python3
"""Repara work_key de gerações v2 existentes sem chamar o LLM.

O comando é dry-run por padrão. Com ``--apply``, cria primeiro um SQLite
compacto contendo as linhas afetadas e só então atualiza a cadeia inteira do
run em uma transação.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from resumo_v2 import (  # noqa: E402
    SummaryCandidate,
    build_embedding_text,
    build_summary_search_text,
    canonical_labels_from_hints,
    load_volume_index_hints,
    open_indices_readonly,
    page_number,
    reconcile_candidate_work_keys,
    sha256_text,
)

DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_INDICES_DB = PROJECT_ROOT / "data" / "patristic_indices.db"


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _candidate_from_row(row: sqlite3.Row) -> SummaryCandidate:
    return SummaryCandidate(
        page_kinds=tuple(str(item) for item in _json_list(row["page_kinds_json"])),
        segments=tuple(
            dict(item) for item in _json_list(row["segments_json"]) if isinstance(item, dict)
        ),
        contributors=tuple(
            dict(item)
            for item in _json_list(row["contributors_json"])
            if isinstance(item, dict)
        ),
        summary_display_pt=str(row["summary_display_pt"] or ""),
        cumulative_summary=str(row["cumulative_summary"] or ""),
        source_conflicts=tuple(
            dict(item)
            for item in _json_list(row["source_conflicts_json"])
            if isinstance(item, dict)
        ),
        administrative_reason=str(row["administrative_reason"] or ""),
        primary_page_kind=str(row["primary_page_kind"] or "other"),
        status=str(row["status"] or "valid"),
        validation_issues=tuple(
            str(item) for item in _json_list(row["validation_issues_json"])
        ),
        context_reset=bool(row["context_reset"]),
        context_reset_confidence=float(row["context_reset_confidence"] or 0.0),
    )


def _analysis_from_stored(
    row: sqlite3.Row, hints: dict[str, Any]
) -> tuple[SimpleNamespace, dict[str, Any]]:
    static = _json_dict(row["static_analysis_json"])
    static["exact_start_candidates"] = list(hints.get("exact_start_candidates") or [])
    static["containing_work_candidates"] = list(
        hints.get("containing_work_candidates") or []
    )
    static["index_ambiguous"] = bool(
        hints.get("exact_start_ambiguous") or hints.get("range_ambiguous")
    )
    analysis = SimpleNamespace(
        exact_start_candidates=tuple(static["exact_start_candidates"]),
        containing_work_candidates=tuple(static["containing_work_candidates"]),
        scripture_candidates=tuple(static.get("scripture_candidates") or []),
    )
    return analysis, static


def plan_repair(
    con: sqlite3.Connection,
    *,
    run_id: int,
    volume_dir: Path,
    indices_db: Path,
) -> tuple[sqlite3.Row, list[dict[str, Any]], dict[str, Any]]:
    run = con.execute("SELECT * FROM resumo_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"Run inexistente: {run_id}")
    documento = str(run["documento"])
    if volume_dir.name != documento:
        raise ValueError(
            f"Volume {volume_dir.name} não corresponde ao documento {documento} do run"
        )
    rows = con.execute(
        "SELECT * FROM resumo_generations WHERE run_id=? ORDER BY pagina_num", (run_id,)
    ).fetchall()
    if not rows:
        raise ValueError(f"Run {run_id} não possui gerações")
    if any(int(row["is_current"] or 0) for row in rows):
        raise ValueError("O reparador recusa gerações já promovidas")

    pages = sorted((volume_dir / "text").glob("*.txt"), key=page_number)
    page_names = {path.name for path in pages}
    missing = [str(row["pagina_file"]) for row in rows if row["pagina_file"] not in page_names]
    if missing:
        raise ValueError(f"Arquivos OCR ausentes, primeiro exemplo: {missing[0]}")

    indices = open_indices_readonly(indices_db)
    try:
        hints_by_page = load_volume_index_hints(indices, documento, pages)
    finally:
        if indices is not None:
            indices.close()

    active_work_key = ""
    previous_chain = str(rows[0]["previous_chain_hash"] or "")
    changes: list[dict[str, Any]] = []
    blank_before = 0
    blank_after = 0
    state_changes: list[dict[str, Any]] = []
    previous_state = ""

    for row in rows:
        pagina_num = int(row["pagina_num"])
        hints = hints_by_page.get(pagina_num, {})
        analysis, static = _analysis_from_stored(row, hints)
        candidate = _candidate_from_row(row)
        blank_before += sum(not item.get("work_key") for item in candidate.segments)
        candidate, active_work_key, resolution = reconcile_candidate_work_keys(
            candidate,
            analysis,
            active_work_key,
        )
        blank_after += sum(not item.get("work_key") for item in candidate.segments)
        if active_work_key != previous_state:
            state_changes.append(
                {
                    "pagina_num": pagina_num,
                    "from": previous_state,
                    "to": active_work_key,
                    "source": resolution.get("source", ""),
                }
            )
            previous_state = active_work_key

        labels = canonical_labels_from_hints(hints)
        search_text = build_summary_search_text(
            candidate.summary_display_pt,
            candidate.segments,
            analysis,
            labels,
        )
        embedding_text = build_embedding_text(candidate.summary_display_pt, labels)
        segments_json = json.dumps(candidate.segments, ensure_ascii=False)
        issues_json = json.dumps(candidate.validation_issues, ensure_ascii=False)
        static_json = json.dumps(static, ensure_ascii=False)
        output_hash = sha256_text(
            candidate.summary_display_pt,
            candidate.cumulative_summary,
            json.dumps(candidate.segments, ensure_ascii=False, sort_keys=True),
        )
        chain_hash = sha256_text(previous_chain, row["source_hash"], output_hash)
        anchor_source_hash = sha256_text(
            row["source_hash"],
            resolution.get("work_key", ""),
            resolution.get("source", ""),
            resolution.get("confidence", 0.0),
            bool(resolution.get("possible_change")),
        )
        changed = any(
            (
                segments_json != str(row["segments_json"] or "[]"),
                search_text != str(row["search_text_pt"] or ""),
                embedding_text != str(row["embedding_text"] or ""),
                output_hash != str(row["output_hash"] or ""),
                previous_chain != str(row["previous_chain_hash"] or ""),
                chain_hash != str(row["chain_hash"] or ""),
                int(candidate.context_reset) != int(row["context_reset"] or 0),
                issues_json != str(row["validation_issues_json"] or "[]"),
                static_json != str(row["static_analysis_json"] or "{}"),
            )
        )
        changes.append(
            {
                "id": int(row["id"]),
                "pagina_num": pagina_num,
                "pagina_file": str(row["pagina_file"]),
                "changed": changed,
                "segments_json": segments_json,
                "search_text_pt": search_text,
                "embedding_text": embedding_text,
                "output_hash": output_hash,
                "previous_chain_hash": previous_chain,
                "chain_hash": chain_hash,
                "context_reset": int(candidate.context_reset),
                "context_reset_confidence": candidate.context_reset_confidence,
                "status": candidate.status,
                "validation_issues_json": issues_json,
                "static_analysis_json": static_json,
                "resolution": resolution,
                "anchor_source_hash": anchor_source_hash,
            }
        )
        previous_chain = chain_hash

    report = {
        "run_id": run_id,
        "documento": documento,
        "generations": len(rows),
        "changed_generations": sum(bool(item["changed"]) for item in changes),
        "blank_work_keys_before": blank_before,
        "blank_work_keys_after": blank_after,
        "filled_work_keys": blank_before - blank_after,
        "state_changes": state_changes,
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
            "CREATE TABLE resumo_context_anchors AS "
            "SELECT * FROM source.resumo_context_anchors WHERE documento=?",
            (documento,),
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
        backup.execute(
            "CREATE TABLE repair_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
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
                       SET segments_json=?, search_text_pt=?, embedding_text=?,
                           output_hash=?, previous_chain_hash=?, chain_hash=?,
                           context_reset=?, context_reset_confidence=?, status=?,
                           validation_issues_json=?, static_analysis_json=?,
                           embedding=NULL, embedding_dim=NULL, embedding_model=NULL,
                           embedding_source_hash=NULL, embedding_updated_at=NULL,
                           updated_at=CURRENT_TIMESTAMP
                     WHERE id=? AND run_id=? AND is_current=0
                    """,
                    (
                        item["segments_json"],
                        item["search_text_pt"],
                        item["embedding_text"],
                        item["output_hash"],
                        item["previous_chain_hash"],
                        item["chain_hash"],
                        item["context_reset"],
                        item["context_reset_confidence"],
                        item["status"],
                        item["validation_issues_json"],
                        item["static_analysis_json"],
                        item["id"],
                        run_id,
                    ),
                )
                con.execute(
                    "UPDATE resumo_translations SET status='stale' "
                    "WHERE generation_id=? AND status!='stale'",
                    (item["id"],),
                )
            resolution = item["resolution"]
            if resolution.get("work_key"):
                con.execute(
                    """
                    INSERT INTO resumo_context_anchors
                        (documento, pagina_num, pagina_file, work_key, confidence,
                         evidence_json, source_hash, is_trusted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(documento, pagina_num, source_hash) DO UPDATE SET
                        pagina_file=excluded.pagina_file,
                        work_key=excluded.work_key,
                        confidence=excluded.confidence,
                        evidence_json=excluded.evidence_json,
                        is_trusted=excluded.is_trusted
                    """,
                    (
                        documento,
                        item["pagina_num"],
                        item["pagina_file"],
                        resolution["work_key"],
                        float(resolution.get("confidence") or 0.0),
                        json.dumps(resolution, ensure_ascii=False, sort_keys=True),
                        item["anchor_source_hash"],
                        int(
                            resolution.get("source") == "index_exact_start"
                            and float(resolution.get("confidence") or 0.0) >= 0.70
                        ),
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
    parser.add_argument("--volume-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--indices-db", type=Path, default=DEFAULT_INDICES_DB)
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
    try:
        run, changes, report = plan_repair(
            con,
            run_id=args.run_id,
            volume_dir=args.volume_dir.resolve(),
            indices_db=args.indices_db.resolve(),
        )
        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = args.backup or (
                PROJECT_ROOT
                / "data"
                / "repair_backups"
                / f"resumo-run-{args.run_id}-work-keys-{stamp}.sqlite"
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
        con.close()


if __name__ == "__main__":
    main()
