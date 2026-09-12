#!/usr/bin/env python3
"""Audit existing alphabetical payloads against the DB and current OCR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.indexing.alphabetical_material_audit import audit_extracted_material


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-audit existing alphabetical payloads, alphabetical_indices.db, "
            "and current OCR. Read-only; deterministic changes are emitted as proposals."
        )
    )
    parser.add_argument("--volume-id", action="append", dest="volume_ids", required=True)
    parser.add_argument(
        "--payload-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "alphabetical_index_payloads",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "data" / "alphabetical_indices.db",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "alphabetical_material_audits",
    )
    parser.add_argument(
        "--max-refs",
        type=int,
        default=50,
        help="OCR-audit this many evenly distributed refs per volume; 0 means all.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Run only the fast payload/DB structural comparison.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    errors = []
    for volume_id in dict.fromkeys(args.volume_ids):
        payload_path = args.payload_dir / f"{volume_id}_alphabetical_indices.json"
        if not payload_path.exists():
            errors.append(
                {
                    "volume_id": volume_id,
                    "error": f"payload not found: {payload_path}",
                }
            )
            continue
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            report = audit_extracted_material(
                payload,
                db_path=args.db,
                max_refs=None if args.max_refs == 0 else args.max_refs,
                workers=max(1, args.workers),
                run_ocr=not args.no_ocr,
            )
            report["payload_file"] = str(payload_path)
            report_path = args.output_dir / f"{volume_id}_audit.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            reports.append(report)
        except Exception as exc:
            errors.append(
                {
                    "volume_id": volume_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    action_totals: dict[str, int] = {}
    db_action_totals: dict[str, int] = {}
    issue_totals: dict[str, int] = {}
    for report in reports:
        for key, value in report.get("issue_counts", {}).items():
            issue_totals[key] = issue_totals.get(key, 0) + int(value)
        mechanical = report.get("mechanical") or {}
        for key, value in mechanical.get("action_counts", {}).items():
            action_totals[key] = action_totals.get(key, 0) + int(value)
        for key, value in mechanical.get("db_action_counts", {}).items():
            db_action_totals[key] = db_action_totals.get(key, 0) + int(value)
    summary = {
        "schema_version": 2,
        "mode": "read_only_audit",
        "volume_count": len(reports),
        "error_count": len(errors),
        "issue_totals": issue_totals,
        "action_totals": action_totals,
        "db_action_totals": db_action_totals,
        "report_files": [
            str(args.output_dir / f"{report['volume_id']}_audit.json")
            for report in reports
        ],
        "errors": errors,
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
