#!/usr/bin/env python3
"""Remove derivados de PL020:612--1223, preservando os bancos de OCR.

O modo padrao e somente leitura. ``--apply`` exige confirmacao literal e um
relatorio. Cada banco e alterado em sua propria transacao e verificado antes do
commit. Bancos historicos/de OCR nao fazem parte da lista desta ferramenta.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable, Iterator, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SOURCE = PROJECT_ROOT / "teste" / "PL020"
VOLUME_ID = "PL020"
FIRST_PAGE = 612
LAST_PAGE = 1223
CONFIRMATION = "PL020:612-1223"
DATABASE_ORDER = (
    "patristica_resumos.db",
    "patristica_keywords.db",
    "editorial_page_estimator.db",
    "patristic_indices.db",
    "alphabetical_indices.db",
    "alphabetical_analysis.db",
    "scripture_citations.db",
    "summary_scripture_citations_v3.db",
)
PRESERVED_DATABASES = (
    "ocr_versions.db",
    "ocr_eval.db",
    "tesseract.db",
    "ocr.db",
    "ocr (copiar 1).db",
)
PAGE_SUMMARY_TABLES = (
    "resumos",
    "resumo_pagina_embedding",
    "resumo_global_embedding",
    "resumo_pagina_embedding_reduced",
    "resumo_pagina_clusters",
    "resumo_global_embedding_reduced",
    "resumo_global_clusters",
    "resumo_review_queue",
    "resumo_context_anchors",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
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


def trailing_page_number(path: Path) -> int | None:
    tail = path.stem.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def is_removed_page(page_num: object) -> bool:
    try:
        value = int(page_num)
    except (TypeError, ValueError):
        return False
    return FIRST_PAGE <= value <= LAST_PAGE


def connect_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def connect_writable(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def scalar(con: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> int:
    return int(con.execute(sql, tuple(params)).fetchone()[0])


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


def require_tables(con: sqlite3.Connection, required: Iterable[str]) -> None:
    missing = sorted(set(required) - table_names(con))
    if missing:
        raise RuntimeError("Tabelas ausentes: " + ", ".join(missing))


def chunks(values: Sequence[Any], size: int = 400) -> Iterator[Sequence[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def count_for_ids(
    con: sqlite3.Connection,
    table: str,
    column: str,
    ids: Sequence[Any],
) -> int:
    total = 0
    for batch in chunks(ids):
        placeholders = ",".join("?" for _ in batch)
        total += scalar(
            con,
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({placeholders})',
            batch,
        )
    return total


def delete_for_ids(
    con: sqlite3.Connection,
    table: str,
    column: str,
    ids: Sequence[Any],
) -> int:
    changed = 0
    for batch in chunks(ids):
        placeholders = ",".join("?" for _ in batch)
        cursor = con.execute(
            f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})',
            tuple(batch),
        )
        changed += max(0, int(cursor.rowcount))
    return changed


def ids_from_moved_paths(
    con: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    path_column: str,
    volume_column: str | None,
) -> list[Any]:
    sql = f'SELECT "{id_column}", "{path_column}" FROM "{table}"'
    params: tuple[Any, ...] = ()
    if volume_column:
        sql += f' WHERE "{volume_column}"=?'
        params = (VOLUME_ID,)
    else:
        sql += f' WHERE "{path_column}" LIKE ?'
        params = (f"%/teste/{VOLUME_ID}/%",)
    selected: list[Any] = []
    for row in con.execute(sql, params):
        page_num = trailing_page_number(Path(str(row[path_column])))
        if page_num is not None and is_removed_page(page_num):
            selected.append(row[id_column])
    return selected


def page_count(con: sqlite3.Connection, table: str, page_column: str) -> int:
    return scalar(
        con,
        f'SELECT COUNT(*) FROM "{table}" '
        f'WHERE documento=? AND "{page_column}" BETWEEN ? AND ?',
        (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
    )


def audit_resumos(con: sqlite3.Connection) -> dict[str, int]:
    required = set(PAGE_SUMMARY_TABLES) | {
        "resumo_runs",
        "resumo_generations",
        "resumo_translations",
        "resumo_generation_clusters",
    }
    require_tables(con, required)
    counts = {table: page_count(con, table, "pagina_num") for table in PAGE_SUMMARY_TABLES}
    generation_ids = [
        int(row[0])
        for row in con.execute(
            "SELECT id FROM resumo_generations "
            "WHERE documento=? AND pagina_num BETWEEN ? AND ?",
            (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
        )
    ]
    counts["resumo_generations"] = len(generation_ids)
    counts["resumo_translations"] = count_for_ids(
        con, "resumo_translations", "generation_id", generation_ids
    )
    counts["resumo_generation_clusters"] = scalar(
        con,
        "SELECT COUNT(*) FROM resumo_generation_clusters WHERE "
        "(documento=? AND pagina_num BETWEEN ? AND ?) OR generation_id IN ("
        "SELECT id FROM resumo_generations WHERE documento=? "
        "AND pagina_num BETWEEN ? AND ?)",
        (
            VOLUME_ID,
            FIRST_PAGE,
            LAST_PAGE,
            VOLUME_ID,
            FIRST_PAGE,
            LAST_PAGE,
        ),
    )
    counts["previous_generation_links"] = count_for_ids(
        con, "resumo_generations", "previous_generation_id", generation_ids
    )
    counts["resumo_runs_preserved"] = scalar(
        con, "SELECT COUNT(*) FROM resumo_runs WHERE documento=?", (VOLUME_ID,)
    )
    return counts


def apply_resumos(con: sqlite3.Connection) -> dict[str, int]:
    generation_ids = [
        int(row[0])
        for row in con.execute(
            "SELECT id FROM resumo_generations "
            "WHERE documento=? AND pagina_num BETWEEN ? AND ?",
            (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
        )
    ]
    changed: dict[str, int] = {}
    changed["previous_generation_links_cleared"] = 0
    for batch in chunks(generation_ids):
        placeholders = ",".join("?" for _ in batch)
        cursor = con.execute(
            "UPDATE resumo_generations SET previous_generation_id=NULL "
            f"WHERE previous_generation_id IN ({placeholders})",
            tuple(batch),
        )
        changed["previous_generation_links_cleared"] += max(0, int(cursor.rowcount))

    changed["resumo_translations"] = delete_for_ids(
        con, "resumo_translations", "generation_id", generation_ids
    )
    cursor = con.execute(
        "DELETE FROM resumo_generation_clusters WHERE "
        "(documento=? AND pagina_num BETWEEN ? AND ?) OR generation_id IN ("
        "SELECT id FROM resumo_generations WHERE documento=? "
        "AND pagina_num BETWEEN ? AND ?)",
        (
            VOLUME_ID,
            FIRST_PAGE,
            LAST_PAGE,
            VOLUME_ID,
            FIRST_PAGE,
            LAST_PAGE,
        ),
    )
    changed["resumo_generation_clusters"] = max(0, int(cursor.rowcount))
    changed["resumo_generations"] = delete_for_ids(
        con, "resumo_generations", "id", generation_ids
    )
    for table in PAGE_SUMMARY_TABLES:
        cursor = con.execute(
            f'DELETE FROM "{table}" '
            "WHERE documento=? AND pagina_num BETWEEN ? AND ?",
            (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
        )
        changed[table] = max(0, int(cursor.rowcount))
    return changed


def audit_keywords(con: sqlite3.Connection) -> dict[str, int]:
    require_tables(
        con,
        {
            "keywords",
            "keyword_occurrence",
            "keyword_alias",
            "keyword_category",
            "keyword_embedding",
            "keyword_clusters",
            "keyword_cluster_meta",
            "cluster_canonical_names",
        },
    )
    target = (
        "documento=? AND pagina_num BETWEEN ? AND ?"
    )
    params = (VOLUME_ID, FIRST_PAGE, LAST_PAGE)
    return {
        "keyword_occurrence": scalar(
            con, f"SELECT COUNT(*) FROM keyword_occurrence WHERE {target}", params
        ),
        "distinct_keyword_ids": scalar(
            con,
            f"SELECT COUNT(DISTINCT keyword_id) FROM keyword_occurrence WHERE {target}",
            params,
        ),
        "keywords_becoming_orphan": scalar(
            con,
            "WITH target AS (SELECT DISTINCT keyword_id FROM keyword_occurrence "
            f"WHERE {target}) SELECT COUNT(*) FROM target t WHERE NOT EXISTS ("
            "SELECT 1 FROM keyword_occurrence o WHERE o.keyword_id=t.keyword_id "
            f"AND NOT (o.documento=? AND o.pagina_num BETWEEN ? AND ?))",
            params + params,
        ),
    }


def apply_keywords(con: sqlite3.Connection) -> dict[str, int]:
    con.execute("CREATE TEMP TABLE cut_keyword_ids(id INTEGER PRIMARY KEY)")
    con.execute(
        "INSERT INTO cut_keyword_ids "
        "SELECT DISTINCT keyword_id FROM keyword_occurrence "
        "WHERE documento=? AND pagina_num BETWEEN ? AND ?",
        (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
    )
    con.execute("CREATE TEMP TABLE cut_hdbscan_groups(id INTEGER PRIMARY KEY)")
    con.execute(
        "INSERT OR IGNORE INTO cut_hdbscan_groups "
        "SELECT hdbscan_group_id FROM keywords WHERE id IN (SELECT id FROM cut_keyword_ids) "
        "AND hdbscan_group_id IS NOT NULL"
    )
    con.execute("CREATE TEMP TABLE cut_cluster_ids(id INTEGER PRIMARY KEY)")
    con.execute(
        "INSERT OR IGNORE INTO cut_cluster_ids "
        "SELECT cluster_id FROM keyword_clusters WHERE keyword_id IN "
        "(SELECT id FROM cut_keyword_ids) AND cluster_id IS NOT NULL"
    )
    changed: dict[str, int] = {}
    cursor = con.execute(
        "DELETE FROM keyword_occurrence "
        "WHERE documento=? AND pagina_num BETWEEN ? AND ?",
        (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
    )
    changed["keyword_occurrence"] = max(0, int(cursor.rowcount))
    con.execute("CREATE TEMP TABLE orphan_keyword_ids(id INTEGER PRIMARY KEY)")
    con.execute(
        "INSERT INTO orphan_keyword_ids SELECT id FROM cut_keyword_ids c "
        "WHERE NOT EXISTS (SELECT 1 FROM keyword_occurrence o WHERE o.keyword_id=c.id)"
    )
    for table in ("keyword_alias", "keyword_category", "keyword_embedding"):
        cursor = con.execute(
            f"DELETE FROM {table} WHERE keyword_id IN (SELECT id FROM orphan_keyword_ids)"
        )
        changed[table] = max(0, int(cursor.rowcount))
    cursor = con.execute(
        "DELETE FROM keyword_clusters WHERE keyword_id IN (SELECT id FROM orphan_keyword_ids)"
    )
    changed["keyword_clusters"] = max(0, int(cursor.rowcount))
    cursor = con.execute("DELETE FROM keywords WHERE id IN (SELECT id FROM orphan_keyword_ids)")
    changed["keywords_orphaned"] = max(0, int(cursor.rowcount))
    cursor = con.execute(
        "DELETE FROM cluster_canonical_names WHERE group_id IN "
        "(SELECT id FROM cut_hdbscan_groups) AND NOT EXISTS ("
        "SELECT 1 FROM keywords k WHERE k.hdbscan_group_id=cluster_canonical_names.group_id)"
    )
    changed["cluster_canonical_names_orphaned"] = max(0, int(cursor.rowcount))
    cursor = con.execute(
        "DELETE FROM keyword_cluster_meta WHERE cluster_id IN "
        "(SELECT id FROM cut_cluster_ids) AND NOT EXISTS ("
        "SELECT 1 FROM keyword_clusters kc "
        "WHERE kc.cluster_id=keyword_cluster_meta.cluster_id)"
    )
    changed["keyword_cluster_meta_orphaned"] = max(0, int(cursor.rowcount))
    return changed


def audit_editorial_cache(con: sqlite3.Connection) -> dict[str, int]:
    require_tables(con, {"file_observation_cache", "page_overrides"})
    ids = ids_from_moved_paths(
        con,
        table="file_observation_cache",
        id_column="file_path",
        path_column="file_path",
        volume_column="volume_id",
    )
    return {
        "file_observation_cache": len(ids),
        "page_overrides_preserved": scalar(
            con, "SELECT COUNT(*) FROM page_overrides WHERE volume_id=?", (VOLUME_ID,)
        ),
    }


def apply_editorial_cache(con: sqlite3.Connection) -> dict[str, int]:
    paths = ids_from_moved_paths(
        con,
        table="file_observation_cache",
        id_column="file_path",
        path_column="file_path",
        volume_column="volume_id",
    )
    return {
        "file_observation_cache": delete_for_ids(
            con, "file_observation_cache", "file_path", paths
        )
    }


def audit_patristic_indices(con: sqlite3.Connection) -> dict[str, int]:
    require_tables(con, {"volumes", "runs", "works", "index_sections", "index_entries"})
    return {
        "volumes": scalar(con, "SELECT COUNT(*) FROM volumes WHERE volume_id=?", (VOLUME_ID,)),
        "runs": scalar(con, "SELECT COUNT(*) FROM runs WHERE volume_id=?", (VOLUME_ID,)),
        "works": scalar(con, "SELECT COUNT(*) FROM works WHERE volume_id=?", (VOLUME_ID,)),
        "index_sections": scalar(
            con, "SELECT COUNT(*) FROM index_sections WHERE volume_id=?", (VOLUME_ID,)
        ),
        "index_entries": scalar(
            con,
            "SELECT COUNT(*) FROM index_entries WHERE section_key IN "
            "(SELECT section_key FROM index_sections WHERE volume_id=?)",
            (VOLUME_ID,),
        ),
    }


def apply_patristic_indices(con: sqlite3.Connection) -> dict[str, int]:
    changed = {}
    changed["runs"] = max(
        0, int(con.execute("DELETE FROM runs WHERE volume_id=?", (VOLUME_ID,)).rowcount)
    )
    changed["volumes"] = max(
        0, int(con.execute("DELETE FROM volumes WHERE volume_id=?", (VOLUME_ID,)).rowcount)
    )
    return changed


def audit_alphabetical_indices(con: sqlite3.Connection) -> dict[str, int]:
    required = {
        "alphabetical_volumes",
        "alphabetical_runs",
        "alphabetical_sections",
        "alphabetical_source_spans",
        "alphabetical_boundaries",
        "alphabetical_nodes",
        "alphabetical_entries",
        "alphabetical_refs",
        "alphabetical_scripture_refs",
        "alphabetical_volume_quality",
    }
    require_tables(con, required)
    section = "SELECT section_key FROM alphabetical_sections WHERE volume_id=?"
    entry = f"SELECT entry_key FROM alphabetical_entries WHERE section_key IN ({section})"
    return {
        "alphabetical_volumes": scalar(
            con, "SELECT COUNT(*) FROM alphabetical_volumes WHERE volume_id=?", (VOLUME_ID,)
        ),
        "alphabetical_runs": scalar(
            con, "SELECT COUNT(*) FROM alphabetical_runs WHERE volume_id=?", (VOLUME_ID,)
        ),
        "alphabetical_sections": scalar(
            con, "SELECT COUNT(*) FROM alphabetical_sections WHERE volume_id=?", (VOLUME_ID,)
        ),
        "alphabetical_source_spans": scalar(
            con, "SELECT COUNT(*) FROM alphabetical_source_spans WHERE volume_id=?", (VOLUME_ID,)
        ),
        "alphabetical_boundaries": scalar(
            con, "SELECT COUNT(*) FROM alphabetical_boundaries WHERE volume_id=?", (VOLUME_ID,)
        ),
        "alphabetical_nodes": scalar(
            con, f"SELECT COUNT(*) FROM alphabetical_nodes WHERE section_key IN ({section})", (VOLUME_ID,)
        ),
        "alphabetical_entries": scalar(
            con, f"SELECT COUNT(*) FROM alphabetical_entries WHERE section_key IN ({section})", (VOLUME_ID,)
        ),
        "alphabetical_refs": scalar(
            con, f"SELECT COUNT(*) FROM alphabetical_refs WHERE entry_key IN ({entry})", (VOLUME_ID,)
        ),
        "alphabetical_scripture_refs": scalar(
            con,
            f"SELECT COUNT(*) FROM alphabetical_scripture_refs WHERE entry_key IN ({entry})",
            (VOLUME_ID,),
        ),
        "alphabetical_volume_quality": scalar(
            con,
            "SELECT COUNT(*) FROM alphabetical_volume_quality WHERE volume_id=?",
            (VOLUME_ID,),
        ),
    }


def apply_alphabetical_indices(con: sqlite3.Connection) -> dict[str, int]:
    cursor = con.execute(
        "DELETE FROM alphabetical_volumes WHERE volume_id=?", (VOLUME_ID,)
    )
    return {"alphabetical_volumes": max(0, int(cursor.rowcount))}


ANALYSIS_DIRECT_TABLES = (
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


def audit_alphabetical_analysis(con: sqlite3.Connection) -> dict[str, int]:
    require_tables(con, set(ANALYSIS_DIRECT_TABLES) | {"analysis_entry_fts"})
    counts = {
        table: scalar(
            con, f'SELECT COUNT(*) FROM "{table}" WHERE volume_id=?', (VOLUME_ID,)
        )
        for table in ANALYSIS_DIRECT_TABLES
    }
    counts["analysis_entry_fts"] = scalar(
        con, "SELECT COUNT(*) FROM analysis_entry_fts WHERE volume_id=?", (VOLUME_ID,)
    )
    return counts


def apply_alphabetical_analysis(con: sqlite3.Connection) -> dict[str, int]:
    changed = {}
    for table in ("analysis_volume_stages", "analysis_entry_fts", "analysis_stage_runs", "analysis_volumes"):
        cursor = con.execute(f'DELETE FROM "{table}" WHERE volume_id=?', (VOLUME_ID,))
        changed[table] = max(0, int(cursor.rowcount))
    return changed


def scripture_target_file_ids(con: sqlite3.Connection) -> list[int]:
    return [
        int(value)
        for value in ids_from_moved_paths(
            con,
            table="citation_files",
            id_column="file_id",
            path_column="file_path",
            volume_column="volume_id",
        )
    ]


def scripture_group_ids(con: sqlite3.Connection, file_ids: Sequence[int]) -> list[int]:
    values: list[int] = []
    for batch in chunks(file_ids):
        placeholders = ",".join("?" for _ in batch)
        values.extend(
            int(row[0])
            for row in con.execute(
                f"SELECT group_id FROM citation_groups WHERE file_id IN ({placeholders})",
                tuple(batch),
            )
        )
    return values


def audit_scripture_citations(con: sqlite3.Connection) -> dict[str, int]:
    required = {
        "citation_volumes",
        "citation_files",
        "citation_file_pages",
        "citation_groups",
        "citation_occurrences",
        "citation_index_seeds",
        "citation_seed_links",
        "citation_book_aliases",
        "citation_format_profiles",
    }
    require_tables(con, required)
    file_ids = scripture_target_file_ids(con)
    group_ids = scripture_group_ids(con, file_ids)
    return {
        "citation_files": len(file_ids),
        "citation_file_pages": count_for_ids(
            con, "citation_file_pages", "file_id", file_ids
        ),
        "citation_groups": len(group_ids),
        "citation_occurrences": count_for_ids(
            con, "citation_occurrences", "group_id", group_ids
        ),
        "citation_index_seeds_volume": scalar(
            con, "SELECT COUNT(*) FROM citation_index_seeds WHERE volume_id=?", (VOLUME_ID,)
        ),
        "citation_book_aliases_volume": scalar(
            con, "SELECT COUNT(*) FROM citation_book_aliases WHERE volume_id=?", (VOLUME_ID,)
        ),
        "citation_format_profiles_volume": scalar(
            con, "SELECT COUNT(*) FROM citation_format_profiles WHERE volume_id=?", (VOLUME_ID,)
        ),
        "citation_volume_rows_preserved": scalar(
            con, "SELECT COUNT(*) FROM citation_volumes WHERE volume_id=?", (VOLUME_ID,)
        ),
    }


def apply_scripture_citations(con: sqlite3.Connection) -> dict[str, int]:
    file_ids = scripture_target_file_ids(con)
    changed = {
        "citation_files": delete_for_ids(con, "citation_files", "file_id", file_ids)
    }
    for table in ("citation_index_seeds", "citation_book_aliases", "citation_format_profiles"):
        cursor = con.execute(f'DELETE FROM "{table}" WHERE volume_id=?', (VOLUME_ID,))
        changed[table] = max(0, int(cursor.rowcount))
    cursor = con.execute(
        "UPDATE citation_volumes SET scan_status='partial', last_run_id=NULL "
        "WHERE volume_id=?",
        (VOLUME_ID,),
    )
    changed["citation_volumes_marked_partial"] = max(0, int(cursor.rowcount))
    return changed


def audit_summary_scripture(con: sqlite3.Connection) -> dict[str, int]:
    require_tables(con, {"pages", "scripture_mentions", "rejected_mentions"})
    page_ids = [
        int(row[0])
        for row in con.execute(
            "SELECT id FROM pages WHERE volume_id=? AND physical_page BETWEEN ? AND ?",
            (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
        )
    ]
    return {
        "pages": len(page_ids),
        "scripture_mentions": count_for_ids(con, "scripture_mentions", "page_id", page_ids),
        "rejected_mentions": count_for_ids(con, "rejected_mentions", "page_id", page_ids),
    }


def apply_summary_scripture(con: sqlite3.Connection) -> dict[str, int]:
    page_ids = [
        int(row[0])
        for row in con.execute(
            "SELECT id FROM pages WHERE volume_id=? AND physical_page BETWEEN ? AND ?",
            (VOLUME_ID, FIRST_PAGE, LAST_PAGE),
        )
    ]
    return {"pages": delete_for_ids(con, "pages", "id", page_ids)}


AUDITORS = {
    "patristica_resumos.db": audit_resumos,
    "patristica_keywords.db": audit_keywords,
    "editorial_page_estimator.db": audit_editorial_cache,
    "patristic_indices.db": audit_patristic_indices,
    "alphabetical_indices.db": audit_alphabetical_indices,
    "alphabetical_analysis.db": audit_alphabetical_analysis,
    "scripture_citations.db": audit_scripture_citations,
    "summary_scripture_citations_v3.db": audit_summary_scripture,
}
APPLIERS = {
    "patristica_resumos.db": apply_resumos,
    "patristica_keywords.db": apply_keywords,
    "editorial_page_estimator.db": apply_editorial_cache,
    "patristic_indices.db": apply_patristic_indices,
    "alphabetical_indices.db": apply_alphabetical_indices,
    "alphabetical_analysis.db": apply_alphabetical_analysis,
    "scripture_citations.db": apply_scripture_citations,
    "summary_scripture_citations_v3.db": apply_summary_scripture,
}


def audit_one(path: Path, name: str) -> dict[str, int]:
    with connect_readonly(path) as con:
        return AUDITORS[name](con)


def apply_one(path: Path, name: str) -> tuple[dict[str, int], dict[str, int]]:
    con = connect_writable(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        changed = APPLIERS[name](con)
        after = AUDITORS[name](con)
        ignored = {
            "resumo_runs_preserved",
            "page_overrides_preserved",
            "citation_volume_rows_preserved",
        }
        remaining = {key: value for key, value in after.items() if value and key not in ignored}
        if remaining:
            raise RuntimeError(f"Registros selecionados permanecem em {name}: {remaining}")
        violations = list(con.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"Violacoes de FK em {name}: {violations[:20]}")
        con.commit()
        return changed, after
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def source_guard(source: Path) -> dict[str, int]:
    counts = {"images": 0, "text": 0}
    for kind in counts:
        directory = source / kind
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            page_num = trailing_page_number(path)
            if page_num is not None and is_removed_page(page_num):
                counts[kind] += 1
    return counts


@contextmanager
def resumo_lock(data_dir: Path) -> Iterator[None]:
    lock_dir = data_dir / ".resumo_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"patristica_resumos.db.{VOLUME_ID}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"PL020 esta sendo processado: {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def selected_databases(values: list[str] | None) -> tuple[str, ...]:
    if not values or values == ["all"]:
        return DATABASE_ORDER
    selected: list[str] = []
    for value in values:
        if value == "all":
            raise SystemExit("Use --database all sem outros --database")
        if value not in DATABASE_ORDER:
            raise SystemExit(f"Banco derivado desconhecido: {value}")
        if value not in selected:
            selected.append(value)
    return tuple(selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--source-volume-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database", action="append")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-source-guard", action="store_true")
    parser.add_argument(
        "--confirm",
        help=f"Obrigatorio com --apply; valor exato: {CONFIRMATION}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"Recusado sem --confirm {CONFIRMATION}")
    if args.apply and args.report is None:
        raise SystemExit("--report e obrigatorio com --apply")
    names = selected_databases(args.database)
    data_dir = args.data_dir.resolve()
    guard = source_guard(args.source_volume_dir.resolve())
    if args.apply and any(guard.values()) and not args.skip_source_guard:
        raise SystemExit(
            "O segmento ainda existe na origem; rode primeiro "
            "quarantine_pl020_embedded_pl021.py. Para uma excecao consciente, "
            "use --skip-source-guard."
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "operation": "cleanup_pl020_split_derivatives",
        "status": "running" if args.apply else "dry_run",
        "started_at": utc_now(),
        "apply": bool(args.apply),
        "volume_id": VOLUME_ID,
        "first_page": FIRST_PAGE,
        "last_page": LAST_PAGE,
        "data_dir": str(data_dir),
        "source_guard": guard,
        "preserved_databases": list(PRESERVED_DATABASES),
        "databases": [],
        "post_actions": [
            "rebuild PL020 in patristic_indices.db",
            "rebuild PL020 in alphabetical_indices.db and alphabetical_analysis.db",
            "rescan PL020 in scripture_citations.db",
            "rebuild summary_scripture_citations_v3.db",
            "regenerate enrichment shards and web exports",
        ],
    }
    report_path = args.report.resolve() if args.report else None
    if report_path:
        write_json_atomic(report_path, report)

    try:
        with resumo_lock(data_dir) if args.apply else _null_context():
            for name in names:
                path = data_dir / name
                if not path.is_file():
                    report["databases"].append(
                        {"database": name, "path": str(path), "status": "missing"}
                    )
                    continue
                before = audit_one(path, name)
                item: dict[str, Any] = {
                    "database": name,
                    "path": str(path),
                    "status": "audited",
                    "selected_rows_before": before,
                    "scope": (
                        "whole_PL020"
                        if name
                        in {
                            "patristic_indices.db",
                            "alphabetical_indices.db",
                            "alphabetical_analysis.db",
                        }
                        else "PL020_pages_612_1223"
                    ),
                }
                if args.apply:
                    changed, after = apply_one(path, name)
                    item.update(
                        {
                            "status": "applied",
                            "statement_rowcounts": changed,
                            "selected_rows_after": after,
                        }
                    )
                report["databases"].append(item)
                if report_path:
                    write_json_atomic(report_path, report)
    except BaseException as exc:
        report["status"] = "failed"
        report["failed_at"] = utc_now()
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report_path:
            write_json_atomic(report_path, report)
        raise

    report["status"] = "applied" if args.apply else "dry_run"
    report["finished_at"] = utc_now()
    if report_path:
        write_json_atomic(report_path, report)
    compact = {
        "status": report["status"],
        "source_guard": guard,
        "databases": {
            item["database"]: item.get("selected_rows_after", item.get("selected_rows_before"))
            for item in report["databases"]
        },
        "preserved_databases": list(PRESERVED_DATABASES),
        "report": str(report_path) if report_path else None,
    }
    print(json.dumps(compact, ensure_ascii=False, sort_keys=True))
    return 0


@contextmanager
def _null_context() -> Iterator[None]:
    yield


if __name__ == "__main__":
    raise SystemExit(main())
