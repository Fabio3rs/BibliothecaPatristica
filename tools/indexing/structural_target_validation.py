"""Validation helpers for applied structural-target repairs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .structural_target_repair import sha256_text


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _section_aliases(
    connection: sqlite3.Connection,
    volume_id: str,
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    rows = connection.execute(
        "SELECT section_key, raw_json FROM index_sections WHERE volume_id = ?",
        (volume_id,),
    ).fetchall()
    for canonical_key, raw_json in rows:
        keys = {str(canonical_key)}
        try:
            section_raw = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            section_raw = {}
        original_key = section_raw.get("original_section_key") if isinstance(section_raw, dict) else None
        if original_key:
            keys.add(str(original_key))
        for key in keys:
            previous = aliases.setdefault(key, str(canonical_key))
            if previous != canonical_key:
                raise ValueError(
                    f"ambiguous SQLite section alias for {volume_id}: {key!r}"
                )
    return aliases


def validate_applied_report(
    report_path: Path,
    *,
    database_path: Path | None = None,
) -> dict[str, Any]:
    report_path = report_path.resolve()
    report = _load_json(report_path)
    if report.get("operation") != "deterministic_structural_target_repair":
        raise ValueError("not a structural-target repair report")
    if report.get("mode") != "dry_run" or report.get("status", "complete") != "complete":
        raise ValueError("validation requires a complete dry-run report")
    if not report.get("apply_ready"):
        raise ValueError("report is not apply-ready")

    connection = sqlite3.connect(database_path.resolve()) if database_path is not None else None
    checked = 0
    checked_volumes = 0
    try:
        for volume in report.get("volumes") or []:
            patches = volume.get("patches") or []
            if not patches:
                continue
            payload_path = Path(str(volume.get("payload_file") or "")).resolve()
            payload = _load_json(payload_path)
            volume_id = str(volume.get("volume_id") or "")
            if str((payload.get("volume") or {}).get("volume_id") or "") != volume_id:
                raise ValueError(f"payload volume mismatch: {payload_path}")
            aliases = _section_aliases(connection, volume_id) if connection is not None else {}

            for patch in patches:
                section_index = int(patch["section_index"])
                entry_index = int(patch["entry_index"])
                section = payload["sections"][section_index]
                entry = section["entries"][entry_index]
                entry_raw = str(entry.get("entry_raw") or "")
                if sha256_text(entry_raw) != patch.get("entry_raw_sha256"):
                    raise ValueError(
                        f"entry text changed after report: {volume_id} section={section_index} entry={entry_index}"
                    )
                expected_target = patch.get("after_target_file")
                expected_evidence = patch.get("physical_target_evidence")
                current_evidence = (entry.get("raw_json") or {}).get("physical_target_evidence")
                if entry.get("target_file") != expected_target or current_evidence != expected_evidence:
                    raise ValueError(
                        f"payload does not contain the reported patch: {volume_id} "
                        f"section={section_index} entry={entry_index}"
                    )
                if expected_target:
                    target_path = Path(str(expected_target)).resolve()
                    if not target_path.is_file():
                        raise ValueError(f"reported target does not exist: {expected_target}")
                elif not (
                    isinstance(expected_evidence, dict)
                    and expected_evidence.get("status") == "unresolved"
                    and expected_evidence.get("method") == "reviewed_override"
                    and expected_evidence.get("disposition")
                    == "cleared_reviewed_invalid_legacy_target"
                ):
                    raise ValueError("a targetless patch is not an approved reviewed clear")

                if connection is not None:
                    raw_section_key = str(section.get("section_key") or "")
                    canonical_section_key = aliases.get(raw_section_key)
                    if canonical_section_key is None:
                        raise ValueError(
                            f"section missing from SQLite: {volume_id} {raw_section_key!r}"
                        )
                    entry_order = int(entry.get("entry_order", entry_index + 1))
                    rows = connection.execute(
                        """SELECT target_file, raw_json
                           FROM index_entries
                           WHERE section_key = ? AND entry_order = ? AND entry_raw = ?""",
                        (canonical_section_key, entry_order, entry_raw),
                    ).fetchall()
                    if len(rows) != 1:
                        raise ValueError(
                            f"expected one SQLite entry, found {len(rows)}: {volume_id} "
                            f"section={raw_section_key!r} order={entry_order}"
                        )
                    db_target, db_raw_json = rows[0]
                    try:
                        db_evidence = json.loads(db_raw_json).get("physical_target_evidence")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"invalid SQLite raw_json: {volume_id} section={raw_section_key!r} "
                            f"order={entry_order}"
                        ) from exc
                    if db_target != expected_target or db_evidence != expected_evidence:
                        raise ValueError(
                            f"SQLite does not contain the reported patch: {volume_id} "
                            f"section={raw_section_key!r} order={entry_order}"
                        )
                checked += 1
            checked_volumes += 1

        integrity = None
        foreign_key_violations = None
        if connection is not None:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"SQLite integrity_check failed: {integrity}")
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            if foreign_key_violations:
                raise ValueError(
                    f"SQLite foreign_key_check found {foreign_key_violations} violation(s)"
                )
        return {
            "status": "ok",
            "report": str(report_path),
            "volume_count": checked_volumes,
            "patch_count": checked,
            "database": str(database_path.resolve()) if database_path is not None else None,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_key_violations,
        }
    finally:
        if connection is not None:
            connection.close()
