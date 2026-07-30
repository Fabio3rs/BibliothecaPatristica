#!/usr/bin/env python3
"""Sincroniza ``teste/*/text/*.txt`` com versões OCR XML do banco.

Por segurança, a execução padrão é somente uma simulação. Use ``--apply``
para substituir os arquivos elegíveis. Se a versão atual não for XML válido,
o histórico é consultado do mais recente para o mais antigo.

Exemplos:
    python scripts/sync_current_ocr_xml.py --since 2026-07-01
    python scripts/sync_current_ocr_xml.py --since 2026-07-01 --volumes PG015 PG016.01
    python scripts/sync_current_ocr_xml.py --since 2026-07-01 --apply
    python scripts/sync_current_ocr_xml.py --ignore-file-mtime --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence


DEFAULT_DB = Path("data/ocr_versions.db")
DEFAULT_BASE_DIR = Path("teste")


@dataclass
class SyncStats:
    selected: int = 0
    not_newer: int = 0
    invalid_xml: int = 0
    missing_file: int = 0
    ambiguous_file: int = 0
    duplicate_current: int = 0
    stale_current: int = 0
    fallback_selected: int = 0
    no_valid_xml: int = 0
    would_update: int = 0
    updated: int = 0
    errors: int = 0


def parse_datetime(value: str) -> datetime:
    """Interpreta data SQLite/ISO e devolve um ``datetime`` UTC."""
    value = value.strip()
    if not value:
        raise ValueError("data vazia")

    if len(value) == 10:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    else:
        normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sqlite_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_valid_page_xml(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    try:
        root = ET.fromstring(stripped)
    except (ET.ParseError, ValueError):
        return False
    local_name = root.tag.rsplit("}", 1)[-1] if isinstance(root.tag, str) else ""
    return local_name.lower() == "pagina"


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"Banco não encontrado: {db_path}")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA query_only = ON")
    return con


def iter_current_rows(
    con: sqlite3.Connection,
    *,
    since: datetime | None = None,
    volumes: Sequence[str] | None = None,
) -> Iterator[sqlite3.Row]:
    filters = ["is_current = 1"]
    params: list[object] = []
    if volumes:
        placeholders = ", ".join("?" for _ in volumes)
        filters.append(f"volume_id IN ({placeholders})")
        params.extend(volumes)

    if since is not None:
        params.append(sqlite_datetime(since))

    query = f"""
        WITH ranked AS (
            SELECT
                id,
                volume_id,
                page_num,
                created_at,
                COUNT(*) OVER (
                    PARTITION BY volume_id, page_num
                ) AS current_count,
                ROW_NUMBER() OVER (
                    PARTITION BY volume_id, page_num
                    ORDER BY datetime(created_at) DESC, id DESC
                ) AS current_rank
            FROM ocr_results
            WHERE {" AND ".join(filters)}
        )
        SELECT id, volume_id, page_num, created_at, current_count
        FROM ranked
        WHERE current_rank = 1
          {"AND datetime(created_at) >= datetime(?)" if since is not None else ""}
        ORDER BY volume_id, page_num
    """
    yield from con.execute(query, params)


def find_target(base_dir: Path, volume_id: str, page_num: int) -> tuple[Path | None, bool]:
    text_dir = base_dir / volume_id / "text"
    if not text_dir.is_dir():
        return None, False

    matches = list(text_dir.glob(f"*-{page_num:03d}.txt"))
    unpadded = list(text_dir.glob(f"*-{page_num}.txt"))
    unique_matches = sorted(set(matches + unpadded))
    if len(unique_matches) > 1:
        return None, True
    if not unique_matches:
        return None, False
    return unique_matches[0], False


def find_latest_valid_fallback(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    page_num: int,
    exclude_id: int,
    since: datetime | None,
) -> sqlite3.Row | None:
    params: list[object] = [volume_id, page_num, exclude_id]
    since_filter = ""
    if since is not None:
        since_filter = "AND datetime(created_at) >= datetime(?)"
        params.append(sqlite_datetime(since))

    rows = con.execute(
        f"""
        SELECT id, text_content, created_at
        FROM ocr_results
        WHERE volume_id = ?
          AND page_num = ?
          AND id != ?
          AND datetime(created_at) IS NOT NULL
          {since_filter}
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        params,
    )
    for row in rows:
        if is_valid_page_xml(row["text_content"]):
            return row
    return None


