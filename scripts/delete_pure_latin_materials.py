#!/usr/bin/env python3
"""Remove linhas estritamente latinas inseridas após um corte temporal.

A classificação é feita linha a linha, usando o melhor texto disponível:
``reviewed_text``, depois ``qwen_text`` e então ``tesseract_text``. Uma linha
só é removida quando contém letras latinas e nenhuma letra grega ou de outro
alfabeto. Pontuação, números e espaços não interferem na classificação.

Por segurança, o programa apenas mostra a prévia, salvo quando ``--apply`` é
informado. Em exclusões reais, os triggers do FTS5 são suspensos e o índice é
reconstruído uma única vez ao final, vinculando ``lines_fts.rowid = lines.id``.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata


FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE lines_fts USING fts5(
    line_id UNINDEXED,
    page_id,
    volume,
    search_text,
    tokenize='unicode61 remove_diacritics 2'
)
"""

FTS_INSERT_TRIGGER_SQL = """
CREATE TRIGGER lines_fts_ai AFTER INSERT ON lines BEGIN
    INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
    VALUES (
        new.id,
        new.id,
        COALESCE(new.page_id, ''),
        COALESCE(new.volume, ''),
        trim(
            COALESCE(new.reviewed_text, '') || ' ' ||
            COALESCE(new.qwen_text, '') || ' ' ||
            COALESCE(new.tesseract_text, '')
        )
    );
END;
"""

FTS_DELETE_TRIGGER_SQL = """
CREATE TRIGGER lines_fts_ad AFTER DELETE ON lines BEGIN
    DELETE FROM lines_fts WHERE rowid = old.id;
END;
"""

FTS_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER lines_fts_au
AFTER UPDATE OF page_id, volume, reviewed_text, qwen_text, tesseract_text
ON lines BEGIN
    DELETE FROM lines_fts WHERE rowid = old.id;
    INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
    VALUES (
        new.id,
        new.id,
        COALESCE(new.page_id, ''),
        COALESCE(new.volume, ''),
        trim(
            COALESCE(new.reviewed_text, '') || ' ' ||
            COALESCE(new.qwen_text, '') || ' ' ||
            COALESCE(new.tesseract_text, '')
        )
    );
