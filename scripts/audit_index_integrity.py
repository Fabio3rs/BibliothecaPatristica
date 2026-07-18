#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_payload(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def materialized_counts(con: sqlite3.Connection) -> dict[str, dict[str, int]]:
    cur = con.cursor()
    rows = cur.execute(
        """
        WITH sec AS (
          SELECT volume_id, COUNT(*) AS sec_count
          FROM index_sections
          GROUP BY volume_id
        ),
        ent AS (
          SELECT s.volume_id, COUNT(e.id) AS ent_count
          FROM index_sections s
          LEFT JOIN index_entries e ON e.section_key = s.section_key
          GROUP BY s.volume_id
        ),
        wk AS (
          SELECT volume_id, COUNT(*) AS work_count
          FROM works
          GROUP BY volume_id
        )
        SELECT v.volume_id,
               COALESCE(wk.work_count, 0),
               COALESCE(sec.sec_count, 0),
               COALESCE(ent.ent_count, 0)
        FROM volumes v
        LEFT JOIN wk ON wk.volume_id = v.volume_id
        LEFT JOIN sec ON sec.volume_id = v.volume_id
        LEFT JOIN ent ON ent.volume_id = v.volume_id
        """
    ).fetchall()
    return {
        volume_id: {"works": works, "sections": sections, "entries": entries}
        for volume_id, works, sections, entries in rows
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit payload/db integrity for patristic index extraction.")
    ap.add_argument("--db", type=Path, default=Path("data/patristic_indices.db"))
    ap.add_argument("--payload-dir", type=Path, default=Path("data/index_payloads"))
    ap.add_argument("--report-json", type=Path)
    args = ap.parse_args()

    payload_files = sorted(args.payload_dir.glob("*_indices.json"))
    conn = sqlite3.connect(args.db)
    db_counts = materialized_counts(conn)
    cur = conn.cursor()

    section_key_counter: Counter[str] = Counter()
    section_key_vols: defaultdict[str, list[str]] = defaultdict(list)
    work_key_counter: Counter[str] = Counter()
    work_key_vols: defaultdict[str, list[str]] = defaultdict(list)

    volumes_with_section_owner_mismatch: dict[str, list[dict[str, Any]]] = {}
    volumes_with_work_owner_mismatch: dict[str, list[dict[str, Any]]] = {}
    volumes_with_materialization_mismatch: dict[str, dict[str, Any]] = {}

    volumes_scanned = 0
    sections_checked = 0
    works_checked = 0

    for path in payload_files:
        volume_id = path.name[:-13]
        payload = load_payload(path)
        if payload is None:
            continue
        volumes_scanned += 1

        payload_work_count = 0
        payload_section_count = 0
        payload_entry_count = 0

        work_mismatches: list[dict[str, Any]] = []
        for work in payload.get("works", []):
            if not isinstance(work, dict):
                continue
            payload_work_count += 1
            work_key = work.get("work_key")
            if not work_key:
                continue
            work_key = str(work_key)
            works_checked += 1
            work_key_counter[work_key] += 1
            work_key_vols[work_key].append(volume_id)
            row = cur.execute("SELECT volume_id FROM works WHERE work_key = ?", (work_key,)).fetchone()
            if row and row[0] != volume_id:
                work_mismatches.append({"work_key": work_key, "db_owner": row[0]})
        if work_mismatches:
            volumes_with_work_owner_mismatch[volume_id] = work_mismatches

        section_mismatches: list[dict[str, Any]] = []
        for section in payload.get("sections", []):
            if not isinstance(section, dict):
                continue
            payload_section_count += 1
            entries = section.get("entries") or []
            payload_entry_count += len(entries)
            section_key = section.get("section_key")
            if not section_key:
                continue
            section_key = str(section_key)
            sections_checked += 1
            section_key_counter[section_key] += 1
            section_key_vols[section_key].append(volume_id)
            row = cur.execute("SELECT volume_id FROM index_sections WHERE section_key = ?", (section_key,)).fetchone()
            if row and row[0] != volume_id:
                section_mismatches.append({"section_key": section_key, "db_owner": row[0]})
        if section_mismatches:
            volumes_with_section_owner_mismatch[volume_id] = section_mismatches

        db_row = db_counts.get(volume_id, {"works": 0, "sections": 0, "entries": 0})
        if (
            db_row["works"] != payload_work_count
            or db_row["sections"] != payload_section_count
            or db_row["entries"] != payload_entry_count
        ):
            volumes_with_materialization_mismatch[volume_id] = {
                "payload": {
                    "works": payload_work_count,
                    "sections": payload_section_count,
                    "entries": payload_entry_count,
                },
                "db": db_row,
            }

    duplicate_work_keys = {
        key: vols
        for key, vols in work_key_vols.items()
        if work_key_counter[key] > 1
    }
    duplicate_section_keys = {
        key: vols
        for key, vols in section_key_vols.items()
        if section_key_counter[key] > 1
    }

    summary = {
        "db": str(args.db),
        "payload_dir": str(args.payload_dir),
        "volumes_scanned": volumes_scanned,
        "works_checked": works_checked,
        "sections_checked": sections_checked,
        "duplicate_work_keys": len(duplicate_work_keys),
        "duplicate_section_keys": len(duplicate_section_keys),
        "volumes_with_work_owner_mismatch": len(volumes_with_work_owner_mismatch),
        "volumes_with_section_owner_mismatch": len(volumes_with_section_owner_mismatch),
        "volumes_with_materialization_mismatch": len(volumes_with_materialization_mismatch),
    }
    report = {
        "summary": summary,
        "duplicate_work_keys": duplicate_work_keys,
        "duplicate_section_keys": duplicate_section_keys,
        "volumes_with_work_owner_mismatch": volumes_with_work_owner_mismatch,
        "volumes_with_section_owner_mismatch": volumes_with_section_owner_mismatch,
        "volumes_with_materialization_mismatch": volumes_with_materialization_mismatch,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    conn.close()


if __name__ == "__main__":
    main()
