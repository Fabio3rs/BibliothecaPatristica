#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "patristic_indices.db"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _count_selected(
    conn: sqlite3.Connection,
    table: str,
    volume_ids: list[str],
) -> int:
    placeholders = ",".join("?" for _ in volume_ids)
    if table == "index_entries":
        sql = (
            "SELECT COUNT(*) FROM index_entries e "
            "JOIN index_sections s ON s.section_key = e.section_key "
            f"WHERE s.volume_id IN ({placeholders})"
        )
    else:
        sql = f"SELECT COUNT(*) FROM {table} WHERE volume_id IN ({placeholders})"
    return int(conn.execute(sql, volume_ids).fetchone()[0])


def _database_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "volumes",
            "works",
            "index_sections",
            "index_entries",
            "runs",
        )
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Remove only manifest-selected rerun volumes from the index database. "
            "Foreign-key cascades remove their works, sections, and entries."
        )
    )
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    volume_ids = [
        str(volume_id)
        for volume_id in manifest.get("rerun_volume_ids") or []
    ]
    if not volume_ids:
        raise SystemExit("Manifest has no rerun_volume_ids")
    if len(volume_ids) != len(set(volume_ids)):
        raise SystemExit("Manifest contains duplicate rerun_volume_ids")
    if not args.backup.is_file():
        raise SystemExit(f"Required database backup not found: {args.backup}")

    db_path = args.db.resolve()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        integrity_before = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity_before != "ok":
            raise SystemExit(f"Database integrity check failed: {integrity_before}")
        before = _database_counts(conn)
        selected_before = {
            table: _count_selected(conn, table, volume_ids)
            for table in (
                "volumes",
                "works",
                "index_sections",
                "index_entries",
                "runs",
            )
        }
        if selected_before["volumes"] != len(volume_ids):
            raise SystemExit(
                "Database/manifest mismatch: "
                f"{selected_before['volumes']}/{len(volume_ids)} rerun volumes exist"
            )

        status = "dry_run"
        if args.apply:
            placeholders = ",".join("?" for _ in volume_ids)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"DELETE FROM runs WHERE volume_id IN ({placeholders})",
                    volume_ids,
                )
                conn.execute(
                    f"DELETE FROM volumes WHERE volume_id IN ({placeholders})",
                    volume_ids,
                )
                remaining = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM volumes "
                        f"WHERE volume_id IN ({placeholders})",
                        volume_ids,
                    ).fetchone()[0]
                )
                if remaining:
                    raise RuntimeError(
                        f"{remaining} selected volume rows remain after deletion"
                    )
                conn.commit()
                status = "applied"
            except BaseException:
                conn.rollback()
                raise

        after = _database_counts(conn)
        integrity_after = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = [
            list(row) for row in conn.execute("PRAGMA foreign_key_check")
        ]

    report = {
        "schema_version": 1,
        "status": status,
        "database": str(db_path),
        "manifest": str(args.manifest.resolve()),
        "backup": str(args.backup.resolve()),
        "selected_volume_count": len(volume_ids),
        "selected_volume_ids": volume_ids,
        "selected_rows_before": selected_before,
        "database_rows_before": before,
        "database_rows_after": after,
        "integrity_before": integrity_before,
        "integrity_after": integrity_after,
        "foreign_key_violations": foreign_key_violations,
    }
    _write_json_atomic(args.report.resolve(), report)
    print(json.dumps(report, ensure_ascii=False))
    if integrity_after != "ok" or foreign_key_violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