END;
"""

FTS_TRIGGER_NAMES = ("lines_fts_ai", "lines_fts_ad", "lines_fts_au")
FTS_TRIGGER_STATEMENTS = (
    FTS_INSERT_TRIGGER_SQL,
    FTS_DELETE_TRIGGER_SQL,
    FTS_UPDATE_TRIGGER_SQL,
)


@dataclass
class ScanResult:
    target_ids: list[int] = field(default_factory=list)
    session_ids: set[int] = field(default_factory=set)
    totals: dict[str, int] = field(default_factory=dict)
    examples: list[tuple[int, str | None, str | None, str]] = field(
        default_factory=list
    )


def parse_cutoff(value: str) -> str:
    """Valida data ou timestamp ISO e conserva o valor aceito pelo SQLite."""
    candidate = value.strip()
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "use uma data/timestamp ISO, por exemplo 2026-08-07"
        ) from exc
    return candidate


def effective_text(
    reviewed_text: str | None,
    qwen_text: str | None,
    tesseract_text: str | None,
) -> str:
    for value in (reviewed_text, qwen_text, tesseract_text):
        if value and value.strip():
            return value
    return ""


def unicode_letter_counts(text: str) -> tuple[int, int, int]:
    """Conta letras latinas, gregas e de outras escritas pelo nome Unicode."""
    latin = greek = other = 0
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue
        name = unicodedata.name(char, "")
        if "LATIN" in name:
            latin += 1
        elif "GREEK" in name:
            greek += 1
        else:
            other += 1
    return latin, greek, other


def classify_counts(latin: int, greek: int, other: int) -> str:
    if latin and not greek and not other:
        return "latin_only"
    if latin and greek:
        return "latin_greek_mixed"
    if greek:
        return "greek_without_latin"
    if other:
        return "other_script"
    return "no_letters"


def connect_database(path: Path, read_only: bool) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"banco não encontrado: {path}")
    if read_only:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size = -64000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def scan_lines(
    conn: sqlite3.Connection, cutoff: str, example_limit: int = 20
) -> ScanResult:
    result = ScanResult()
    totals: defaultdict[str, int] = defaultdict(int)
    cursor = conn.execute(
        """
        SELECT id, session_id, page_id, volume,
               reviewed_text, qwen_text, tesseract_text
        FROM lines
        WHERE created_at >= ?
        """,
        (cutoff,),
    )
    for row in cursor:
        text = effective_text(
            row["reviewed_text"], row["qwen_text"], row["tesseract_text"]
        )
        category = classify_counts(*unicode_letter_counts(text))
        totals[category] += 1
        if category != "latin_only":
            continue
        result.target_ids.append(row["id"])
        if row["session_id"] is not None:
            result.session_ids.add(row["session_id"])
        if len(result.examples) < example_limit:
            compact_text = " ".join(text.split())
            result.examples.append(
                (row["id"], row["page_id"], row["volume"], compact_text[:120])
            )
    result.totals = dict(totals)
    return result


def print_preview(database: Path, cutoff: str, result: ScanResult) -> None:
    examined = sum(result.totals.values())
    print(f"Banco: {database.resolve()}")
    print(f"Corte inclusivo: created_at >= {cutoff!r}")
    print(f"Linhas examinadas: {examined:,}")
    for category in (
        "latin_only",
        "latin_greek_mixed",
        "greek_without_latin",
        "other_script",
        "no_letters",
    ):
        print(f"  {category}: {result.totals.get(category, 0):,} linhas")
    print(f"Alvo da exclusão: {len(result.target_ids):,} linhas")
    if result.examples:
        print("Exemplos que seriam apagados:")
        for line_id, page_id, volume, text in result.examples:
            print(f"  id={line_id} {page_id or '?'} ({volume or '?'}): {text}")


def backup_database(conn: sqlite3.Connection, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"o backup já existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_conn = sqlite3.connect(destination)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()


def has_fts_table(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'lines_fts'
            """
        ).fetchone()
        is not None
    )


