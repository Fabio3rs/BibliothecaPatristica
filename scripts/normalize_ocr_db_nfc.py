#!/usr/bin/env python3
"""Audita ou normaliza em NFC os textos do banco OCR.

O modo padrao e somente leitura. Use ``--apply`` para persistir as mudancas.
O processamento e paginado e idempotente para permitir retomada segura.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata


LINE_TEXT_COLUMNS = ("reviewed_text", "qwen_text", "tesseract_text")


def connect_database(path: Path, apply: bool) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Banco SQLite nao encontrado: {resolved}")
    if apply:
        conn = sqlite3.connect(resolved)
    else:
        conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def require_columns(
    conn: sqlite3.Connection, table: str, required: set[str]
) -> None:
    available = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
    }
    missing = required - available
    if missing:
        raise RuntimeError(
            f"Tabela {table} nao possui as colunas exigidas: {sorted(missing)}"
        )


def normalize_line_columns(
    conn: sqlite3.Connection, apply: bool, batch_size: int, stats: Counter
) -> None:
    for column in LINE_TEXT_COLUMNS:
        last_id = 0
        while True:
            rows = conn.execute(
                f"""
                SELECT id, {column} AS text_content
                FROM lines
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                (last_id, batch_size),
            ).fetchall()
            if not rows:
                break
            updates: list[tuple[str, int]] = []
            for row in rows:
                text = row["text_content"]
                if text is None:
                    continue
                normalized = unicodedata.normalize("NFC", text)
                if normalized != text:
                    stats[f"lines.{column}.changed"] += 1
                    updates.append((normalized, int(row["id"])))
            if apply and updates:
                # Atualizar pelas colunas reais aciona o trigger FTS existente.
                conn.executemany(
                    f"""
                    UPDATE lines
                    SET {column} = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    updates,
                )
                conn.commit()
            last_id = int(rows[-1]["id"])


def _duplicate_version(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    normalized_text: str,
    normalized_hash: str,
) -> sqlite3.Row | None:
    run_id = row["run_id"]
    if run_id is None:
        return None
    return conn.execute(
        """
        SELECT id, source_score, is_current, meta_json
        FROM line_versions
        WHERE run_id = ?
          AND line_id = ?
          AND provider = ?
          AND model = ?
          AND text_hash = ?
          AND text_content = ?
          AND id <> ?
        ORDER BY id
        LIMIT 1
        """,
        (
            run_id,
            row["line_id"],
            row["provider"],
            row["model"],
            normalized_hash,
            normalized_text,
            row["id"],
        ),
    ).fetchone()


def _merge_duplicate_version(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    duplicate: sqlite3.Row,
    normalized_text: str,
    normalized_hash: str,
) -> None:
    scores = [
        score
        for score in (row["source_score"], duplicate["source_score"])
        if score is not None
    ]
    source_score = max(scores) if scores else None
    meta_json = duplicate["meta_json"] or row["meta_json"]
    is_current = max(int(row["is_current"]), int(duplicate["is_current"]))
    if int(row["id"]) < int(duplicate["id"]):
        # Preservar o menor id mantem referencias de auditoria tao estaveis
        # quanto possivel; o duplicado precisa sair antes de adotar seu hash.
        conn.execute("DELETE FROM line_versions WHERE id = ?", (duplicate["id"],))
        conn.execute(
            """
            UPDATE line_versions
            SET text_content = ?, text_hash = ?, source_score = ?,
                is_current = ?, meta_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                normalized_text,
                normalized_hash,
                source_score,
                is_current,
                meta_json,
                row["id"],
            ),
        )
        return
    conn.execute(
        """
        UPDATE line_versions
        SET source_score = ?,
            is_current = ?,
            meta_json = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            source_score,
            is_current,
            meta_json,
            duplicate["id"],
        ),
    )
    conn.execute("DELETE FROM line_versions WHERE id = ?", (row["id"],))


def normalize_versions(
    conn: sqlite3.Connection, apply: bool, batch_size: int, stats: Counter
) -> None:
    last_id = 0
    while True:
        rows = conn.execute(
            """
            SELECT id, run_id, line_id, provider, model, text_content,
                   text_hash, source_score, is_current, meta_json
            FROM line_versions
            WHERE id > ?
            ORDER BY id
            LIMIT ?
            """,
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            text = row["text_content"]
            normalized = unicodedata.normalize("NFC", text)
            normalized_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            text_changed = normalized != text
            hash_changed = normalized_hash != row["text_hash"]
            if not text_changed and not hash_changed:
                continue
            if text_changed:
                stats["line_versions.text_content.changed"] += 1
            if hash_changed:
                stats["line_versions.text_hash.changed"] += 1
            if not apply:
                continue

            # Normalizacao pode unir duas grafias equivalentes do mesmo run.
            # Nesse caso preservamos o melhor score/estado e removemos o voto
            # duplicado, mantendo a restricao unica coerente com o novo hash.
            duplicate = _duplicate_version(
                conn, row, normalized, normalized_hash
            )
            if duplicate is not None:
                _merge_duplicate_version(
                    conn,
                    row,
                    duplicate,
                    normalized,
                    normalized_hash,
                )
                stats["line_versions.duplicates_merged"] += 1
            else:
                conn.execute(
                    """
                    UPDATE line_versions
                    SET text_content = ?, text_hash = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (normalized, normalized_hash, row["id"]),
                )
        if apply:
            conn.commit()
        last_id = int(rows[-1]["id"])


def normalize_database(path: Path, apply: bool, batch_size: int) -> dict:
    if batch_size <= 0:
        raise ValueError("--batch-size deve ser > 0")
    conn = connect_database(path, apply)
    stats: Counter = Counter()
    try:
        require_columns(
            conn,
            "lines",
            {"id", "updated_at", *LINE_TEXT_COLUMNS},
        )
        require_columns(
            conn,
            "line_versions",
            {
                "id",
                "run_id",
                "line_id",
                "provider",
                "model",
                "text_content",
                "text_hash",
                "source_score",
                "is_current",
                "meta_json",
                "updated_at",
            },
        )
        normalize_line_columns(conn, apply, batch_size, stats)
        normalize_versions(conn, apply, batch_size, stats)
    finally:
        conn.close()
    return {
        "database": str(path.resolve()),
        "mode": "apply" if apply else "dry-run",
        "batch_size": batch_size,
        "changes": dict(sorted(stats.items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("ocr.db"))
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persiste as correcoes; sem esta opcao apenas audita.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = normalize_database(args.db, args.apply, args.batch_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
