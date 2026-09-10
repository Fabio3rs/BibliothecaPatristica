#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAYLOAD_DIR = PROJECT_ROOT / "data" / "index_payloads"
DEFAULT_AUDIT_DIR = PROJECT_ROOT / "data" / "index_payload_audits"
DEFAULT_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
PAGE_FILE_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def iter_entries(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    found_nested = False
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for entry in section.get("entries") or []:
            if isinstance(entry, dict):
                found_nested = True
                yield entry
    if found_nested:
        return
    for entry in payload.get("entries") or []:
        if isinstance(entry, dict):
            yield entry


def has_editorial_reference(entry: dict[str, Any]) -> bool:
    return any(
        value is not None and str(value).strip()
        for value in (
            entry.get("page_ref_raw"),
            entry.get("page_ref_int"),
            entry.get("page_ref_col"),
        )
    )


def has_resolved_target(entry: dict[str, Any]) -> bool:
    target_file = str(entry.get("target_file") or "").strip()
    return bool(target_file and PAGE_FILE_RE.search(target_file))


def database_stats(db_path: Path) -> dict[str, dict[str, int]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        entry_counts = {
            str(volume_id): int(count)
            for volume_id, count in con.execute(
                """
                SELECT s.volume_id, COUNT(*)
                FROM index_entries e
                JOIN index_sections s ON s.section_key = e.section_key
                GROUP BY s.volume_id
                """
            )
        }
        run_counts = {
            str(volume_id): int(count)
            for volume_id, count in con.execute(
                "SELECT volume_id, COUNT(*) FROM runs GROUP BY volume_id"
            )
        }
        exact_duplicate_counts = {
            str(volume_id): int(count)
            for volume_id, count in con.execute(
                """
                SELECT volume_id, SUM(row_count - 1)
                FROM (
                    SELECT s.volume_id, COUNT(*) AS row_count
                    FROM index_entries e
                    JOIN index_sections s ON s.section_key = e.section_key
                    GROUP BY s.volume_id, e.section_key, e.entry_order, e.entry_raw,
                             COALESCE(e.target_raw, ''), COALESCE(e.target_file, ''),
                             COALESCE(e.page_ref_raw, ''), COALESCE(e.page_ref_int, -1),
                             COALESCE(e.page_ref_col, ''), COALESCE(e.note_raw, ''),
                             COALESCE(e.normalized_target, ''), COALESCE(e.confidence, -1),
                             e.raw_json
                    HAVING COUNT(*) > 1
                ) duplicated
                GROUP BY volume_id
                """
            )
        }
    return {
        "entry_counts": entry_counts,
        "run_counts": run_counts,
        "exact_duplicate_counts": exact_duplicate_counts,
    }


def load_evidence(audit_dir: Path, volume_id: str) -> dict[str, Any]:
    path = audit_dir / f"{volume_id}_indices_evidence.json"
    if not path.is_file():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    return report if report.get("pipeline_kind") == "general" else {}


def audit_payload(
    path: Path,
    audit_dir: Path,
    db: dict[str, dict[str, int]],
    min_verified_ratio: float,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    volume_id = str((payload.get("volume") or {}).get("volume_id") or path.name[:-13])
    entries = list(iter_entries(payload))
    entry_keys = [str(entry.get("entry_key") or "").strip() for entry in entries]
    key_counts = Counter(key for key in entry_keys if key)
    missing_entry_key_count = sum(not key for key in entry_keys)
    duplicate_entry_key_count = sum(count - 1 for count in key_counts.values() if count > 1)
    editorial_count = sum(has_editorial_reference(entry) for entry in entries)
    resolved_target_count = sum(has_resolved_target(entry) for entry in entries)
    target_file_count = sum(bool(str(entry.get("target_file") or "").strip()) for entry in entries)
    locator_not_run_count = 0
    for entry in entries:
        raw_json = entry.get("raw_json")
        if isinstance(raw_json, dict) and raw_json.get("target_locator_status") == "not_run_in_chunk_phase":
            locator_not_run_count += 1

    evidence = load_evidence(audit_dir, volume_id)
    sampled = int(evidence.get("sampled_entry_count") or 0)
    verified_ratio = evidence.get("verified_ratio")
    evidence_entry_count = evidence.get("payload_entry_count")
    evidence_stale = evidence_entry_count is not None and int(evidence_entry_count) != len(entries)
    payload_upgrade_reasons: list[str] = []
    if missing_entry_key_count:
        payload_upgrade_reasons.append("missing_stable_entry_keys")
    if duplicate_entry_key_count:
        payload_upgrade_reasons.append("duplicate_entry_keys")
    extraction_reasons: list[str] = []
    if not evidence_stale and int(evidence.get("unjustified_empty_list_section_count") or 0):
        extraction_reasons.append("unjustified_empty_list_sections")
    if not evidence_stale and int(evidence.get("segmentation_suspect_count") or 0):
        extraction_reasons.append("segmentation_suspects")
    if (
        sampled
        and not evidence_stale
        and verified_ratio is not None
        and float(verified_ratio) < min_verified_ratio
    ):
        extraction_reasons.append("low_ocr_evidence_ratio")

    db_entry_count = db["entry_counts"].get(volume_id, 0)
    db_run_count = db["run_counts"].get(volume_id, 0)
    exact_duplicate_count = db["exact_duplicate_counts"].get(volume_id, 0)
    materialization_reasons: list[str] = []
    if db_entry_count != len(entries):
        materialization_reasons.append("database_payload_entry_count_mismatch")
    if exact_duplicate_count:
        materialization_reasons.append("exact_duplicate_database_entries")
    if db_run_count > 1:
        materialization_reasons.append("multiple_database_runs")

    return {
        "volume_id": volume_id,
        "payload_file": str(path),
        "entries_total": len(entries),
        "entries_with_editorial_reference": editorial_count,
        "editorial_reference_coverage": round(editorial_count / len(entries), 4) if entries else 0.0,
        "entries_with_target_file": target_file_count,
        "entries_with_resolved_target": resolved_target_count,
        "target_coverage": round(resolved_target_count / len(entries), 4) if entries else 0.0,
        "missing_entry_key_count": missing_entry_key_count,
        "duplicate_entry_key_count": duplicate_entry_key_count,
        "target_locator_not_run_count": locator_not_run_count,
        "evidence_sampled_entry_count": sampled,
        "evidence_verified_ratio": verified_ratio,
        "evidence_payload_entry_count": evidence_entry_count,
        "evidence_stale": evidence_stale,
        "unjustified_empty_list_section_count": int(evidence.get("unjustified_empty_list_section_count") or 0),
        "segmentation_suspect_count": int(evidence.get("segmentation_suspect_count") or 0),
        "database_entry_count": db_entry_count,
        "database_run_count": db_run_count,
        "database_exact_duplicate_entry_count": exact_duplicate_count,
        "payload_upgrade_reasons": payload_upgrade_reasons,
        "extraction_rerun_reasons": extraction_reasons,
        "database_reimport_reasons": materialization_reasons,
        "requires_payload_upgrade": bool(payload_upgrade_reasons),
        "requires_extraction_rerun": bool(extraction_reasons),
        "requires_database_reimport": bool(materialization_reasons),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Audit general-index payload quality separately from editorial-reference and "
            "physical-viewer target coverage. The database is opened read-only."
        )
    )
    ap.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    ap.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-verified-ratio", type=float, default=0.9)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    db = database_stats(args.db.resolve())
    reports = [
        audit_payload(path, args.audit_dir.resolve(), db, args.min_verified_ratio)
        for path in sorted(args.payload_dir.resolve().glob("*_indices.json"))
    ]
    extraction_rerun_ids = [r["volume_id"] for r in reports if r["requires_extraction_rerun"]]
    payload_upgrade_ids = [r["volume_id"] for r in reports if r["requires_payload_upgrade"]]
    database_reimport_ids = [r["volume_id"] for r in reports if r["requires_database_reimport"]]
    rerun_ids = sorted(set(extraction_rerun_ids) | set(database_reimport_ids))
    entries_total = sum(r["entries_total"] for r in reports)
    editorial_total = sum(r["entries_with_editorial_reference"] for r in reports)
    target_total = sum(r["entries_with_resolved_target"] for r in reports)
    output = {
        "schema_version": 1,
        "pipeline_kind": "general",
        "generated_on": date.today().isoformat(),
        "definition": {
            "editorial_reference_coverage": "entries with any page_ref_raw/page_ref_int/page_ref_col divided by entries_total",
            "target_coverage": "entries whose target_file contains a physical OCR page suffix divided by entries_total",
            "quality_warning": "Neither coverage is extraction completeness; structural entries and internal references may legitimately have no viewer target.",
        },
        "payload_count": len(reports),
        "entries_total": entries_total,
        "entries_with_editorial_reference": editorial_total,
        "editorial_reference_coverage": round(editorial_total / entries_total, 4) if entries_total else 0.0,
        "entries_with_resolved_target": target_total,
        "target_coverage": round(target_total / entries_total, 4) if entries_total else 0.0,
        "extraction_rerun_volume_count": len(extraction_rerun_ids),
        "extraction_rerun_volume_ids": extraction_rerun_ids,
        "payload_upgrade_volume_count": len(payload_upgrade_ids),
        "payload_upgrade_volume_ids": payload_upgrade_ids,
        "database_reimport_volume_count": len(database_reimport_ids),
        "database_reimport_volume_ids": database_reimport_ids,
        "rerun_volume_count": len(rerun_ids),
        "rerun_volume_ids": rerun_ids,
        "evidence_refresh_volume_ids": [r["volume_id"] for r in reports if r["evidence_stale"]],
        "recommended_rerun_command": (
            "python scripts/run_index_extraction.py --all-volumes --skip-done "
            "--fresh-extraction --helper-workers 12 --chunk-workers 12 --continue-on-error"
        ),
        "volumes": reports,
    }
    write_json_atomic(args.output.resolve(), output)
    print(
        json.dumps(
            {
                "payload_count": len(reports),
                "entries_total": entries_total,
                "editorial_reference_coverage": output["editorial_reference_coverage"],
                "target_coverage": output["target_coverage"],
                "extraction_rerun_volume_count": len(extraction_rerun_ids),
                "payload_upgrade_volume_count": len(payload_upgrade_ids),
                "database_reimport_volume_count": len(database_reimport_ids),
                "rerun_volume_count": len(rerun_ids),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
