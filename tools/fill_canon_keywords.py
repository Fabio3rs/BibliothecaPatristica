#!/usr/bin/env python3
"""Mantém flags bíblicas e nomes canônicos dos grupos de keywords.

A reconstrução dos nomes canônicos usa exclusivamente os grupos HDBSCAN já
persistidos. Ela não recalcula embeddings, UMAP nem HDBSCAN.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripture_ref_normalizer import extract_citations_from_value_cached


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_keywords.db"
DEFAULT_OVERRIDES = Path(__file__).with_name("keyword_canonical_overrides.json")


@dataclass(frozen=True)
class CanonicalCandidate:
    group_id: int
    keyword_id: int
    keyword_norm: str
    keyword_original: str
    membership_probability: float
    page_count: int

    @property
    def score(self) -> float:
        """Equilibra uso real, centralidade do cluster e legibilidade."""
        words = max(1, len(self.keyword_norm.split()))
        return (
            math.log1p(self.page_count)
            + 1.5 * self.membership_probability
            - 0.12 * words
            - 0.01 * len(self.keyword_norm)
        )


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def ensure_scripture_field(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(keywords)")}
    if "is_scripture_citation" not in columns:
        conn.execute(
            "ALTER TABLE keywords "
            "ADD COLUMN is_scripture_citation BOOLEAN DEFAULT FALSE"
        )
        conn.commit()


def _is_scripture_citation(keyword_text: str) -> bool:
    citation_results = extract_citations_from_value_cached(
        keyword_text,
        source_kind="keywords",
        source_path="backfill",
        support_mode=False,
    )
    return bool(citation_results and citation_results[0]["number"])


def backfill_keywords_scripture_flag(
    conn: sqlite3.Connection, batch_size: int = 500, dry_run: bool = False
) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT id, keyword_original FROM keywords ORDER BY id"
    ).fetchall()
    true_ids: list[int] = []
    false_ids: list[int] = []

    for row in rows:
        target = true_ids if _is_scripture_citation(row["keyword_original"]) else false_ids
        target.append(int(row["id"]))

    if not dry_run:
        for value, ids in ((1, true_ids), (0, false_ids)):
            for start in range(0, len(ids), batch_size):
                conn.executemany(
                    "UPDATE keywords SET is_scripture_citation = ? WHERE id = ?",
                    ((value, keyword_id) for keyword_id in ids[start : start + batch_size]),
                )
        conn.commit()

    mode = "DRY RUN" if dry_run else "OK"
    print(
        f"[{mode}] citações bíblicas: {len(true_ids)} | "
        f"não bíblicas: {len(false_ids)}"
    )
    return len(true_ids), len(false_ids)


def load_overrides(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    overrides = payload.get("overrides", [])
    for item in overrides:
        if not item.get("anchor_norm") or not item.get("canonical_norm"):
            raise ValueError(f"Override incompleto em {path}: {item!r}")
    return overrides


def fetch_candidates(conn: sqlite3.Connection) -> list[CanonicalCandidate]:
    sql = """
        SELECT
            k.hdbscan_group_id AS group_id,
            k.id AS keyword_id,
            k.keyword_norm,
            k.keyword_original,
            COALESCE(kc.membership_probability, 0.0) AS membership_probability,
            COUNT(o.pagina_id) AS page_count
        FROM keywords k
        JOIN keyword_clusters kc ON kc.keyword_id = k.id
        LEFT JOIN keyword_occurrence o ON o.keyword_id = k.id
        WHERE k.hdbscan_group_id IS NOT NULL
          AND k.hdbscan_group_id >= 0
          AND COALESCE(k.is_scripture_citation, 0) = 0
        GROUP BY
            k.hdbscan_group_id,
            k.id,
            k.keyword_norm,
            k.keyword_original,
            kc.membership_probability
        ORDER BY k.hdbscan_group_id, k.id
    """
    return [
        CanonicalCandidate(
            group_id=int(row["group_id"]),
            keyword_id=int(row["keyword_id"]),
            keyword_norm=row["keyword_norm"],
            keyword_original=row["keyword_original"],
            membership_probability=float(row["membership_probability"] or 0.0),
            page_count=int(row["page_count"] or 0),
        )
        for row in conn.execute(sql)
    ]


def select_canonical_candidates(
    candidates: list[CanonicalCandidate], overrides: list[dict[str, str]]
) -> dict[int, CanonicalCandidate]:
    by_group: dict[int, list[CanonicalCandidate]] = {}
    by_norm: dict[str, CanonicalCandidate] = {}
    for candidate in candidates:
        by_group.setdefault(candidate.group_id, []).append(candidate)
        by_norm[candidate.keyword_norm] = candidate

    selected = {
        group_id: max(
            members,
            key=lambda item: (item.score, item.page_count, -item.keyword_id),
        )
        for group_id, members in by_group.items()
    }

    for override in overrides:
        anchor = by_norm.get(override["anchor_norm"])
        canonical = by_norm.get(override["canonical_norm"])
        if anchor is None:
            print(f"[AVISO] âncora ausente: {override['anchor_norm']!r}")
            continue
        if canonical is None or canonical.group_id != anchor.group_id:
            print(
                "[AVISO] canônico ausente do grupo da âncora: "
                f"{override['canonical_norm']!r}"
            )
            continue
        selected[anchor.group_id] = canonical

    return selected


def backup_current_table(conn: sqlite3.Connection, path: Path) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'cluster_canonical_names'"
    ).fetchone()
    rows = []
    if exists:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM cluster_canonical_names ORDER BY group_id"
            )
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "table": "cluster_canonical_names",
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(rows)


def rebuild_canonical_names(
    conn: sqlite3.Connection,
    overrides: list[dict[str, str]],
    backup_out: Path | None = None,
    dry_run: bool = False,
) -> dict[int, CanonicalCandidate]:
    candidates = fetch_candidates(conn)
    selected = select_canonical_candidates(candidates, overrides)
    expected_groups = conn.execute(
        """
        SELECT COUNT(DISTINCT hdbscan_group_id)
        FROM keywords
        WHERE hdbscan_group_id IS NOT NULL
          AND hdbscan_group_id >= 0
          AND COALESCE(is_scripture_citation, 0) = 0
        """
    ).fetchone()[0]
    if len(selected) != expected_groups:
        raise RuntimeError(
            f"Cobertura canônica incompleta: {len(selected)} de {expected_groups} grupos"
        )

    if dry_run:
        print(f"[DRY RUN] {len(selected)} nomes canônicos seriam reconstruídos")
        return selected

    if backup_out is not None:
        backed_up = backup_current_table(conn, backup_out)
        print(f"[OK] backup: {backed_up} linhas em {backup_out}")

    rows = [
        (
            item.group_id,
            item.keyword_norm,
            item.keyword_original,
            item.membership_probability,
        )
        for item in sorted(selected.values(), key=lambda item: item.group_id)
    ]
    with conn:
        conn.execute("DROP TABLE IF EXISTS cluster_canonical_names_new")
        conn.execute(
            """
            CREATE TABLE cluster_canonical_names_new (
                group_id INTEGER NOT NULL PRIMARY KEY,
                nome_canonico TEXT NOT NULL UNIQUE,
                nome_canonico_original TEXT NOT NULL,
                score_ancora REAL NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO cluster_canonical_names_new (
                group_id,
                nome_canonico,
                nome_canonico_original,
                score_ancora
            ) VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.execute(
            "SELECT COUNT(*) FROM cluster_canonical_names_new"
        ).fetchone()[0]
        if inserted != expected_groups:
            raise RuntimeError(
                f"Tabela temporária incompleta: {inserted} de {expected_groups} grupos"
            )
        conn.execute("DROP TABLE IF EXISTS cluster_canonical_names")
        conn.execute(
            "ALTER TABLE cluster_canonical_names_new "
            "RENAME TO cluster_canonical_names"
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_group_id "
            "ON cluster_canonical_names (group_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_nome_canonico "
            "ON cluster_canonical_names (nome_canonico)"
        )

    print(f"[OK] {len(selected)} nomes canônicos reconstruídos atomicamente")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstrói nomes canônicos sem recalcular UMAP/HDBSCAN."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--no-overrides", action="store_true")
    parser.add_argument("--backup-out", type=Path)
    parser.add_argument("--backfill-scripture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = connect_db(args.db)
    try:
        ensure_scripture_field(conn)
        if args.backfill_scripture:
            backfill_keywords_scripture_flag(conn, dry_run=args.dry_run)
        overrides = load_overrides(None if args.no_overrides else args.overrides)
        rebuild_canonical_names(
            conn,
            overrides=overrides,
            backup_out=args.backup_out,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
