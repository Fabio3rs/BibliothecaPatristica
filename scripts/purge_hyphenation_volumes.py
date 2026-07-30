#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from audit_hyphenation_volumes import finalize_report, scan_alphabetical, scan_patristic


DEFAULT_DBS = [Path("data/alphabetical_indices.db"), Path("data/patristic_indices.db")]


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def backup_db(db_path: Path, suffix: str) -> Path:
    backup_path = db_path.with_name(f"{db_path.stem}.before_hyphen_purge_{suffix}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def delete_count(con: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    cur = con.execute(sql, params)
    return cur.rowcount if cur.rowcount != -1 else 0


def purge_alphabetical(db_path: Path, volume_ids: list[str], *, apply: bool) -> dict[str, int]:
    if not volume_ids:
        return {}
    ph = placeholders(volume_ids)
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        counts["alphabetical_scripture_refs"] = delete_count(
            con,
            f"""
            DELETE FROM alphabetical_scripture_refs
            WHERE entry_key IN (
                SELECT e.entry_key
                FROM alphabetical_entries e
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id IN ({ph})
            )
            """,
            tuple(volume_ids),
        )
        counts["alphabetical_refs"] = delete_count(
            con,
            f"""
            DELETE FROM alphabetical_refs
            WHERE entry_key IN (
                SELECT e.entry_key
                FROM alphabetical_entries e
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id IN ({ph})
            )
            """,
            tuple(volume_ids),
        )
        counts["alphabetical_entries"] = delete_count(
            con,
            f"""
            DELETE FROM alphabetical_entries
            WHERE section_key IN (
                SELECT section_key FROM alphabetical_sections WHERE volume_id IN ({ph})
            )
            """,
            tuple(volume_ids),
        )
        counts["alphabetical_nodes"] = delete_count(
            con,
            f"""
            DELETE FROM alphabetical_nodes
            WHERE section_key IN (
                SELECT section_key FROM alphabetical_sections WHERE volume_id IN ({ph})
            )
            """,
            tuple(volume_ids),
        )
        counts["alphabetical_sections"] = delete_count(
            con,
            f"DELETE FROM alphabetical_sections WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        counts["alphabetical_runs"] = delete_count(
            con,
            f"DELETE FROM alphabetical_runs WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        counts["alphabetical_volumes"] = delete_count(
            con,
            f"DELETE FROM alphabetical_volumes WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        if apply:
            con.commit()
        else:
            con.rollback()
    return counts


def purge_patristic(db_path: Path, volume_ids: list[str], *, apply: bool) -> dict[str, int]:
    if not volume_ids:
        return {}
    ph = placeholders(volume_ids)
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        counts["index_entries"] = delete_count(
            con,
            f"""
            DELETE FROM index_entries
            WHERE section_key IN (
                SELECT section_key FROM index_sections WHERE volume_id IN ({ph})
            )
            """,
            tuple(volume_ids),
        )
        counts["index_sections"] = delete_count(
            con,
            f"DELETE FROM index_sections WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        counts["works"] = delete_count(
            con,
            f"DELETE FROM works WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        counts["runs"] = delete_count(
            con,
            f"DELETE FROM runs WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        counts["volumes"] = delete_count(
            con,
            f"DELETE FROM volumes WHERE volume_id IN ({ph})",
            tuple(volume_ids),
        )
        if apply:
            con.commit()
        else:
            con.rollback()
    return counts


def audit_db(db_path: Path) -> dict[str, Any]:
    label = "alphabetical" if db_path.name.startswith("alphabetical_") else "patristic"
    raw_result = scan_alphabetical(db_path) if label == "alphabetical" else scan_patristic(db_path)
    return finalize_report(db_path, label, raw_result)


def main() -> None:
    ap = argparse.ArgumentParser(description="Remove contaminated volumes from index SQLite databases.")
    ap.add_argument("--db", dest="dbs", action="append", type=Path, help="Database to purge. Repeat for more than one DB.")
    ap.add_argument("--apply", action="store_true", help="Actually delete rows. Without this flag, only reports planned deletion.")
    ap.add_argument("--no-backup", action="store_true", help="Do not create a database backup before applying deletion.")
    args = ap.parse_args()

    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    dbs = args.dbs or DEFAULT_DBS
    for db_path in dbs:
        report = audit_db(db_path)
        volume_ids = [volume["volume_id"] for volume in report["volumes"]]
        backup_path = None
        if args.apply and volume_ids and not args.no_backup:
            backup_path = backup_db(db_path, suffix)
        if report["label"] == "alphabetical":
            counts = purge_alphabetical(db_path, volume_ids, apply=args.apply)
        else:
            counts = purge_patristic(db_path, volume_ids, apply=args.apply)
        action = "deleted" if args.apply else "would_delete"
        print(
            {
                "db": str(db_path),
                "label": report["label"],
                "action": action,
                "backup": str(backup_path) if backup_path else None,
                "contaminated_volumes": len(volume_ids),
                "row_counts": counts,
            }
        )


if __name__ == "__main__":
    main()