def atomic_write_text(target: Path, content: str, *, source_timestamp: float) -> None:
    original = target.stat()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temp_path = Path(temporary.name)
            temporary.write(content.encode("utf-8"))
            temporary.flush()
            os.fsync(temporary.fileno())

        os.chmod(temp_path, stat.S_IMODE(original.st_mode))
        now = datetime.now(timezone.utc).timestamp()
        os.utime(temp_path, (original.st_atime, max(now, source_timestamp)))
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def sync_current_xml(
    *,
    db_path: Path = DEFAULT_DB,
    base_dir: Path = DEFAULT_BASE_DIR,
    since: datetime | None = None,
    volumes: Sequence[str] | None = None,
    apply: bool = False,
    ignore_file_mtime: bool = False,
) -> SyncStats:
    stats = SyncStats()
    con = connect_read_only(db_path)
    detail_con: sqlite3.Connection | None = None
    try:
        detail_con = connect_read_only(db_path)
        for row in iter_current_rows(con, since=since, volumes=volumes):
            stats.selected += 1
            label = f"{row['volume_id']} p.{row['page_num']} id={row['id']}"

            if row["current_count"] > 1:
                stats.duplicate_current += 1
                print(
                    f"[WARN] {label}: {row['current_count']} versões atuais; "
                    "usando a mais recente"
                )

            target, ambiguous = find_target(base_dir, row["volume_id"], row["page_num"])
            if ambiguous:
                stats.ambiguous_file += 1
                print(f"[SKIP] {label}: mais de um arquivo correspondente")
                continue
            if target is None:
                stats.missing_file += 1
                print(f"[SKIP] {label}: arquivo correspondente não encontrado")
                continue

            try:
                created_at = parse_datetime(row["created_at"])
                file_mtime = target.stat().st_mtime
            except (OSError, ValueError) as exc:
                stats.errors += 1
                print(f"[ERROR] {label}: não foi possível comparar datas: {exc}")
                continue

            if not ignore_file_mtime and created_at.timestamp() <= file_mtime:
                stats.not_newer += 1
                continue

            # Uma segunda conexão evita reutilizar o snapshot mantido pelo
            # cursor da consulta principal e enxerga trocas recentes de
            # ``is_current`` feitas por outro processo.
            current = detail_con.execute(
                """
                SELECT text_content
                FROM ocr_results
                WHERE id = ? AND is_current = 1
                """,
                (row["id"],),
            ).fetchone()
            if current is None:
                stats.stale_current += 1
                print(f"[SKIP] {label}: deixou de ser a versão atual durante a execução")
                continue

            content = current["text_content"]
            source_id = row["id"]
            source_created_at = created_at
            if not is_valid_page_xml(content):
                stats.invalid_xml += 1
                fallback = find_latest_valid_fallback(
                    detail_con,
                    volume_id=row["volume_id"],
                    page_num=row["page_num"],
                    exclude_id=row["id"],
                    since=since,
                )
                if fallback is None:
                    stats.no_valid_xml += 1
                    print(
                        f"[SKIP] {label}: versão atual inválida e nenhum "
                        "XML <pagina> válido encontrado no histórico"
                    )
                    continue
                try:
                    source_created_at = parse_datetime(fallback["created_at"])
                except ValueError as exc:
                    stats.errors += 1
                    print(f"[ERROR] {label}: data inválida no fallback: {exc}")
                    continue
                content = fallback["text_content"]
                source_id = fallback["id"]
                stats.fallback_selected += 1
                print(
                    f"[FALLBACK] {label}: versão atual não é XML válido; "
                    f"usando id={source_id} de {source_created_at.isoformat()}"
                )

            if not ignore_file_mtime and source_created_at.timestamp() <= file_mtime:
                stats.not_newer += 1
                continue

            if not apply:
                stats.would_update += 1
                print(
                    f"[WOULD-UPDATE] {target} "
                    f"(arquivo={datetime.fromtimestamp(file_mtime, timezone.utc).isoformat()}, "
                    f"banco={source_created_at.isoformat()}, id={source_id})"
                )
                continue

            try:
                # Evita sobrescrever uma alteração concorrente feita depois da
                # primeira comparação.
                if not ignore_file_mtime:
                    latest_mtime = target.stat().st_mtime
                    if source_created_at.timestamp() <= latest_mtime:
                        stats.not_newer += 1
                        continue
                atomic_write_text(
                    target,
                    content,
                    source_timestamp=source_created_at.timestamp(),
                )
            except OSError as exc:
                stats.errors += 1
                print(f"[ERROR] {label}: falha ao substituir {target}: {exc}")
                continue

            stats.updated += 1
            print(f"[UPDATED] {target}")
    finally:
        if detail_con is not None:
            detail_con.close()
        con.close()
    return stats


def print_summary(stats: SyncStats, *, apply: bool) -> None:
    print(
        "[DONE] "
        f"mode={'apply' if apply else 'dry-run'} "
        f"selected={stats.selected} "
        f"not_newer={stats.not_newer} "
        f"invalid_xml={stats.invalid_xml} "
        f"missing_file={stats.missing_file} "
        f"ambiguous_file={stats.ambiguous_file} "
        f"duplicate_current={stats.duplicate_current} "
        f"stale_current={stats.stale_current} "
        f"fallback_selected={stats.fallback_selected} "
        f"no_valid_xml={stats.no_valid_xml} "
        f"would_update={stats.would_update} "
        f"updated={stats.updated} "
        f"errors={stats.errors}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Substitui arquivos OCR .txt pela versão atual do banco ou pelo "
            "fallback histórico mais recente que seja XML <pagina> válido."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument(
        "--since",
        type=parse_datetime,
        help="Data mínima inclusiva em UTC (YYYY-MM-DD ou timestamp ISO).",
    )
    parser.add_argument(
        "--volumes",
        nargs="+",
        metavar="VOL",
        help="Limita a pesquisa aos volumes informados.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa as substituições; sem esta opção apenas simula.",
    )
    parser.add_argument(
        "--ignore-file-mtime",
        action="store_true",
        help="Ignora a data do arquivo e considera todo XML elegível para substituição.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        stats = sync_current_xml(
            db_path=args.db,
            base_dir=args.base_dir,
            since=args.since,
            volumes=args.volumes,
            apply=args.apply,
            ignore_file_mtime=args.ignore_file_mtime,
        )
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"[FATAL] {exc}")
        return 1
    print_summary(stats, apply=args.apply)
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
