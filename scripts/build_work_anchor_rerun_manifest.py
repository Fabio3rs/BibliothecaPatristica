#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAYLOAD_DIR = PROJECT_ROOT / "data" / "index_payloads"
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


def _invalid_ranges(payload: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    for index, work in enumerate(payload.get("works") or []):
        if not isinstance(work, dict):
            continue
        start = work.get("start_page")
        end = work.get("end_page")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and start > end
        ):
            invalid.append(str(work.get("work_key") or f"works[{index}]"))
    return invalid


def _markers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for index, work in enumerate(payload.get("works") or []):
        if not isinstance(work, dict):
            continue
        raw_json = work.get("raw_json")
        marker = (
            raw_json.get("work_anchor_rerun")
            if isinstance(raw_json, dict)
            else None
        )
        if not isinstance(marker, dict):
            continue
        markers.append(
            {
                "work_key": str(work.get("work_key") or f"works[{index}]"),
                "title_raw": work.get("title_raw"),
                "status": marker.get("status"),
                "reason": marker.get("reason"),
                "inspection_reasons": marker.get("inspection_reasons") or [],
                "candidate_count": len(
                    ((marker.get("locator") or {}).get("candidates") or [])
                ),
                "has_anchor_locator_review": isinstance(
                    marker.get("anchor_locator_review"),
                    dict,
                ),
            }
        )
    return markers


def _database_volume_ids(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            str(row[0])
            for row in conn.execute("SELECT volume_id FROM volumes")
        }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Build the exact rerun set from persisted work_anchor_rerun markers "
            "and invalid editorial ranges."
        )
    )
    ap.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--audit-report", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    audit_by_volume: dict[str, dict[str, Any]] = {}
    if args.audit_report and args.audit_report.is_file():
        audit = json.loads(args.audit_report.read_text(encoding="utf-8"))
        audit_by_volume = {
            str(report.get("volume_id")): report
            for report in audit.get("reports") or []
            if isinstance(report, dict) and report.get("volume_id")
        }

    db_volume_ids = _database_volume_ids(args.db.resolve())
    volume_reports: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for path in sorted(args.payload_dir.resolve().glob("*_indices.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        volume = payload.get("volume") or {}
        volume_id = str(volume.get("volume_id") or path.name[:-13])
        markers = _markers(payload)
        invalid_ranges = _invalid_ranges(payload)
        audit_report = audit_by_volume.get(volume_id) or {}
        for marker in markers:
            reason_counts[str(marker.get("reason") or "unspecified")] += 1
        requires_rerun = bool(markers or invalid_ranges)
        volume_reports.append(
            {
                "volume_id": volume_id,
                "payload_file": str(path),
                "in_database_before_cleanup": volume_id in db_volume_ids,
                "requires_rerun": requires_rerun,
                "marker_count": len(markers),
                "invalid_ranges": invalid_ranges,
                "deterministic_change_count": int(
                    audit_report.get("changed_count") or 0
                ),
                "payload_mutation_count": int(
                    audit_report.get("payload_mutation_count") or 0
                ),
                "markers": markers,
            }
        )

    rerun = [
        report["volume_id"]
        for report in volume_reports
        if report["requires_rerun"]
    ]
    clean_corrected = [
        report["volume_id"]
        for report in volume_reports
        if not report["requires_rerun"]
        and report["deterministic_change_count"] > 0
    ]
    manifest = {
        "schema_version": 1,
        "payload_dir": str(args.payload_dir.resolve()),
        "database": str(args.db.resolve()),
        "payload_count": len(volume_reports),
        "database_volume_count_before_cleanup": len(db_volume_ids),
        "rerun_volume_count": len(rerun),
        "rerun_marker_count": sum(
            int(report["marker_count"]) for report in volume_reports
        ),
        "invalid_range_volume_count": sum(
            bool(report["invalid_ranges"]) for report in volume_reports
        ),
        "clean_corrected_volume_count": len(clean_corrected),
        "rerun_reason_counts": dict(sorted(reason_counts.items())),
        "rerun_volume_ids": rerun,
        "clean_corrected_volume_ids": clean_corrected,
        "recommended_command": (
            "python scripts/run_index_extraction.py --all-volumes "
            "--skip-done --fresh-extraction --helper-workers 12 --chunk-workers 12 "
            "--continue-on-error"
        ),
        "volumes": volume_reports,
    }
    _write_json_atomic(args.output.resolve(), manifest)
    print(
        json.dumps(
            {
                "payload_count": manifest["payload_count"],
                "rerun_volume_count": manifest["rerun_volume_count"],
                "rerun_marker_count": manifest["rerun_marker_count"],
                "invalid_range_volume_count": (
                    manifest["invalid_range_volume_count"]
                ),
                "clean_corrected_volume_count": (
                    manifest["clean_corrected_volume_count"]
                ),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