def drop_fts_triggers(conn: sqlite3.Connection) -> None:
    for trigger_name in FTS_TRIGGER_NAMES:
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def suspend_fts_updates(conn: sqlite3.Connection) -> bool:
    """Desativa manutenção linha a linha; retorna se havia tabela FTS."""
    exists = has_fts_table(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        drop_fts_triggers(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return exists


def rebuild_fts(conn: sqlite3.Connection, optimize: bool = False) -> int:
    """Recria o FTS a partir de lines com rowid estável e triggers eficientes."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        drop_fts_triggers(conn)
        conn.execute("DROP TABLE IF EXISTS lines_fts")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(FTS_TABLE_SQL)
        conn.execute(
            """
            INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
            SELECT
                id,
                id,
                COALESCE(page_id, ''),
                COALESCE(volume, ''),
                trim(
                    COALESCE(reviewed_text, '') || ' ' ||
                    COALESCE(qwen_text, '') || ' ' ||
                    COALESCE(tesseract_text, '')
                )
            FROM lines
            """
        )
        for statement in FTS_TRIGGER_STATEMENTS:
            conn.execute(statement)
        if optimize:
            conn.execute("INSERT INTO lines_fts(lines_fts) VALUES('optimize')")
        count = int(conn.execute("SELECT COUNT(*) FROM lines_fts").fetchone()[0])
        lines_count = int(conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0])
        if count != lines_count:
            raise RuntimeError(
                f"reconstrução FTS incompleta: lines={lines_count}, FTS={count}"
            )
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise


def chunks(values: list[int], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def reconcile_sessions(conn: sqlite3.Connection, session_ids: set[int]) -> None:
    for session_id in sorted(session_ids):
        row = conn.execute(
            "SELECT COUNT(*) FROM lines WHERE session_id = ?", (session_id,)
        ).fetchone()
        count = int(row[0])
        if count == 0:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            continue
        volumes = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT volume
                FROM lines
                WHERE session_id = ? AND volume IS NOT NULL AND volume <> ''
                ORDER BY volume
                """,
                (session_id,),
            )
        ]
        conn.execute(
            """
            UPDATE sessions
            SET sample_size = ?, volumes = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (count, json.dumps(volumes, ensure_ascii=False), session_id),
        )


def delete_targets(
    conn: sqlite3.Connection,
    line_ids: list[int],
    session_ids: set[int],
    batch_size: int,
) -> int:
    deleted = 0
    line_ids.sort(reverse=True)
    print(f"Linhas a serem apagadas: {len(line_ids):,}")

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TEMP TABLE ids_to_delete (
                id INTEGER PRIMARY KEY
            )
            """
        )
        for batch in chunks(line_ids, batch_size):
            conn.executemany(
                "INSERT INTO ids_to_delete(id) VALUES (?)",
                ((line_id,) for line_id in batch),
            )
        cursor = conn.execute(
            "DELETE FROM lines WHERE id IN (SELECT id FROM ids_to_delete)"
        )
        deleted = cursor.rowcount
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    print(f"Linhas apagadas: {deleted:,}/{len(line_ids):,}", file=sys.stderr)

    conn.execute("BEGIN IMMEDIATE")
    try:
        reconcile_sessions(conn, session_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return deleted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("ocr.db"))
    parser.add_argument(
        "--since",
        type=parse_cutoff,
        help="corte inclusivo ISO aplicado a lines.created_at",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="executa a exclusão; sem esta opção, apenas mostra a prévia",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="antes de apagar, cria um backup SQLite consistente neste caminho",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2_000,
        help="IDs preparados por lote antes do DELETE único (default: 2000)",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=20,
        help="quantidade de linhas exibidas na prévia (default: 20)",
    )
    parser.add_argument(
        "--keep-fts",
        action="store_true",
        help=(
            "mantém os triggers FTS durante o DELETE; é muito mais lento e existe "
            "apenas como escape hatch"
        ),
    )
    parser.add_argument(
        "--rebuild-fts-only",
        action="store_true",
        help="não apaga linhas; apenas recria o FTS e seus triggers",
    )
    parser.add_argument(
        "--optimize-fts",
        action="store_true",
        help="executa optimize após reconstruir o FTS (opcional e custoso)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size deve ser maior que zero")
    if args.examples < 0:
        raise SystemExit("--examples não pode ser negativo")
    if not args.rebuild_fts_only and args.since is None:
        raise SystemExit("--since é obrigatório, salvo com --rebuild-fts-only")

    write_mode = args.apply or args.rebuild_fts_only
    conn = connect_database(args.db, read_only=not write_mode)
    try:
        if args.rebuild_fts_only:
            print("Reconstruindo FTS com rowid = lines.id...", file=sys.stderr)
            count = rebuild_fts(conn, optimize=args.optimize_fts)
            print(f"FTS reconstruído: {count:,} documentos.")
            return 0

        result = scan_lines(conn, args.since, args.examples)
        print_preview(args.db, args.since, result)
        if not args.apply:
            print("Prévia somente; use --apply para confirmar a exclusão.")
            return 0
        if not result.target_ids:
            print("Nenhuma linha atende ao critério; nada foi alterado.")
            return 0
        if args.backup:
            print(f"Criando backup em {args.backup}...", file=sys.stderr)
            backup_database(conn, args.backup)
        else:
            print("AVISO: exclusão solicitada sem --backup.", file=sys.stderr)
        rebuild_after_delete = False
        if not args.keep_fts:
            rebuild_after_delete = suspend_fts_updates(conn)
            if rebuild_after_delete:
                print(
                    "Triggers FTS suspensos; o índice será reconstruído ao final.",
                    file=sys.stderr,
                )
        try:
            deleted = delete_targets(
                conn,
                result.target_ids,
                result.session_ids,
                args.batch_size,
            )
        finally:
            if rebuild_after_delete:
                print("Reconstruindo FTS com rowid = lines.id...", file=sys.stderr)
                count = rebuild_fts(conn, optimize=args.optimize_fts)
                print(
                    f"FTS reconstruído: {count:,} documentos.",
                    file=sys.stderr,
                )
        print(f"Concluído: {deleted:,} linhas apagadas.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
