#!/usr/bin/env python3
"""Audit and selectively purge wrong-source volume rows from active databases.

Dry-run is the default. Applying changes requires both ``--apply`` and the exact
volume confirmation string printed by ``--help``. Each database is changed in
its own ``BEGIN IMMEDIATE`` transaction and verified before commit.

This script intentionally never deletes a database file, runs VACUUM, or edits
SQLite ``-wal``/``-shm`` files. The compact summary scripture database is
reported as rebuild-only because its builder creates a complete new database.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "data"
    / "volume_similarity_audit"
    / "defective_volume_cleanup_manifest.json"
)
AUTHORIZED_VOLUME_IDS = ("PG024", "PG031", "PG084", "PG116", "PL124")
CONFIRMATION = ",".join(AUTHORIZED_VOLUME_IDS)
DATABASE_ORDER = (
    "ocr_eval.db",
    "ocr_versions.db",
    "tesseract.db",
    "patristica_resumos.db",
    "patristica_keywords.db",
    "editorial_page_estimator.db",
    "patristic_indices.db",
    "alphabetical_indices.db",
    "alphabetical_analysis.db",
    "scripture_citations.db",
    "summary_scripture_citations_v3.db",
)
REBUILD_ONLY_DATABASES = frozenset({"summary_scripture_citations_v3.db"})


@dataclass(frozen=True)
class Query:
    label: str
    sql: str
    params: tuple[Any, ...]


@dataclass(frozen=True)
class Statement:
    label: str
    sql: str
    params: tuple[Any, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema_version") != 1:
        raise SystemExit(f"Unsupported cleanup manifest schema: {path}")
    volume_ids = tuple(manifest.get("volume_ids") or ())
    if volume_ids != AUTHORIZED_VOLUME_IDS:
        raise SystemExit(
            "Refusing a changed volume set. Expected exactly "
            f"{CONFIRMATION}; found {','.join(volume_ids)}"
        )
    if len(volume_ids) != len(set(volume_ids)):
        raise SystemExit("Cleanup manifest contains duplicate volume IDs")
    if any(not re.fullmatch(r"(?:PG|PL|PO)\d{3}", item) for item in volume_ids):
        raise SystemExit("Cleanup manifest contains an invalid volume ID")
    excluded = set(manifest.get("excluded_volume_ids") or ())
    if excluded.intersection(volume_ids):
        raise SystemExit("A volume is both selected and excluded")
    return manifest, hashlib.sha256(raw).hexdigest()


def _placeholders(values: Sequence[object]) -> str:
    if not values:
        raise ValueError("Cannot build an empty SQL placeholder list")
    return ",".join("?" for _ in values)


def _simple_query(
    label: str, table: str, column: str, volume_ids: Sequence[str]
) -> Query:
    ph = _placeholders(volume_ids)
    return Query(
        label,
        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({ph})',
        tuple(volume_ids),
    )


def _tesseract_predicate(volume_ids: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for volume_id in volume_ids:
        clauses.append("(imgpath LIKE ? OR imgpath LIKE ?)")
        params.extend(
            (
                f"%/teste/{volume_id}/images/%",
                f"teste/{volume_id}/images/%",
            )
        )
    return " OR ".join(clauses), tuple(params)


def _database_queries(name: str, volume_ids: Sequence[str]) -> list[Query]:
    ph = _placeholders(volume_ids)
    params = tuple(volume_ids)
    simple = lambda label, table, column="volume_id": _simple_query(
        label, table, column, volume_ids
    )

    if name == "ocr_eval.db":
        return [simple("evaluations", "evaluations")]
    if name == "ocr_versions.db":
        return [simple("ocr_results", "ocr_results")]
    if name == "tesseract.db":
        predicate, path_params = _tesseract_predicate(volume_ids)
        return [
            Query(
                "tesseract_cache",
                f"SELECT COUNT(*) FROM tesseract_cache WHERE {predicate}",
                path_params,
            )
        ]
    if name == "patristica_resumos.db":
        direct = [
            "resumos",
            "resumo_runs",
            "resumo_generations",
            "resumo_context_anchors",
            "resumo_pagina_embedding",
            "resumo_pagina_embedding_reduced",
            "resumo_pagina_clusters",
            "resumo_global_embedding",
            "resumo_global_embedding_reduced",
            "resumo_global_clusters",
        ]
        queries = [simple(table, table, "documento") for table in direct]
        for label, table in (
            ("resumo_generation_clusters", "resumo_generation_clusters"),
            ("resumo_review_queue", "resumo_review_queue"),
        ):
            queries.append(
                Query(
                    label,
                    f"SELECT COUNT(*) FROM {table} WHERE documento IN ({ph}) "
                    "OR generation_id IN (SELECT id FROM resumo_generations "
                    f"WHERE documento IN ({ph}))",
                    params + params,
                )
            )
        queries.append(
            Query(
                "resumo_translations",
                "SELECT COUNT(*) FROM resumo_translations WHERE generation_id IN "
                "(SELECT id FROM resumo_generations "
                f"WHERE documento IN ({ph}))",
                params,
            )
        )
        return queries
    if name == "patristica_keywords.db":
        return [simple("keyword_occurrence", "keyword_occurrence", "documento")]
    if name == "editorial_page_estimator.db":
        return [
            simple("file_observation_cache", "file_observation_cache"),
            simple("page_overrides", "page_overrides"),
        ]
    if name == "patristic_indices.db":
        queries = [simple(table, table) for table in ("volumes", "runs", "works", "index_sections")]
        queries.append(
            Query(
                "index_entries",
                "SELECT COUNT(*) FROM index_entries e "
                "JOIN index_sections s ON s.section_key=e.section_key "
                f"WHERE s.volume_id IN ({ph})",
                params,
            )
        )
        return queries
    if name == "alphabetical_indices.db":
        queries = [
            simple(table, table)
            for table in (
                "alphabetical_volumes",
                "alphabetical_runs",
                "alphabetical_sections",
                "alphabetical_source_spans",
                "alphabetical_boundaries",
                "alphabetical_volume_quality",
            )
        ]
        for label, table, alias in (
            ("alphabetical_nodes", "alphabetical_nodes", "n"),
            ("alphabetical_entries", "alphabetical_entries", "e"),
        ):
            queries.append(
                Query(
                    label,
                    f"SELECT COUNT(*) FROM {table} {alias} "
                    "JOIN alphabetical_sections s ON s.section_key="
                    f"{alias}.section_key WHERE s.volume_id IN ({ph})",
                    params,
                )
            )
        for label, table, alias in (
            ("alphabetical_refs", "alphabetical_refs", "r"),
            ("alphabetical_scripture_refs", "alphabetical_scripture_refs", "r"),
        ):
            queries.append(
                Query(
                    label,
                    f"SELECT COUNT(*) FROM {table} {alias} "
                    f"JOIN alphabetical_entries e ON e.entry_key={alias}.entry_key "
                    "JOIN alphabetical_sections s ON s.section_key=e.section_key "
                    f"WHERE s.volume_id IN ({ph})",
                    params,
                )
            )
        return queries
    if name == "alphabetical_analysis.db":
        return [
            simple(table, table)
            for table in (
                "analysis_volumes",
                "analysis_volume_stages",
                "analysis_stage_runs",
                "analysis_stage_artifacts",
                "analysis_discovered_pages",
                "analysis_discovered_segments",
                "analysis_sections",
                "analysis_entries",
                "analysis_occurrences",
                "analysis_scripture_refs",
            )
        ]
    if name == "scripture_citations.db":
        queries = [
            simple(table, table)
            for table in (
                "citation_volumes",
                "citation_files",
                "citation_book_aliases",
                "citation_format_profiles",
                "citation_index_seeds",
            )
        ]
        queries.extend(
            (
                Query(
                    "citation_file_pages",
                    "SELECT COUNT(*) FROM citation_file_pages p "
                    "JOIN citation_files f ON f.file_id=p.file_id "
                    f"WHERE f.volume_id IN ({ph})",
                    params,
                ),
                Query(
                    "citation_groups",
                    "SELECT COUNT(*) FROM citation_groups g "
                    "JOIN citation_files f ON f.file_id=g.file_id "
                    f"WHERE f.volume_id IN ({ph})",
                    params,
                ),
                Query(
                    "citation_occurrences",
                    "SELECT COUNT(*) FROM citation_occurrences o "
                    "JOIN citation_groups g ON g.group_id=o.group_id "
                    "JOIN citation_files f ON f.file_id=g.file_id "
                    f"WHERE f.volume_id IN ({ph})",
                    params,
                ),
                Query(
                    "citation_seed_links",
                    "SELECT COUNT(*) FROM citation_seed_links l WHERE "
                    "l.seed_key IN (SELECT seed_key FROM citation_index_seeds "
                    f"WHERE volume_id IN ({ph})) OR l.occurrence_key IN ("
                    "SELECT o.occurrence_key FROM citation_occurrences o "
                    "JOIN citation_groups g ON g.group_id=o.group_id "
                    "JOIN citation_files f ON f.file_id=g.file_id "
                    f"WHERE f.volume_id IN ({ph}))",
                    params + params,
                ),
            )
        )
        return queries
    if name == "summary_scripture_citations_v3.db":
        return [
            simple("pages", "pages"),
            Query(
                "scripture_mentions",
                "SELECT COUNT(*) FROM scripture_mentions m "
                "JOIN pages p ON p.id=m.page_id "
                f"WHERE p.volume_id IN ({ph})",
                params,
            ),
            Query(
                "rejected_mentions",
                "SELECT COUNT(*) FROM rejected_mentions r "
                "JOIN pages p ON p.id=r.page_id "
                f"WHERE p.volume_id IN ({ph})",
                params,
            ),
        ]
    raise ValueError(f"Unsupported database: {name}")


def _simple_delete(
    label: str, table: str, column: str, volume_ids: Sequence[str]
) -> Statement:
    ph = _placeholders(volume_ids)
    return Statement(
        label,
        f'DELETE FROM "{table}" WHERE "{column}" IN ({ph})',
        tuple(volume_ids),
    )


def _database_statements(name: str, volume_ids: Sequence[str]) -> list[Statement]:
    ph = _placeholders(volume_ids)
    params = tuple(volume_ids)
    delete = lambda label, table, column="volume_id": _simple_delete(
        label, table, column, volume_ids
    )

    if name == "ocr_eval.db":
        return [delete("evaluations", "evaluations")]
    if name == "ocr_versions.db":
        return [delete("ocr_results", "ocr_results")]
    if name == "tesseract.db":
        predicate, path_params = _tesseract_predicate(volume_ids)
        return [
            Statement(
                "tesseract_cache",
                f"DELETE FROM tesseract_cache WHERE {predicate}",
                path_params,
            )
        ]
    if name == "patristica_resumos.db":
        statements = [
            Statement(
                "resumo_translations",
                "DELETE FROM resumo_translations WHERE generation_id IN "
                "(SELECT id FROM resumo_generations "
                f"WHERE documento IN ({ph}))",
                params,
            ),
            Statement(
                "resumo_generation_clusters",
                "DELETE FROM resumo_generation_clusters "
                f"WHERE documento IN ({ph}) OR generation_id IN ("
                "SELECT id FROM resumo_generations "
                f"WHERE documento IN ({ph}))",
                params + params,
            ),
            Statement(
                "resumo_review_queue",
                "DELETE FROM resumo_review_queue "
                f"WHERE documento IN ({ph}) OR generation_id IN ("
                "SELECT id FROM resumo_generations "
                f"WHERE documento IN ({ph}))",
                params + params,
            ),
            Statement(
                "resumo_generations_previous_generation_id",
                "UPDATE resumo_generations SET previous_generation_id=NULL "
                f"WHERE documento IN ({ph})",
                params,
            ),
            delete("resumo_generations", "resumo_generations", "documento"),
            delete("resumo_runs", "resumo_runs", "documento"),
        ]
        statements.extend(
            delete(table, table, "documento")
            for table in (
                "resumo_context_anchors",
                "resumo_pagina_embedding_reduced",
                "resumo_pagina_embedding",
                "resumo_pagina_clusters",
                "resumo_global_embedding_reduced",
                "resumo_global_embedding",
                "resumo_global_clusters",
                "resumos",
            )
        )
        return statements
    if name == "patristica_keywords.db":
        return [delete("keyword_occurrence", "keyword_occurrence", "documento")]
    if name == "editorial_page_estimator.db":
        return [delete("file_observation_cache", "file_observation_cache")]
    if name == "patristic_indices.db":
        return [delete("runs", "runs"), delete("volumes", "volumes")]
    if name == "alphabetical_indices.db":
        return [delete("alphabetical_volumes", "alphabetical_volumes")]
    if name == "alphabetical_analysis.db":
        return [
            delete("analysis_volume_stages", "analysis_volume_stages"),
            delete("analysis_stage_runs", "analysis_stage_runs"),
            delete("analysis_volumes", "analysis_volumes"),
        ]
    if name == "scripture_citations.db":
        return [
            Statement(
                "citation_seed_links",
                "DELETE FROM citation_seed_links WHERE seed_key IN ("
                "SELECT seed_key FROM citation_index_seeds "
                f"WHERE volume_id IN ({ph})) OR occurrence_key IN ("
                "SELECT o.occurrence_key FROM citation_occurrences o "
                "JOIN citation_groups g ON g.group_id=o.group_id "
                "JOIN citation_files f ON f.file_id=g.file_id "
                f"WHERE f.volume_id IN ({ph}))",
                params + params,
            ),
            delete("citation_index_seeds", "citation_index_seeds"),
            delete("citation_book_aliases", "citation_book_aliases"),
            delete("citation_format_profiles", "citation_format_profiles"),
            delete("citation_volumes", "citation_volumes"),
        ]
    if name in REBUILD_ONLY_DATABASES:
        return []
    raise ValueError(f"Unsupported database: {name}")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _connect_writable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _count_queries(
    connection: sqlite3.Connection, queries: Iterable[Query]
) -> dict[str, int]:
    return {
        query.label: int(connection.execute(query.sql, query.params).fetchone()[0])
        for query in queries
    }


def _validate_expected_counts(
    database_name: str,
    actual: dict[str, int],
    manifest: dict[str, Any],
    *,
    allow_count_drift: bool,
) -> list[str]:
    expected_all = manifest.get("expected_database_rows") or {}
    expected = expected_all.get(database_name) or {}
    drift: list[str] = []
    for label, expected_count in expected.items():
        actual_count = actual.get(label)
        if actual_count not in (0, int(expected_count)):
            drift.append(
                f"{database_name}:{label}: expected old={expected_count} or clean=0, "
                f"found {actual_count}"
            )
    if drift and not allow_count_drift:
        raise RuntimeError("Count guard rejected apply:\n- " + "\n- ".join(drift))
    return drift


def _selected_databases(values: list[str] | None) -> tuple[str, ...]:
    if not values or values == ["all"]:
        return DATABASE_ORDER
    selected: list[str] = []
    for value in values:
        if value == "all":
            raise SystemExit("Use --database all alone")
        if value not in DATABASE_ORDER:
            raise SystemExit(f"Unknown database: {value}")
        if value not in selected:
            selected.append(value)
    return tuple(selected)


def _audit_one(
    path: Path,
    volume_ids: Sequence[str],
) -> tuple[dict[str, int], list[Query]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    queries = _database_queries(path.name, volume_ids)
    with _connect_readonly(path) as connection:
        counts = _count_queries(connection, queries)
    return counts, queries


def _apply_one(
    path: Path,
    queries: list[Query],
    volume_ids: Sequence[str],
) -> tuple[dict[str, int], dict[str, int], list[list[Any]]]:
    statements = _database_statements(path.name, volume_ids)
    connection = _connect_writable(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        changed: dict[str, int] = {}
        for statement in statements:
            cursor = connection.execute(statement.sql, statement.params)
            changed[statement.label] = max(0, int(cursor.rowcount))
        remaining = _count_queries(connection, queries)
        nonzero = {label: count for label, count in remaining.items() if count}
        if nonzero:
            raise RuntimeError(f"Selected rows remain in {path.name}: {nonzero}")
        violations = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if violations:
            raise RuntimeError(
                f"Foreign-key violations after cleaning {path.name}: "
                f"{violations[:20]}"
            )
        connection.commit()
        return changed, remaining, violations
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit or selectively purge database rows for the five confirmed "
            "wrong-source volumes. Dry-run is the default."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--data-dir", type=Path, default=PROJECT_ROOT / "data", help="Database directory"
    )
    parser.add_argument(
        "--database",
        action="append",
        help="Database filename to inspect; repeat as needed, or use 'all'",
    )
    parser.add_argument("--report", type=Path, help="Write an atomic JSON audit report")
    parser.add_argument("--apply", action="store_true", help="Commit selective deletes")
    parser.add_argument(
        "--confirm",
        help=f"Required with --apply; exact value: {CONFIRMATION}",
    )
    parser.add_argument(
        "--allow-count-drift",
        action="store_true",
        help="Apply despite manifest count drift; requires the normal confirmation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest, manifest_sha256 = _load_manifest(manifest_path)
    volume_ids = tuple(manifest["volume_ids"])
    database_names = _selected_databases(args.database)

    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(
            "Refusing apply without exact --confirm "
            f"{CONFIRMATION}"
        )
    if args.apply and args.report is None:
        raise SystemExit("--report is required with --apply")

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running" if args.apply else "dry_run",
        "started_at": _utc_now(),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "volume_ids": list(volume_ids),
        "apply": bool(args.apply),
        "allow_count_drift": bool(args.allow_count_drift),
        "databases": [],
    }
    report_path = args.report.resolve() if args.report else None
    if report_path:
        _write_json_atomic(report_path, report)

    try:
        for database_name in database_names:
            database_path = (args.data_dir / database_name).resolve()
            before, queries = _audit_one(database_path, volume_ids)
            drift = _validate_expected_counts(
                database_name,
                before,
                manifest,
                allow_count_drift=(not args.apply or bool(args.allow_count_drift)),
            )
            item: dict[str, Any] = {
                "database": database_name,
                "path": str(database_path),
                "mode": (
                    "rebuild_whole_database"
                    if database_name in REBUILD_ONLY_DATABASES
                    else "selective_delete"
                ),
                "selected_rows_before": before,
                "count_drift": drift,
                "status": "audited",
            }
            if args.apply and database_name not in REBUILD_ONLY_DATABASES:
                changed, after, violations = _apply_one(
                    database_path, queries, volume_ids
                )
                item.update(
                    {
                        "status": "applied",
                        "statement_rowcounts": changed,
                        "selected_rows_after": after,
                        "foreign_key_violations": violations,
                    }
                )
            elif database_name in REBUILD_ONLY_DATABASES:
                item["status"] = "rebuild_required"
            report["databases"].append(item)
            if report_path:
                _write_json_atomic(report_path, report)
    except BaseException as exc:
        report["status"] = "failed"
        report["failed_at"] = _utc_now()
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report_path:
            _write_json_atomic(report_path, report)
        raise

    report["status"] = "applied" if args.apply else "dry_run"
    report["finished_at"] = _utc_now()
    if report_path:
        _write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
