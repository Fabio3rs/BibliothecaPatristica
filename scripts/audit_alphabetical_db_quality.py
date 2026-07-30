#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

from alphabetical_index_db import (
    ALPHABETICAL_DB_SCHEMA_VERSION,
    DEFAULT_DB,
    connect_db,
    init_schema,
    now_iso,
    refresh_volume_quality,
)

AUDITOR_VERSION = 3
SAMPLE_LIMIT = 50
SCRIPTURE_ENTRY_KINDS = {"scripture_citation", "scripture_pericope"}
PAGE_SPECIFIC_EVIDENCE_KINDS = {
    "cited_page_match",
    "direct_editorial_page",
    "editorial_header_match",
    "editorial_page_match",
    "header_pair",
    "neighbor_fit",
    "neighbor_sequence",
    "page_number_match",
    "pagination_sequence",
}
PHYSICAL_FILE_NUMBER_RE = re.compile(
    r"(?:^|[-_])(\d+)(?:\.txt)?(?:\.gz)?$",
    re.IGNORECASE,
)


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def physical_file_number(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    match = PHYSICAL_FILE_NUMBER_RE.search(Path(value.strip()).name)
    return int(match.group(1)) if match else None


def target_is_inside_section(
    target_file: Any,
    file_start: Any,
    file_end: Any,
) -> bool:
    if not isinstance(target_file, str) or not target_file.strip():
        return False

    target = target_file.strip()
    boundaries = [
        value.strip()
        for value in (file_start, file_end)
        if isinstance(value, str) and value.strip()
    ]
    if any(target == value for value in boundaries):
        return True
    if any(Path(target).name == Path(value).name for value in boundaries):
        return True

    target_number = physical_file_number(target)
    start_number = physical_file_number(file_start)
    end_number = physical_file_number(file_end)
    if target_number is None or start_number is None or end_number is None:
        return False
    low, high = sorted((start_number, end_number))
    return low <= target_number <= high


def is_source_only(entry_raw_json: Any, section_raw_json: Any) -> bool:
    for raw_json in (entry_raw_json, section_raw_json):
        if parse_json_object(raw_json).get("material_reference_mode") == "source_only":
            return True
    return False


def has_verified_locator_evidence(raw_json: Any) -> bool:
    locator = parse_json_object(raw_json).get("compact_locator")
    if not isinstance(locator, dict) or locator.get("status") != "resolved":
        return False
    evidence = locator.get("evidence")
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        if kind in PAGE_SPECIFIC_EVIDENCE_KINDS:
            return True
    return False


def _sample(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return items[:SAMPLE_LIMIT]


def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_has_column(
    con: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    if not _table_exists(con, table_name):
        return False
    return any(
        str(row["name"]) == column_name
        for row in con.execute(f"PRAGMA table_info({table_name})")
    )


def _capture_preinit_deterministic_links(
    con: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    required_tables = {
        "alphabetical_refs",
        "alphabetical_scripture_refs",
        "alphabetical_entries",
        "alphabetical_sections",
    }
    if not all(_table_exists(con, table) for table in required_tables):
        return {}
    has_link_column = _table_has_column(
        con,
        "alphabetical_refs",
        "scripture_ref_order",
    )
    missing_predicate = (
        "r.scripture_ref_order IS NULL" if has_link_column else "1 = 1"
    )
    rows = con.execute(
        f"""
        SELECT s.volume_id, r.ref_id, r.entry_key, r.ref_order,
               MIN(sr.ref_order) AS scripture_ref_order,
               COUNT(sr.scripture_ref_id) AS scripture_ref_count
        FROM alphabetical_refs r
        JOIN alphabetical_entries e ON e.entry_key = r.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        JOIN alphabetical_scripture_refs sr ON sr.entry_key = r.entry_key
        WHERE {missing_predicate}
        GROUP BY s.volume_id, r.ref_id, r.entry_key, r.ref_order
        HAVING COUNT(sr.scripture_ref_id) = 1
        ORDER BY s.volume_id, r.entry_key, r.ref_order
        """
    ).fetchall()
    by_volume: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_volume.setdefault(str(row["volume_id"]), []).append(
            {
                "ref_id": int(row["ref_id"]),
                "entry_key": str(row["entry_key"]),
                "ref_order": int(row["ref_order"]),
                "scripture_ref_count": 1,
                "scripture_ref_order": int(row["scripture_ref_order"]),
                "repaired_by_schema_migration": True,
            }
        )
    return by_volume


def _restore_preinit_links_in_snapshot(
    snapshot: dict[str, Any],
    links: list[dict[str, Any]],
) -> None:
    issues = snapshot["issues"]["missing_scripture_links"]
    known_ref_ids = {int(item["ref_id"]) for item in issues}
    issues.extend(
        item for item in links if int(item["ref_id"]) not in known_ref_ids
    )
    issues.sort(key=lambda item: (str(item["entry_key"]), int(item["ref_order"])))
    snapshot["issue_counts"]["missing_scripture_links"] = len(issues)
    snapshot["issue_samples"]["missing_scripture_links"] = _sample(issues)


def _volume_snapshot(con: sqlite3.Connection, volume_id: str) -> dict[str, Any]:
    extraction_counts = con.execute(
        """
        SELECT
            COUNT(DISTINCT s.section_key) AS section_count,
            COUNT(e.entry_key) AS entry_count
        FROM alphabetical_volumes v
        LEFT JOIN alphabetical_sections s ON s.volume_id = v.volume_id
        LEFT JOIN alphabetical_entries e ON e.section_key = s.section_key
        WHERE v.volume_id = ?
        """,
        (volume_id,),
    ).fetchone()
    latest_run = con.execute(
        """
        SELECT raw_json
        FROM alphabetical_runs
        WHERE volume_id = ? AND status = 'imported'
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (volume_id,),
    ).fetchone()
    coverage: dict[str, Any] = {}
    if latest_run and isinstance(latest_run["raw_json"], str):
        try:
            run_payload = json.loads(latest_run["raw_json"])
        except json.JSONDecodeError:
            run_payload = {}
        if isinstance(run_payload, dict) and isinstance(
            run_payload.get("coverage"), dict
        ):
            coverage = run_payload["coverage"]
    evidence_files = coverage.get("evidence_files")
    entries_status = coverage.get("entries_status")
    section_count = int(extraction_counts["section_count"] or 0)
    empty_status_matches_structure = (
        (entries_status == "no_index_section" and section_count == 0)
        or (entries_status == "no_line_items" and section_count > 0)
    )
    confirmed_empty = (
        empty_status_matches_structure
        and isinstance(coverage.get("entries_status_reason"), str)
        and bool(coverage["entries_status_reason"].strip())
        and isinstance(evidence_files, list)
        and bool(evidence_files)
        and all(isinstance(item, str) and item.strip() for item in evidence_files)
    )
    entry_count = int(extraction_counts["entry_count"] or 0)
    unverified_empty_extraction = (
        [
            {
                "volume_id": volume_id,
                "section_count": section_count,
                "entries_status": entries_status,
                "evidence_file_count": (
                    len(evidence_files) if isinstance(evidence_files, list) else 0
                ),
            }
        ]
        if entry_count == 0 and not confirmed_empty
        else []
    )
    unrecoverable_extraction = (
        [
            {
                "volume_id": volume_id,
                "entries_status": entries_status,
                "entries_status_reason": coverage.get("entries_status_reason"),
            }
        ]
        if entries_status == "unrecoverable_ocr"
        else []
    )
    coverage_locator_partial = (
        [
            {
                "volume_id": volume_id,
                "locator_status": coverage.get("locator_status"),
                "locator_total_refs": coverage.get("locator_total_refs"),
                "locator_resolved_refs": coverage.get("locator_resolved_refs"),
            }
        ]
        if coverage.get("locator_status") == "partial"
        or (
            isinstance(entries_status, str)
            and entries_status.startswith("partial")
        )
        else []
    )
    forbidden_section_rows = con.execute(
        """SELECT section_key, section_kind, heading_raw, file_start, file_end
        FROM alphabetical_sections
        WHERE volume_id = ?
          AND section_kind IN ('ordo_rerum', 'editorial_closure')
        ORDER BY section_order, section_key""",
        (volume_id,),
    ).fetchall()
    ordo_rerum_sections = [
        dict(row)
        for row in forbidden_section_rows
        if row["section_kind"] == "ordo_rerum"
    ]
    editorial_closure_sections = [
        dict(row)
        for row in forbidden_section_rows
        if row["section_kind"] == "editorial_closure"
    ]

    ref_rows = con.execute(
        """
        SELECT r.ref_id, r.entry_key, r.ref_order, r.scripture_ref_order,
               r.target_file, r.target_file_probability, r.locator_status,
               r.page_ref_raw, r.range_start_raw, r.range_end_raw,
               r.raw_json AS ref_raw_json,
               e.entry_kind, s.section_key, s.file_start, s.file_end,
               (
                   SELECT COUNT(*)
                   FROM alphabetical_scripture_refs sr
                   WHERE sr.entry_key = r.entry_key
               ) AS scripture_ref_count,
               (
                   SELECT MIN(sr.ref_order)
                   FROM alphabetical_scripture_refs sr
                   WHERE sr.entry_key = r.entry_key
               ) AS scripture_ref_only_order,
               EXISTS (
                   SELECT 1
                   FROM alphabetical_scripture_refs sr
                   WHERE sr.entry_key = r.entry_key
                     AND sr.ref_order = r.scripture_ref_order
               ) AS scripture_parent_exists
        FROM alphabetical_refs r
        JOIN alphabetical_entries e ON e.entry_key = r.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
        ORDER BY r.entry_key, r.ref_order
        """,
        (volume_id,),
    ).fetchall()

    targets_inside_section: list[dict[str, Any]] = []
    material_refs_without_target: list[dict[str, Any]] = []
    unverified_target_evidence: list[dict[str, Any]] = []
    missing_scripture_links: list[dict[str, Any]] = []
    dangling_scripture_links: list[dict[str, Any]] = []
    for row in ref_rows:
        if not isinstance(row["target_file"], str) or not row["target_file"].strip():
            material_refs_without_target.append(
                {
                    "ref_id": int(row["ref_id"]),
                    "entry_key": row["entry_key"],
                    "ref_order": int(row["ref_order"]),
                    "section_key": row["section_key"],
                }
            )
        elif not has_verified_locator_evidence(row["ref_raw_json"]):
            unverified_target_evidence.append(
                {
                    "ref_id": int(row["ref_id"]),
                    "entry_key": row["entry_key"],
                    "ref_order": int(row["ref_order"]),
                    "section_key": row["section_key"],
                    "target_file": row["target_file"],
                }
            )
        if target_is_inside_section(
            row["target_file"],
            row["file_start"],
            row["file_end"],
        ):
            targets_inside_section.append(
                {
                    "ref_id": int(row["ref_id"]),
                    "entry_key": row["entry_key"],
                    "ref_order": int(row["ref_order"]),
                    "target_file": row["target_file"],
                    "can_clear_target": any(
                        isinstance(row[field], str) and row[field].strip()
                        for field in (
                            "page_ref_raw",
                            "range_start_raw",
                            "range_end_raw",
                        )
                    ),
                    "section_key": row["section_key"],
                    "file_start": row["file_start"],
                    "file_end": row["file_end"],
                }
            )

        scripture_ref_count = int(row["scripture_ref_count"] or 0)
        is_biblical = (
            row["entry_kind"] in SCRIPTURE_ENTRY_KINDS
            or scripture_ref_count > 0
        )
        if (
            is_biblical
            and scripture_ref_count > 0
            and row["scripture_ref_order"] is None
        ):
            missing_scripture_links.append(
                {
                    "ref_id": int(row["ref_id"]),
                    "entry_key": row["entry_key"],
                    "ref_order": int(row["ref_order"]),
                    "scripture_ref_count": scripture_ref_count,
                    "scripture_ref_order": row["scripture_ref_only_order"],
                }
            )
        if (
            row["scripture_ref_order"] is not None
            and not bool(row["scripture_parent_exists"])
        ):
            dangling_scripture_links.append(
                {
                    "ref_id": int(row["ref_id"]),
                    "entry_key": row["entry_key"],
                    "ref_order": int(row["ref_order"]),
                    "scripture_ref_order": int(row["scripture_ref_order"]),
                }
            )

    scripture_rows = con.execute(
        """
        SELECT sr.scripture_ref_id, sr.entry_key, sr.ref_order,
               sr.chapter_start, sr.chapter_end, e.entry_kind
        FROM alphabetical_scripture_refs sr
        JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
        ORDER BY sr.entry_key, sr.ref_order
        """,
        (volume_id,),
    ).fetchall()
    unknown_scripture_books = [
        dict(row)
        for row in con.execute(
            """SELECT sr.scripture_ref_id, sr.entry_key, sr.ref_order,
                      sr.book_raw, sr.book_norm, sr.ref_raw
            FROM alphabetical_scripture_refs sr
            JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
            JOIN alphabetical_sections s ON s.section_key = e.section_key
            WHERE s.volume_id = ?
              AND sr.book_key IS NULL
              AND NOT (
                  json_valid(sr.raw_json)
                  AND json_extract(
                      sr.raw_json, '$.canonical_status'
                  ) = 'historical_noncanonical'
              )
            ORDER BY sr.entry_key, sr.ref_order""",
            (volume_id,),
        ).fetchall()
    ]

    counts_by_entry: dict[str, int] = {}
    entry_kinds: dict[str, str] = {}
    implausible_chapters: list[dict[str, Any]] = []
    for row in scripture_rows:
        entry_key = str(row["entry_key"])
        counts_by_entry[entry_key] = counts_by_entry.get(entry_key, 0) + 1
        entry_kinds[entry_key] = str(row["entry_kind"])
        if (
            (row["chapter_start"] is not None and int(row["chapter_start"]) > 150)
            or (row["chapter_end"] is not None and int(row["chapter_end"]) > 150)
        ):
            implausible_chapters.append(
                {
                    "scripture_ref_id": int(row["scripture_ref_id"]),
                    "entry_key": entry_key,
                    "ref_order": int(row["ref_order"]),
                    "chapter_start": row["chapter_start"],
                    "chapter_end": row["chapter_end"],
                }
            )

    multiple_scripture_refs = [
        {
            "entry_key": entry_key,
            "entry_kind": entry_kinds[entry_key],
            "scripture_ref_count": count,
        }
        for entry_key, count in sorted(counts_by_entry.items())
        if entry_kinds[entry_key] in SCRIPTURE_ENTRY_KINDS and count > 1
    ]

    biblical_entries = con.execute(
        """
        SELECT e.entry_key, e.entry_kind, e.raw_json AS entry_raw_json,
               s.section_key, s.raw_json AS section_raw_json,
               (
                   SELECT COUNT(*)
                   FROM alphabetical_refs r
                   WHERE r.entry_key = e.entry_key
               ) AS material_ref_count,
               (
                   SELECT COUNT(*)
                   FROM alphabetical_scripture_refs sr
                   WHERE sr.entry_key = e.entry_key
               ) AS scripture_ref_count
        FROM alphabetical_entries e
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
          AND (
              e.entry_kind IN ('scripture_citation', 'scripture_pericope')
              OR EXISTS (
                  SELECT 1
                  FROM alphabetical_scripture_refs sr
                  WHERE sr.entry_key = e.entry_key
              )
          )
        ORDER BY e.entry_key
        """,
        (volume_id,),
    ).fetchall()
    biblical_entries_without_material_refs: list[dict[str, Any]] = []
    for row in biblical_entries:
        if int(row["material_ref_count"] or 0) != 0:
            continue
        if is_source_only(row["entry_raw_json"], row["section_raw_json"]):
            continue
        biblical_entries_without_material_refs.append(
            {
                "entry_key": row["entry_key"],
                "entry_kind": row["entry_kind"],
                "section_key": row["section_key"],
                "scripture_ref_count": int(row["scripture_ref_count"] or 0),
            }
        )
    scripture_entries_without_scripture_refs = [
        dict(row)
        for row in con.execute(
            """SELECT e.entry_key, e.entry_kind, e.entry_raw, s.section_key
            FROM alphabetical_entries e
            JOIN alphabetical_sections s ON s.section_key = e.section_key
            WHERE s.volume_id = ?
              AND e.entry_kind IN ('scripture_citation', 'scripture_pericope')
              AND NOT EXISTS (
                  SELECT 1
                  FROM alphabetical_scripture_refs sr
                  WHERE sr.entry_key = e.entry_key
              )
            ORDER BY e.entry_key""",
            (volume_id,),
        ).fetchall()
    ]

    issues = {
        "targets_inside_section": targets_inside_section,
        "material_refs_without_target": material_refs_without_target,
        "unverified_target_evidence": unverified_target_evidence,
        "missing_scripture_links": missing_scripture_links,
        "dangling_scripture_links": dangling_scripture_links,
        "multiple_scripture_refs": multiple_scripture_refs,
        "implausible_chapters": implausible_chapters,
        "biblical_entries_without_material_refs": biblical_entries_without_material_refs,
        "scripture_entries_without_scripture_refs": (
            scripture_entries_without_scripture_refs
        ),
        "unverified_empty_extraction": unverified_empty_extraction,
        "unrecoverable_extraction": unrecoverable_extraction,
        "coverage_locator_partial": coverage_locator_partial,
        "ordo_rerum_sections": ordo_rerum_sections,
        "editorial_closure_sections": editorial_closure_sections,
        "unknown_scripture_books": unknown_scripture_books,
    }
    return {
        "issues": issues,
        "issue_counts": {key: len(value) for key, value in issues.items()},
        "issue_samples": {key: _sample(value) for key, value in issues.items()},
    }


def _deterministic_scripture_link_repairs(
    snapshot: dict[str, Any],
) -> list[dict[str, int | str]]:
    return [
        {
            "ref_id": int(item["ref_id"]),
            "entry_key": str(item["entry_key"]),
            "ref_order": int(item["ref_order"]),
            "scripture_ref_order": int(item["scripture_ref_order"]),
        }
        for item in snapshot["issues"]["missing_scripture_links"]
        if int(item["scripture_ref_count"]) == 1
    ]


def _recompute_entry_targets(
    con: sqlite3.Connection,
    entry_keys: Iterable[str],
) -> dict[str, str | None]:
    results: dict[str, str | None] = {}
    for entry_key in sorted(set(entry_keys)):
        rows = con.execute(
            """
            SELECT r.target_file, s.file_start, s.file_end
            FROM alphabetical_refs r
            JOIN alphabetical_entries e ON e.entry_key = r.entry_key
            JOIN alphabetical_sections s ON s.section_key = e.section_key
            WHERE r.entry_key = ?
              AND r.target_file IS NOT NULL
              AND TRIM(r.target_file) != ''
            ORDER BY
                CASE WHEN r.target_file_probability IS NULL THEN 1 ELSE 0 END,
                r.target_file_probability DESC,
                CASE WHEN r.confidence IS NULL THEN 1 ELSE 0 END,
                r.confidence DESC,
                r.ref_order ASC,
                r.ref_id ASC
            """,
            (entry_key,),
        ).fetchall()
        target_file = next(
            (
                str(row["target_file"])
                for row in rows
                if not target_is_inside_section(
                    row["target_file"],
                    row["file_start"],
                    row["file_end"],
                )
            ),
            None,
        )
        con.execute(
            "UPDATE alphabetical_entries SET target_file_best = ? WHERE entry_key = ?",
            (target_file, entry_key),
        )
        results[entry_key] = target_file
    return results


def _blocking_issue_counts(
    snapshot: dict[str, Any],
    *,
    quarantined_target_count: int = 0,
) -> dict[str, int]:
    counts = snapshot["issue_counts"]
    ambiguous_missing_links = sum(
        1
        for item in snapshot["issues"]["missing_scripture_links"]
        if int(item["scripture_ref_count"]) != 1
    )
    return {
        "quarantined_targets_needing_relocation": quarantined_target_count,
        "ambiguous_missing_scripture_links": ambiguous_missing_links,
        "dangling_scripture_links": int(counts["dangling_scripture_links"]),
        "multiple_scripture_refs": int(counts["multiple_scripture_refs"]),
        "implausible_chapters": int(counts["implausible_chapters"]),
        "biblical_entries_without_material_refs": int(
            counts["biblical_entries_without_material_refs"]
        ),
        "scripture_entries_without_scripture_refs": int(
            counts["scripture_entries_without_scripture_refs"]
        ),
        "unverified_empty_extraction": int(
            counts["unverified_empty_extraction"]
        ),
        "unrecoverable_extraction": int(counts["unrecoverable_extraction"]),
        "ordo_rerum_sections": int(counts["ordo_rerum_sections"]),
        "editorial_closure_sections": int(
            counts["editorial_closure_sections"]
        ),
        "unknown_scripture_books": int(counts["unknown_scripture_books"]),
    }


def _status_for(blocking_counts: dict[str, int]) -> str:
    return "needs_reextract" if any(
        int(value) > 0 for value in blocking_counts.values()
    ) else "valid"


def _quality_status(
    blocking_counts: dict[str, int],
    *,
    unresolved_target_count: int,
    coverage_locator_partial_count: int = 0,
) -> str:
    if any(int(value) > 0 for value in blocking_counts.values()):
        return "needs_reextract"
    if unresolved_target_count > 0 or coverage_locator_partial_count > 0:
        return "partial"
    return "valid"


def _store_quality(
    con: sqlite3.Connection,
    volume_id: str,
    *,
    status: str,
    details: dict[str, Any],
) -> None:
    metrics = refresh_volume_quality(con, volume_id)
    computed_at = now_iso()
    con.execute(
        """
        UPDATE alphabetical_volume_quality
        SET db_schema_version = ?,
            status = ?,
            scripture_entry_count = ?,
            scripture_ref_count = ?,
            material_ref_count = ?,
            linked_material_ref_count = ?,
            unlinked_scripture_material_ref_count = ?,
            dangling_scripture_link_count = ?,
            computed_at = ?,
            raw_json = ?
        WHERE volume_id = ?
        """,
        (
            ALPHABETICAL_DB_SCHEMA_VERSION,
            status,
            metrics["scripture_entry_count"],
            metrics["scripture_ref_count"],
            metrics["material_ref_count"],
            metrics["linked_material_ref_count"],
            metrics["unlinked_scripture_material_ref_count"],
            metrics["dangling_scripture_link_count"],
            computed_at,
            json.dumps(details, ensure_ascii=False, sort_keys=True),
            volume_id,
        ),
    )


def audit_volume(
    con: sqlite3.Connection,
    volume_id: str,
    *,
    apply: bool = False,
    schema_migration_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    before = _volume_snapshot(con, volume_id)
    migration_links = schema_migration_links or []
    _restore_preinit_links_in_snapshot(before, migration_links)
    deterministic_links = _deterministic_scripture_link_repairs(before)
    quarantines_by_ref_id = {
        int(item["ref_id"]): {
            **item,
            "quarantine_reason": "target_inside_index_section",
        }
        for item in before["issues"]["targets_inside_section"]
    }
    for item in before["issues"]["unverified_target_evidence"]:
        ref_id = int(item["ref_id"])
        if ref_id in quarantines_by_ref_id:
            continue
        quarantines_by_ref_id[ref_id] = {
            **item,
            "can_clear_target": True,
            "quarantine_reason": "missing_page_specific_locator_evidence",
        }
    planned_quarantines = list(quarantines_by_ref_id.values())

    if not apply:
        blocking = _blocking_issue_counts(
            before,
            quarantined_target_count=len(planned_quarantines),
        )
        return {
            "volume_id": volume_id,
            "mode": "dry-run",
            "issue_counts_before": before["issue_counts"],
            "issue_samples_before": before["issue_samples"],
            "planned_repairs": {
                "scripture_links": len(deterministic_links),
                "targets_to_quarantine": len(planned_quarantines),
                "entries_to_recompute": len(
                    {str(item["entry_key"]) for item in planned_quarantines}
                ),
            },
            "blocking_issue_counts": blocking,
            "status_if_applied": _quality_status(
                blocking,
                unresolved_target_count=int(
                    before["issue_counts"]["material_refs_without_target"]
                )
                + int(before["issue_counts"]["unverified_target_evidence"]),
                coverage_locator_partial_count=int(
                    before["issue_counts"]["coverage_locator_partial"]
                ),
            ),
        }

    for repair in deterministic_links:
        con.execute(
            """
            UPDATE alphabetical_refs
            SET scripture_ref_order = ?
            WHERE ref_id = ?
              AND scripture_ref_order IS NULL
            """,
            (repair["scripture_ref_order"], repair["ref_id"]),
        )

    cleared_ref_ids = [
        int(item["ref_id"])
        for item in planned_quarantines
        if bool(item["can_clear_target"])
    ]
    retained_ref_ids = [
        int(item["ref_id"])
        for item in planned_quarantines
        if not bool(item["can_clear_target"])
    ]
    affected_entry_keys = {
        str(item["entry_key"]) for item in planned_quarantines
    }
    for ref_id in cleared_ref_ids:
        con.execute(
            """
            UPDATE alphabetical_refs
            SET target_file = NULL,
                target_file_probability = NULL,
                locator_status = 'unverified'
            WHERE ref_id = ?
            """,
            (ref_id,),
        )
    recomputed_targets = _recompute_entry_targets(con, affected_entry_keys)

    after = _volume_snapshot(con, volume_id)
    blocking = _blocking_issue_counts(
        after,
        quarantined_target_count=len(planned_quarantines),
    )
    status = _quality_status(
        blocking,
        unresolved_target_count=int(
            after["issue_counts"]["material_refs_without_target"]
        )
        + int(after["issue_counts"]["unverified_target_evidence"]),
        coverage_locator_partial_count=int(
            after["issue_counts"]["coverage_locator_partial"]
        ),
    )
    repairs = {
        "scripture_links_filled": len(deterministic_links),
        "targets_quarantined": len(planned_quarantines),
        "targets_cleared": len(cleared_ref_ids),
        "targets_retained_due_legacy_anchor_constraint": len(retained_ref_ids),
        "entry_targets_recomputed": len(recomputed_targets),
        "entry_target_results": recomputed_targets,
    }
    quality_details = {
        "auditor": "scripts/audit_alphabetical_db_quality.py",
        "auditor_version": AUDITOR_VERSION,
        "audit_applied": True,
        "audited_at": now_iso(),
        "issue_counts_before": before["issue_counts"],
        "issue_counts_after": after["issue_counts"],
        "issue_samples_before": before["issue_samples"],
        "issue_samples_after": after["issue_samples"],
        "repairs": repairs,
        "blocking_issue_counts": blocking,
        "status_reason": (
            "At least one semantic or locator defect requires re-extraction."
            if status == "needs_reextract"
            else "Some material occurrences remain unresolved."
            if status == "partial"
            else "All detected defects had a deterministic schema-v5 repair."
        ),
    }
    _store_quality(
        con,
        volume_id,
        status=status,
        details=quality_details,
    )
    return {
        "volume_id": volume_id,
        "mode": "apply",
        "issue_counts_before": before["issue_counts"],
        "issue_counts_after": after["issue_counts"],
        "issue_samples_before": before["issue_samples"],
        "repairs": repairs,
        "blocking_issue_counts": blocking,
        "status": status,
    }


def _selected_volume_ids(
    con: sqlite3.Connection,
    requested: list[str] | None,
) -> tuple[list[str], list[str]]:
    available = {
        str(row["volume_id"])
        for row in con.execute(
            "SELECT volume_id FROM alphabetical_volumes ORDER BY volume_id"
        )
    }
    if requested:
        selected = list(dict.fromkeys(requested))
        missing = [volume_id for volume_id in selected if volume_id not in available]
        return [volume_id for volume_id in selected if volume_id in available], missing
    return sorted(available), []


def audit_database(
    db_path: Path | str = DEFAULT_DB,
    *,
    volume_ids: list[str] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    path = Path(db_path)
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if apply:
        con = connect_db(path)
        source_con = None
    else:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix=".alphabetical-audit-",
            dir=path.parent,
        )
        temporary_path = Path(temporary_directory.name) / "audit.db"
        con = connect_db(temporary_path)
        source_con = None
        if path.exists():
            source_con = sqlite3.connect(
                f"file:{path.resolve()}?mode=ro",
                uri=True,
            )
            source_con.backup(con)
            source_con.close()
            source_con = None
    try:
        migration_links_by_volume = _capture_preinit_deterministic_links(con)
        init_schema(con)
        selected, missing = _selected_volume_ids(con, volume_ids)
        reports: list[dict[str, Any]] = []
        errors = [
            {"volume_id": volume_id, "error": "volume_not_found"}
            for volume_id in missing
        ]
        for volume_id in selected:
            if not apply:
                reports.append(
                    audit_volume(
                        con,
                        volume_id,
                        apply=False,
                        schema_migration_links=migration_links_by_volume.get(
                            volume_id,
                            [],
                        ),
                    )
                )
                continue
            try:
                con.execute("BEGIN IMMEDIATE")
                report = audit_volume(
                    con,
                    volume_id,
                    apply=True,
                    schema_migration_links=migration_links_by_volume.get(
                        volume_id,
                        [],
                    ),
                )
                con.commit()
                reports.append(report)
            except Exception as exc:
                con.rollback()
                errors.append(
                    {
                        "volume_id": volume_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        if source_con is not None:
            source_con.close()
        con.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()

    status_counts: dict[str, int] = {}
    status_field = "status" if apply else "status_if_applied"
    for report in reports:
        status = str(report[status_field])
        status_counts[status] = status_counts.get(status, 0) + 1
    issue_totals_before: dict[str, int] = {}
    blocking_issue_totals: dict[str, int] = {}
    repair_totals: dict[str, int] = {}
    repair_field = "repairs" if apply else "planned_repairs"
    for report in reports:
        for key, value in report["issue_counts_before"].items():
            issue_totals_before[key] = issue_totals_before.get(key, 0) + int(value)
        for key, value in report["blocking_issue_counts"].items():
            blocking_issue_totals[key] = blocking_issue_totals.get(key, 0) + int(value)
        for key, value in report[repair_field].items():
            if isinstance(value, int):
                repair_totals[key] = repair_totals.get(key, 0) + value
    return {
        "db": str(path),
        "mode": "apply" if apply else "dry-run",
        "schema_version": ALPHABETICAL_DB_SCHEMA_VERSION,
        "volume_count": len(reports),
        "status_counts": status_counts,
        "issue_totals_before": issue_totals_before,
        "blocking_issue_totals": blocking_issue_totals,
        "repair_totals": repair_totals,
        "volumes": reports,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit alphabetical material/scripture integrity and quarantine index-page "
            "targets without inventing replacement OCR files."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--volume-id",
        action="append",
        dest="volume_ids",
        help="Audit only this volume. Repeat for more than one; default is all volumes.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deterministic repairs and record per-volume quality.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Omit per-volume details from stdout and print aggregate counts only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = audit_database(
        args.db,
        volume_ids=args.volume_ids,
        apply=args.apply,
    )
    printable = (
        {key: value for key, value in summary.items() if key != "volumes"}
        if args.summary_only
        else summary
    )
    print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
