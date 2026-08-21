#!/usr/bin/env python3
"""
infer.py — inferência via Ollama/OpenAI em batch, atualiza SQLite
Suporta paralelismo via --jobs (workers independentes, lotes atômicos via WAL)
"""

import argparse
import base64
import email.utils
import json
import math
import os
import random
import re
import socket
import sqlite3
import time
import uuid
from typing import Optional
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import requests
import urllib3.connection as _uc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import multiprocessing as mp
import hashlib
from dataclasses import dataclass

try:
    import pytesseract
except ImportError:  # pragma: no cover - fail fast at runtime
    pytesseract = None

from sample import preprocess_image
import cv2
import numpy as np
import io
from PIL import Image

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TOP_P = 0.9
DEFAULT_TEMP = 0.1
DEFAULT_BATCH_SIZE = 50
DEFAULT_DB_TIMEOUT_SECONDS = 120.0
DEFAULT_DB_BUSY_TIMEOUT_MS = 120000
DEFAULT_DB_LOCK_RETRIES = 6
DEFAULT_DB_LOCK_RETRY_DELAY = 1.0
DEFAULT_MIN_IMAGE_SIDE = 11
DEFAULT_API_CONCURRENCY = None
DEFAULT_RATE_LIMIT_RETRIES = 8
DEFAULT_RATE_LIMIT_BACKOFF_BASE = 1.0
DEFAULT_RATE_LIMIT_BACKOFF_MAX = 60.0
CROP_PADDING = 2
MAX_ERROR_BODY_CHARS = 2000
SYSTEM_PROMPT = """You are a precise OCR post-processor specializing in classical Latin and Ancient Greek manuscripts and printed editions.
Your task: transcribe EXACTLY what you see in the image — a single line of text from a historical printed book.

Rules:
- Output ONLY the transcribed text, nothing else. No explanations, no punctuation added, no commentary.
- Preserve original spelling, ligatures, abbreviations, diacritics.
- For Latin: preserve macrons, cedillas, and any special characters visible.
- For Greek: preserve all accents (acute, grave, circumflex), breathings (smooth, rough), and subscripts.
- Do NOT modernize spelling or correct what appear to be errors — transcribe what is printed.
- For partially legible text, transcribe what you can see and use [?] for individual characters you cannot make out.
"""

USER_PROMPT = "Transcribe:"

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def connect_db(path: str, timeout: float = DEFAULT_DB_TIMEOUT_SECONDS) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout = {DEFAULT_DB_BUSY_TIMEOUT_MS}")
    journal_mode = con.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(journal_mode).lower() != "wal":
        con.close()
        raise RuntimeError(f"SQLite não entrou em WAL mode: {journal_mode}")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _run_db_with_retry(
    conn: sqlite3.Connection,
    operation,
    *,
    retries: int = DEFAULT_DB_LOCK_RETRIES,
    delay: float = DEFAULT_DB_LOCK_RETRY_DELAY,
):
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not _is_locked_error(exc):
                raise
            last_exc = exc
            _rollback_quietly(conn)
            if attempt == retries - 1:
                break
            time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Falha inesperada ao executar operação SQLite.")


def claim_batch(
    db: str,
    batch_size: int,
    prefer_missing_backend: "BackendSpec | None" = None,
    conn: sqlite3.Connection | None = None,
    min_image_side: int = DEFAULT_MIN_IMAGE_SIDE,
) -> list[tuple]:
    """
    Reserva atomicamente até `batch_size` linhas pending → processing.
    Retorna lista de (id, line_image, tesseract_text).
    Quando `prefer_missing_backend` é informado, prioriza primeiro linhas sem
    versão registrada para esse `provider/model` em `line_versions`.
    Depois aplica `agreement_score ASC` e `id ASC` para desempate.
    Funciona com SQLite em transação explícita para manter a seleção atômica.
    """
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        def _claim() -> list[tuple]:
            conn.execute("BEGIN IMMEDIATE")
            _skip_too_small_claimable_rows(
                conn,
                min_image_side=min_image_side,
                prefer_missing_backend=prefer_missing_backend,
            )
            ids = _select_claimable_ids(
                conn,
                batch_size=batch_size,
                prefer_missing_backend=prefer_missing_backend,
                min_image_side=min_image_side,
            )
            if not ids:
                conn.commit()
                return []
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"""
                UPDATE lines
                SET status = 'processing', updated_at = datetime('now')
                WHERE id IN ({placeholders})
                RETURNING id, image_path, bbox, line_image, tesseract_text
                """,
                ids,
            ).fetchall()
            conn.commit()
            rows_by_id = {r["id"]: r for r in rows}
            return [
                (
                    rows_by_id[line_id]["id"],
                    rows_by_id[line_id]["image_path"],
                    rows_by_id[line_id]["bbox"],
                    rows_by_id[line_id]["line_image"],
                    rows_by_id[line_id]["tesseract_text"],
                )
                for line_id in ids
            ]

        return _run_db_with_retry(conn, _claim)
    finally:
        if owns_conn:
            conn.close()


def _skip_too_small_claimable_rows(
    conn: sqlite3.Connection,
    *,
    min_image_side: int,
    prefer_missing_backend: "BackendSpec | None",
) -> int:
    """
    Retira atomicamente da fila bboxes que jamais atingirão o mínimo do modelo.

    O crop acrescenta `CROP_PADDING` de cada lado. A checagem aqui usa portanto
    o tamanho máximo possível; clipping nas bordas é validado depois no worker.
    """
    small_bbox_sql = """
        (
            (
                CASE
                    WHEN json_valid(bbox)
                    THEN CAST(json_extract(bbox, '$.w') AS INTEGER)
                END
            ) + ? < ?
            OR (
                CASE
                    WHEN json_valid(bbox)
                    THEN CAST(json_extract(bbox, '$.h') AS INTEGER)
                END
            ) + ? < ?
        )
    """
    padding = 2 * CROP_PADDING
    size_params: tuple[object, ...] = (
        padding,
        min_image_side,
        padding,
        min_image_side,
    )

    if prefer_missing_backend is None:
        cursor = conn.execute(
            f"""
            UPDATE lines
            SET status = 'skipped', updated_at = datetime('now')
            WHERE status = 'pending'
              AND {small_bbox_sql}
            """,
            size_params,
        )
        return cursor.rowcount

    cursor = conn.execute(
        f"""
        UPDATE lines
        SET status = 'skipped', updated_at = datetime('now')
        WHERE (
                status = 'pending'
                OR (
                    status = 'error'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM line_versions lv
                        WHERE lv.line_id = lines.id
                          AND lv.provider = ?
                          AND lv.model = ?
                    )
                )
              )
          AND {small_bbox_sql}
        """,
        (
            prefer_missing_backend.provider,
            prefer_missing_backend.model,
            *size_params,
        ),
    )
    return cursor.rowcount


def _fetch_ids_for_query(
    conn: sqlite3.Connection,
    query: str,
    params: list[object] | tuple[object, ...],
) -> list[int]:
    return [int(row[0]) for row in conn.execute(query, params).fetchall()]


def _fetch_line_candidates(
    conn: sqlite3.Connection,
    query: str,
    params: list[object] | tuple[object, ...],
) -> list[tuple[int, float]]:
    return [
        (int(row[0]), float(row[1]) if row[1] is not None else 0.0)
        for row in conn.execute(query, params).fetchall()
    ]


def _line_candidate_sort_key(item: tuple[int, float]) -> tuple[float, int]:
    line_id, agreement_score = item
    return (agreement_score, line_id)


def _select_claimable_ids(
    conn: sqlite3.Connection,
    batch_size: int,
    prefer_missing_backend: "BackendSpec | None",
    min_image_side: int = DEFAULT_MIN_IMAGE_SIDE,
) -> list[int]:
    if prefer_missing_backend is None:
        return _fetch_ids_for_query(
            conn,
            """
            SELECT id
            FROM lines
            WHERE status = 'pending'
            ORDER BY IFNULL(agreement_score, 0) ASC,
                     id ASC
            LIMIT ?
            """,
            (batch_size,),
        )

    candidate_rows: list[tuple[int, float]] = []
    seen_ids: set[int] = set()

    for status in ("pending", "inferred", "error"):
        rows = _fetch_line_candidates(
            conn,
            """
            SELECT id, IFNULL(agreement_score, 0)
            FROM lines
            WHERE status = ?
              AND NOT EXISTS (
                    SELECT 1
                    FROM line_versions lv
                    WHERE lv.line_id = lines.id
                      AND lv.provider = ?
                      AND lv.model = ?
                )
              AND COALESCE(
                    (
                        CASE
                            WHEN json_valid(lines.bbox)
                            THEN CAST(json_extract(lines.bbox, '$.w') AS INTEGER)
                        END
                    ) + ? < ?
                    OR (
                        CASE
                            WHEN json_valid(lines.bbox)
                            THEN CAST(json_extract(lines.bbox, '$.h') AS INTEGER)
                        END
                    ) + ? < ?,
                    0
                ) = 0
            ORDER BY IFNULL(agreement_score, 0) ASC,
                     id ASC
            LIMIT ?
            """,
            (
                status,
                prefer_missing_backend.provider,
                prefer_missing_backend.model,
                2 * CROP_PADDING,
                min_image_side,
                2 * CROP_PADDING,
                min_image_side,
                batch_size,
            ),
        )
        for row in rows:
            if row[0] not in seen_ids:
                candidate_rows.append(row)
                seen_ids.add(row[0])

    candidate_rows.sort(key=_line_candidate_sort_key)
    ids = [line_id for line_id, _score in candidate_rows[:batch_size]]
    if len(ids) >= batch_size:
        return ids

    remaining = batch_size - len(ids)
    if ids:
        placeholders = ",".join("?" * len(ids))
        top_up_query = f"""
            SELECT id
            FROM lines
            WHERE status = 'pending'
              AND id NOT IN ({placeholders})
            ORDER BY IFNULL(agreement_score, 0) ASC,
                     id ASC
            LIMIT ?
        """
        top_up_params: list[object] = [*ids, remaining]
    else:
        top_up_query = """
            SELECT id
            FROM lines
            WHERE status = 'pending'
            ORDER BY IFNULL(agreement_score, 0) ASC,
                     id ASC
            LIMIT ?
        """
        top_up_params = [remaining]

    ids.extend(_fetch_ids_for_query(conn, top_up_query, top_up_params))
    return ids


def count_claimable_lines(
    db: str,
    prefer_missing_backend: "BackendSpec | None" = None,
    conn: sqlite3.Connection | None = None,
    min_image_side: int = DEFAULT_MIN_IMAGE_SIDE,
) -> int:
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        pending_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM lines
            WHERE status = 'pending'
            """
        ).fetchone()
        pending = int(pending_row[0]) if pending_row else 0
        if prefer_missing_backend is None:
            return pending

        missing_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM lines l
            LEFT JOIN line_versions lv
              ON lv.line_id = l.id
             AND lv.provider = ?
             AND lv.model = ?
            WHERE l.status IN ('inferred', 'error')
              AND lv.line_id IS NULL
              AND COALESCE(
                    (
                        CASE
                            WHEN json_valid(l.bbox)
                            THEN CAST(json_extract(l.bbox, '$.w') AS INTEGER)
                        END
                    ) + ? < ?
                    OR (
                        CASE
                            WHEN json_valid(l.bbox)
                            THEN CAST(json_extract(l.bbox, '$.h') AS INTEGER)
                        END
                    ) + ? < ?,
                    0
                ) = 0
            """,
            (
                prefer_missing_backend.provider,
                prefer_missing_backend.model,
                2 * CROP_PADDING,
                min_image_side,
                2 * CROP_PADDING,
                min_image_side,
            ),
        ).fetchone()
        missing = int(missing_row[0]) if missing_row else 0
        return pending + missing
    finally:
        if owns_conn:
            conn.close()


def save_result(
    db: str,
    line_id: int,
    consensus_text: str,
    score: float,
    score_llm: float | None = None,
    status: str = "inferred",
    preserve_rejected: bool = False,
    text_column: str = "qwen_text",
    conn: sqlite3.Connection | None = None,
) -> None:
    # Centralizar NFC aqui cobre reruns e futuros chamadores que nao passam pelo
    # normalizador da resposta dos backends.
    consensus_text = unicodedata.normalize("NFC", consensus_text or "")
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        def _save() -> None:
            final_status = status
            if preserve_rejected:
                current_status = conn.execute(
                    "SELECT status FROM lines WHERE id=?",
                    (line_id,),
                ).fetchone()
                if current_status and current_status["status"] == "rejected":
                    final_status = "rejected"
            if text_column not in {"qwen_text", "tesseract_text", "reviewed_text"}:
                raise ValueError(f"Coluna de texto inválida: {text_column}")
            conn.execute(
                f"""
                UPDATE lines
                SET {text_column}=?, agreement_score=?, score_llm=?, status=?, updated_at=datetime('now')
                WHERE id=?
            """,
                (consensus_text, score, score_llm, final_status, line_id),
            )
            conn.commit()

        _run_db_with_retry(conn, _save)
    finally:
        if owns_conn:
            conn.close()


def reference_text_for_row(row: sqlite3.Row, reference_mode: str) -> str:
    """
    Resolve o texto de referência para comparação.

    Observação:
    - `auto` aqui significa `reviewed_text` se existir, senão `qwen_text`.
    - O recálculo de `agreement_score` usa `tesseract_text` explicitamente no modo
      `auto`, porque esse score representa Tesseract vs LLM.
    """
    reviewed = (row["reviewed_text"] or "").strip() if "reviewed_text" in row.keys() else ""
    consensus = (row["qwen_text"] or "").strip() if "qwen_text" in row.keys() else ""
    if reference_mode == "reviewed":
        return reviewed
    if reference_mode == "consensus":
        return consensus
    if reference_mode == "auto":
        return reviewed or consensus
    raise ValueError(f"reference_mode inválido: {reference_mode}")


def ensure_versions_table(db: str) -> None:
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS line_versions (
            id              INTEGER PRIMARY KEY,
            run_id          TEXT,
            line_id         INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
            provider        TEXT NOT NULL,
            model           TEXT NOT NULL,
            text_content    TEXT NOT NULL,
            text_hash       TEXT NOT NULL,
            source_score    REAL,
            is_current      INTEGER NOT NULL DEFAULT 1,
            meta_json       TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_line_versions_dedup
            ON line_versions(run_id, line_id, provider, model, text_hash);
        CREATE INDEX IF NOT EXISTS idx_line_versions_line_id
            ON line_versions(line_id, is_current, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_line_versions_provider_model_line_id
            ON line_versions(provider, model, line_id);
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(line_versions)").fetchall()]
    if "run_id" not in cols:
        conn.execute("ALTER TABLE line_versions ADD COLUMN run_id TEXT")
    conn.execute("DROP INDEX IF EXISTS uq_line_versions_dedup")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_line_versions_dedup
            ON line_versions(run_id, line_id, provider, model, text_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_line_versions_provider_model_line_id
            ON line_versions(provider, model, line_id)
        """
    )
    conn.commit()
    conn.close()


def ensure_claim_indexes(db: str) -> None:
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_lines_status_score_id
            ON lines(status, IFNULL(agreement_score, 0), id);
        """
    )
    conn.commit()
    conn.close()


def ensure_search_update_trigger(db: str) -> None:
    """Evita reconstruir o FTS em updates que não alteram conteúdo pesquisável."""
    conn = connect_db(db)
    has_fts = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lines_fts'"
    ).fetchone()
    if not has_fts:
        conn.close()
        return

    misaligned_rowids = conn.execute(
        """
        SELECT 1
        FROM lines_fts
        WHERE rowid != CAST(line_id AS INTEGER)
        LIMIT 1
        """
    ).fetchone()
    conn.execute("DROP TRIGGER IF EXISTS lines_fts_au")
    if misaligned_rowids:
        # Compatibilidade temporária com bancos criados antes de o FTS usar
        # lines.id como rowid. A manutenção de limpeza migra o índice.
        conn.execute(
            """
            CREATE TRIGGER lines_fts_au
            AFTER UPDATE OF page_id, volume, reviewed_text, qwen_text, tesseract_text
            ON lines BEGIN
                DELETE FROM lines_fts WHERE line_id = old.id;
                INSERT INTO lines_fts(line_id, page_id, volume, search_text)
                VALUES (
                    new.id,
                    COALESCE(new.page_id, ''),
                    COALESCE(new.volume, ''),
                    trim(
                        COALESCE(new.reviewed_text, '') || ' ' ||
                        COALESCE(new.qwen_text, '') || ' ' ||
                        COALESCE(new.tesseract_text, '')
                    )
                );
            END
            """
        )
    else:
        conn.execute(
            """
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
            END
            """
        )
    conn.commit()
    conn.close()


def ensure_runs_column(db: str) -> None:
    """Garante que a coluna `runs` exista na tabela `lines`. Se faltar, adiciona com valor default 0."""
    conn = connect_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()]
    if "runs" not in cols:
        # ALTER TABLE ADD COLUMN é seguro para SQLite — adiciona coluna com default 0
        conn.execute("ALTER TABLE lines ADD COLUMN runs INTEGER DEFAULT 0")
        conn.commit()
    conn.close()


def ensure_score_llm_column(db: str) -> None:
    """Garante a coluna `score_llm` na tabela `lines`."""
    conn = connect_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()]
    if "score_llm" not in cols:
        conn.execute("ALTER TABLE lines ADD COLUMN score_llm REAL")
        conn.commit()
    conn.close()


def increment_runs(
    db: str,
    line_id: int,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Incrementa o contador `runs` para a linha especificada."""
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        _run_db_with_retry(
            conn,
            lambda: (
                conn.execute(
                    "UPDATE lines SET runs = IFNULL(runs,0) + 1, updated_at = datetime('now') WHERE id = ?",
                    (line_id,),
                ),
                conn.commit(),
            ),
        )
    finally:
        if owns_conn:
            conn.close()


def mark_line_skipped(
    db: str,
    line_id: int,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Marca a linha como ignorada sem apagar textos ou scores existentes."""
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        def _mark() -> None:
            conn.execute(
                """
                UPDATE lines
                SET status = 'skipped', updated_at = datetime('now')
                WHERE id = ?
                """,
                (line_id,),
            )
            conn.commit()

        _run_db_with_retry(conn, _mark)
    finally:
        if owns_conn:
            conn.close()


@dataclass(frozen=True)
class BackendSpec:
    provider: str
    model: str
    weight: float = 1.0


def parse_backend_spec(spec: str) -> BackendSpec:
    if ":" not in spec:
        return BackendSpec("ollama", spec.strip())
    provider, model = spec.split(":", 1)
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in {"ollama", "openai"}:
        return BackendSpec("ollama", spec.strip())
    weight = 1.0
    if "@" in model:
        model_part, weight_part = model.rsplit("@", 1)
        try:
            weight = float(weight_part)
            model = model_part
        except ValueError:
            weight = 1.0
    return BackendSpec(provider, model, weight)


def parse_backend_identity(spec: str) -> BackendSpec:
    backend = parse_backend_spec(spec)
    return BackendSpec(backend.provider, backend.model, 1.0)


def normalize_for_consensus(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def consensus_from_versions(versions: list[dict], tesseract_text: str) -> tuple[str, float]:
    if not versions:
        return "", 0.0
    votes: dict[str, list[dict]] = {}
    for v in versions:
        key = normalize_for_consensus(v["text"])
        votes.setdefault(key, []).append(v)

    ranked = sorted(
        votes.items(),
        key=lambda item: (
            sum(vv.get("weight", 1.0) for vv in item[1]),
            sum((vv.get("weight", 1.0) * agreement_score(tesseract_text or "", vv["text"])) for vv in item[1])
            / max(sum(vv.get("weight", 1.0) for vv in item[1]), 1e-9),
            max(vv.get("score", 0.0) for vv in item[1]),
        ),
        reverse=True,
    )
    best_text, best_votes = ranked[0]
    best_weight = sum(vv.get("weight", 1.0) for vv in best_votes)
    weighted_agreement = sum(
        vv.get("weight", 1.0) * agreement_score(tesseract_text or "", vv["text"])
        for vv in best_votes
    ) / max(best_weight, 1e-9)
    # O score deve refletir a concordância real com o Tesseract.
    # `weight_share` é útil para desempate, mas não serve como score final
    # porque vira 1.0 quando há apenas um backend.
    return best_text, round(min(1.0, weighted_agreement), 4)


def llm_best_score(versions: list[dict], tesseract_text: str) -> float:
    if not versions:
        return 0.0
    return round(
        max(agreement_score(tesseract_text or "", v["text"]) for v in versions),
        4,
    )


def save_line_version(
    db: str,
    run_id: str,
    line_id: int,
    provider: str,
    model: str,
    text: str,
    source_score: float | None = None,
    meta_json: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    # O hash identifica o texto persistido; normalizar antes dele impede que
    # formas Unicode equivalentes sejam registradas como votos distintos.
    text = unicodedata.normalize("NFC", text or "")
    owns_conn = conn is None
    conn = conn or connect_db(db)
    try:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        def _save_version() -> None:
            exists = conn.execute(
                """
                SELECT 1 FROM line_versions
                WHERE run_id = ? AND line_id = ? AND provider = ? AND model = ? AND text_hash = ?
                LIMIT 1
                """,
                (run_id, line_id, provider, model, text_hash),
            ).fetchone()
            if exists:
                conn.commit()
                return
            conn.execute(
                """
                UPDATE line_versions
                SET is_current = 0, updated_at = datetime('now')
                WHERE line_id = ? AND provider = ? AND model = ? AND is_current = 1
                """,
                (line_id, provider, model),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO line_versions
                    (run_id, line_id, provider, model, text_content, text_hash, source_score, is_current, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (run_id, line_id, provider, model, text, text_hash, source_score, meta_json),
            )
            conn.commit()

        _run_db_with_retry(conn, _save_version)
    finally:
        if owns_conn:
            conn.close()


def build_tesseract_config(tessdata_dir: str | None = None) -> str:
    parts = ["--psm 6", "--oem 1"]
    if tessdata_dir:
        parts.append(f'--tessdata-dir "{tessdata_dir}"')
    return " ".join(parts)


def rerun_tesseract_lines(
    db: str,
    lang: str,
    tessdata_dir: str | None = None,
    limit: int | None = None,
    delay: float = 0.0,
    dry_run: bool = False,
    reference_mode: str = "auto",
    jobs: int = 1,
    empty_only: bool = False,
) -> None:
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract não está disponível no ambiente. Ative a .venv com a dependência instalada."
        )

    conn = connect_db(db)
    query = """
        SELECT id, line_image, tesseract_text, qwen_text, reviewed_text, agreement_score, status
        FROM lines
        WHERE IFNULL(agreement_score, 0) < 1.0
            AND line_image IS NOT NULL
            AND status NOT IN ('approved', 'corrected')
            AND qwen_text IS NOT NULL AND qwen_text <> ''
    """
    if empty_only:
        query += " AND TRIM(IFNULL(tesseract_text, '')) = ''"
    query += """
        ORDER BY IFNULL(agreement_score, 0) ASC, id ASC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("[INFO] Nenhuma linha elegível para rerun Tesseract.")
        return

    config = build_tesseract_config(tessdata_dir)
    print(
        f"[INFO] Rerun Tesseract em {len(rows)} linha(s) com lang={lang} ref={reference_mode} config={config}"
        + (" empty_only=True" if empty_only else "")
    )
    if dry_run:
        sample_ids = ", ".join(str(row["id"]) for row in rows[:20])
        print(f"[DRY-RUN] Nenhuma linha será alterada. IDs elegíveis: {sample_ids}")
        if len(rows) > 20:
            print(f"[DRY-RUN] ... e mais {len(rows) - 20} linha(s).")
        return

    job_args = [
        {
            "db": db,
            "run_id": f"tess-{uuid.uuid4().hex}",
            "line_id": row["id"],
            "line_image": row["line_image"],
            "tesseract_text": row["tesseract_text"],
            "qwen_text": row["qwen_text"],
            "reviewed_text": row["reviewed_text"],
            "agreement_score": row["agreement_score"],
            "status": row["status"],
            "lang": lang,
            "config": config,
            "delay": delay,
            "reference_mode": reference_mode,
            "tessdata_dir": tessdata_dir,
        }
        for row in rows
    ]

    if jobs == 1:
        for job in job_args:
            _rerun_tesseract_job(job)
        return

    with mp.Pool(processes=jobs) as pool:
        pool.map(_rerun_tesseract_job, job_args)


def _rerun_tesseract_job(job: dict) -> None:
    db = job["db"]
    line_id = job["line_id"]
    delay = job["delay"]
    reference_mode = job["reference_mode"]
    try:
        img = Image.open(io.BytesIO(job["line_image"]))
        text = pytesseract.image_to_string(img, lang=job["lang"], config=job["config"]).strip()
        row_like = {
            "qwen_text": job["qwen_text"],
            "reviewed_text": job["reviewed_text"],
        }
        ref_text = reference_text_for_row(row_like, reference_mode)
        score = agreement_score(ref_text, text) if ref_text else 0.0
        meta = {
            "rerun": "tesseract",
            "lang": job["lang"],
            "tessdata_dir": job["tessdata_dir"],
            "reference_mode": reference_mode,
            "reference_kind": "reviewed_text"
            if reference_mode == "reviewed"
            else ("qwen_text" if reference_mode == "consensus" else "reviewed_text_or_qwen_text"),
            "reference_score": job["agreement_score"],
        }
        save_line_version(
            db,
            job["run_id"] or "rerun-tesseract",
            line_id,
            "tesseract",
            job["lang"],
            text,
            source_score=score,
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
        save_result(
            db,
            line_id,
            text,
            score,
            score,
            "inferred",
            preserve_rejected=True,
            text_column="tesseract_text",
        )
        marker = "✓" if score >= 1.0 else ("△" if score >= 0.5 else "✗")
        print(
            f"[TESS] id={line_id} ref={reference_mode} score_before={job['agreement_score']:.2f} score={score:.2f} {marker} text={repr(text[:80])} ref_text={repr(ref_text[:80])}"
        )
    except Exception as e:
        print(f"[TESS] ERRO id={line_id}: {e}")
        save_result(db, line_id, "", 0.0, 0.0, "error", preserve_rejected=True)

    if delay > 0:
        time.sleep(delay)


def recalc_agreement_scores(
    db: str,
    reference_mode: str = "auto",
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    conn = connect_db(db)
    query = """
        SELECT id, qwen_text, reviewed_text, tesseract_text, agreement_score
        FROM lines
        WHERE IFNULL(qwen_text, '') <> ''
           OR IFNULL(reviewed_text, '') <> ''
        ORDER BY id ASC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    rows = conn.execute(query, params).fetchall()

    if not rows:
        conn.close()
        print("[INFO] Nenhuma linha elegível para recálculo de score.")
        return

    print(f"[INFO] Recálculo de score em {len(rows)} linha(s) ref={reference_mode}")
    if dry_run:
        sample_ids = ", ".join(str(row["id"]) for row in rows[:20])
        print(f"[DRY-RUN] Nenhuma linha será alterada. IDs elegíveis: {sample_ids}")
        if len(rows) > 20:
            print(f"[DRY-RUN] ... e mais {len(rows) - 20} linha(s).")
        conn.close()
        return

    updated = 0
    for row in rows:
        current_text = (row["qwen_text"] or "").strip()
        if reference_mode == "auto":
            ref_text = (row["tesseract_text"] or "").strip()
        else:
            ref_text = reference_text_for_row(row, reference_mode)
        if not current_text or not ref_text:
            continue
        score = agreement_score(ref_text, current_text)
        conn.execute(
            "UPDATE lines SET agreement_score=?, updated_at=datetime('now') WHERE id=?",
            (score, row["id"]),
        )
        updated += 1

    conn.commit()
    conn.close()
    print(f"[DONE] agreement_score atualizado em {updated} linha(s)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_orig_connect = _uc.HTTPConnection.connect


def make_session(prefix:str = "https://") -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=0))

    def _connect_with_keepalive(self):
        _orig_connect(self)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)

    _uc.HTTPConnection.connect = _connect_with_keepalive
    session.mount(prefix, adapter)
    return session


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _compact_error_body(raw: bytes, charset: str = "utf-8") -> str:
    text = raw.decode(charset, errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r'(?i)(authorization["\']?\s*[:=]\s*["\']?)(?:bearer\s+)?[^\s"\',}]+',
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r'(?i)((?:api[-_]?key|token)["\']?\s*[:=]\s*["\']?)[^\s"\',}]+',
        r"\1<redacted>",
        text,
    )
    if len(text) > MAX_ERROR_BODY_CHARS:
        return f"{text[:MAX_ERROR_BODY_CHARS]}…[truncated]"
    return text


def _interesting_http_headers(headers) -> dict[str, str]:
    if not headers:
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).lower().startswith("x-ratelimit")
        or str(key).lower() in {"retry-after", "x-request-id"}
    }


def _http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def _http_headers(exc: Exception):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.headers
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.headers
    return None


def _retry_after_seconds(exc: Exception, now: float | None = None) -> float | None:
    headers = _http_headers(exc)
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None

    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        pass

    try:
        retry_at = email.utils.parsedate_to_datetime(str(value))
        seconds = retry_at.timestamp() - (time.time() if now is None else now)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _call_with_429_backoff(
    operation,
    *,
    retries: int = DEFAULT_RATE_LIMIT_RETRIES,
    base_delay: float = DEFAULT_RATE_LIMIT_BACKOFF_BASE,
    max_delay: float = DEFAULT_RATE_LIMIT_BACKOFF_MAX,
    on_retry=None,
):
    """Repete somente HTTP 429, com backoff exponencial e equal jitter."""
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as exc:
            if _http_status_code(exc) != 429 or attempt >= retries:
                raise

            exponential_cap = min(max_delay, base_delay * (2**attempt))
            jittered_delay = random.uniform(exponential_cap / 2, exponential_cap)
            retry_after = _retry_after_seconds(exc)
            sleep_seconds = max(jittered_delay, retry_after or 0.0)
            if on_retry is not None:
                on_retry(attempt + 1, retries, sleep_seconds)
            time.sleep(sleep_seconds)


def _call_with_api_slot(operation, semaphore=None, on_wait=None):
    """Executa uma chamada ocupando no máximo um slot global de API."""
    if semaphore is None:
        return operation()

    acquired = semaphore.acquire(False)
    if not acquired:
        if on_wait is not None:
            on_wait()
        semaphore.acquire()
    try:
        return operation()
    finally:
        semaphore.release()


def format_inference_error(
    exc: Exception,
    *,
    stage: str,
    backend: BackendSpec | None,
    total_elapsed: float,
    api_elapsed: float | None,
    image_source: str,
    image_bytes: bytes | None,
    prompt_chars: int,
) -> str:
    """Formata falhas do worker em uma única linha, incluindo a resposta HTTP."""
    fields = [
        f"stage={stage}",
        f"type={type(exc).__name__}",
    ]
    if backend is not None:
        fields.append(f"backend={backend.provider}:{backend.model}")
    if api_elapsed is not None:
        fields.append(f"api={api_elapsed:.2f}s")
    fields.extend(
        [
            f"total={total_elapsed:.2f}s",
            f"src={image_source}",
            f"image_bytes={len(image_bytes) if image_bytes is not None else 0}",
            f"prompt_chars={prompt_chars}",
        ]
    )

    status = None
    reason = None
    url = None
    body = ""
    headers = {}

    try:
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
            reason = exc.reason
            url = _safe_url(exc.geturl())
            headers = _interesting_http_headers(exc.headers)
            charset = (
                exc.headers.get_content_charset()
                if hasattr(exc.headers, "get_content_charset")
                else None
            )
            raw_body = exc.read(MAX_ERROR_BODY_CHARS * 4 + 1)
            body = _compact_error_body(raw_body, charset or "utf-8")
        elif isinstance(exc, requests.HTTPError) and exc.response is not None:
            response = exc.response
            status = response.status_code
            reason = response.reason
            url = _safe_url(response.url)
            headers = _interesting_http_headers(response.headers)
            body = _compact_error_body(
                response.content[: MAX_ERROR_BODY_CHARS * 4 + 1],
                response.encoding or "utf-8",
            )
    except Exception as detail_exc:
        fields.append(f"http_detail_error={str(detail_exc)!r}")

    if status is not None:
        fields.append(f"http_status={status}")
    if reason:
        fields.append(f"reason={reason!r}")
    if url:
        fields.append(f"url={url!r}")
    if body:
        fields.append(f"body={body!r}")
    if headers:
        fields.append(f"headers={headers!r}")
    if status is None:
        fields.append(f"message={str(exc)!r}")

    return " ".join(fields)


def levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def unicode_block_diff(a: str, b: str) -> int:
    """Conta caracteres que diferem dentro do bloco politônico grego."""
    a = unicodedata.normalize("NFC", a)
    b = unicodedata.normalize("NFC", b)
    diffs = sum(
        1 for ca, cb in zip(a, b) if ca != cb and (ord(ca) > 0x1F00 or ord(cb) > 0x1F00)
    )
    diffs += abs(len(a) - len(b))  # diferença de comprimento
    return diffs


def has_diacritic_diff(a: str, b: str) -> bool:
    """Detecta diferenças em caracteres com diacríticos mesmo com score alto."""

    def normalize(s):
        # NFC garante representação consistente de compostos Unicode
        return unicodedata.normalize("NFC", s)

    a, b = normalize(a), normalize(b)
    if a == b:
        return False

    # Compara caractere a caractere procurando diferenças em codepoints altos
    for ca, cb in zip(a, b):
        if ca != cb:
            # Diferença em bloco grego politônico (U+1F00–U+1FFF)
            if ord(ca) > 0x1F00 or ord(cb) > 0x1F00:
                return True
    return False


def agreement_score(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    a, b = a.strip(), b.strip()
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return round(1.0 - levenshtein(a, b) / max_len, 4)


def encoded_image_dimensions(image_bytes: bytes | None) -> tuple[int, int]:
    """Lê largura e altura da imagem codificada sem decodificar todos os pixels."""
    if not image_bytes:
        raise ValueError("imagem vazia")
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.size


def image_is_too_small(width: int, height: int, min_side: int) -> bool:
    return width < min_side or height < min_side


def parse_page_num_from_filename(image_path: Path) -> Optional[int]:
    """
    Extrai o sufixo numérico final da imagem, ex.: foo-076.png -> 76.
    """
    m = re.search(r"-([0-9]{1,4})$", image_path.stem)
    return int(m.group(1)) if m else None


def infer_volume_id(image_path: Path) -> Optional[str]:
    """
    Considera a convenção teste/<VOL>/images/<file>.png → retorna <VOL>.
    """
    try:
        return image_path.parent.parent.name
    except Exception:
        return None


def txt_path_for_image(img_path: Path, txt_dir: Path) -> Path:
    """
    Seleciona o txt associado a uma imagem:
    - Usa o nome estável se existir.
    - Caso contrário, procura qualquer txt que termine com o número da página.
    - Fallback: path estável mesmo que ainda não exista (para escrita).
    """
    stable = txt_dir / (img_path.stem + ".txt")
    if stable.exists():
        return stable

    page_num = parse_page_num_from_filename(img_path)
    if page_num is not None:
        # prioriza zero-padding, depois sem padding
        candidates = sorted(txt_dir.glob(f"*-{page_num:03d}.txt"))
        if candidates:
            return candidates[0]
        candidates = sorted(txt_dir.glob(f"*-{page_num}.txt"))
        if candidates:
            return candidates[0]

    return stable


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
from requests.adapters import HTTPAdapter

session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)

def call_ollama_vision(
    image_bytes: bytes,
    model: str,
    base_url: str,
    user_prompt: str = USER_PROMPT,
) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt, "images": [b64]},
        ],
        "stream": False,
        "options": {"top_p": DEFAULT_TOP_P, "temperature": DEFAULT_TEMP},
    }

    # data = json.dumps(payload).encode("utf-8")
    # req = urllib.request.Request(
    #     base_url.rstrip("/") + "/api/chat",
    #     data=data,
    #     headers={"Content-Type": "application/json"},
    #     method="POST",
    # )
    # with urllib.request.urlopen(req, timeout=300) as resp:
    #     body = json.loads(resp.read().decode("utf-8"))

    response = session.post(base_url.rstrip("/") + "/api/chat", json=payload)
    body = response.json()

    return body.get("message", {}).get("content", "").strip()

def openai_process_image(
    image_bytes: bytes,
    model: str = DEFAULT_OPENAI_MODEL,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    api_key: str | None = None,
    mime: str = "image/png",
    reprocess: bool = False,
    temperature: float = DEFAULT_TEMP,
    top_p: float = DEFAULT_TOP_P,
    user_prompt: str = USER_PROMPT,
) -> str:
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não encontrado no ambiente.")

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_url = {"url": f"data:{mime};base64,{img_b64}"}
    if "gpt-5" in model:
        image_url["detail"] = "high"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": image_url},
            ],
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }
    timeout = 120

    if "gpt-5" not in model:
        payload["temperature"] = temperature
        payload["top_p"] = top_p
    else:
        payload["reasoning_effort"] = "medium" if reprocess else "low"
        # payload["service_tier"] = "flex"
        if reprocess:
            timeout = 1200

    with make_session() as session:
        r = session.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            data=json.dumps(payload),
            timeout=timeout,
        )

    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def worker(args: dict) -> None:
    """
    Processo filho: fica pegando lotes do banco até não haver mais pending.
    Cada worker tem sua própria conexão — sem estado compartilhado.
    """
    db = args["db"]
    base_url = args["base_url"]
    openai_base_url = args["openai_base_url"]
    openai_api_key = args["openai_api_key"]
    backends: list[BackendSpec] = args["backends"]
    delay = args["delay"]
    batch_size = args["batch_size"]
    worker_id = args["worker_id"]
    run_id = args["run_id"]
    should_append_extra_context = args.get("should_append_extra_context", False)
    prefer_missing_backend = args.get("prefer_missing_backend")
    min_image_side = args.get("min_image_side", DEFAULT_MIN_IMAGE_SIDE)
    rate_limit_retries = args.get(
        "rate_limit_retries",
        DEFAULT_RATE_LIMIT_RETRIES,
    )
    rate_limit_backoff_base = args.get(
        "rate_limit_backoff_base",
        DEFAULT_RATE_LIMIT_BACKOFF_BASE,
    )
    rate_limit_backoff_max = args.get(
        "rate_limit_backoff_max",
        DEFAULT_RATE_LIMIT_BACKOFF_MAX,
    )
    api_semaphore = args.get("api_semaphore")

    prefix = f"[W{worker_id}]"
    processed = 0
    conn = connect_db(db)

    def crop_from_preprocessed(img: np.ndarray, bbox_json: str) -> bytes:
        """Crop bbox from preprocessed (rotated) image and return PNG bytes."""
        try:
            bbox = json.loads(bbox_json) if isinstance(bbox_json, str) else bbox_json
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            w = int(bbox.get("w", 0))
            h = int(bbox.get("h", 0))
        except Exception:
            raise

        x1 = max(0, x - CROP_PADDING)
        y1 = max(0, y - CROP_PADDING)
        x2 = min(img.shape[1], x + w + CROP_PADDING)
        y2 = min(img.shape[0], y + h + CROP_PADDING)
        crop = img[y1:y2, x1:x2]
        buf = io.BytesIO()
        if len(crop.shape) == 3:
            pil_img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        else:
            pil_img = Image.fromarray(crop)
        pil_img.save(buf, format="PNG")
        return buf.getvalue()

    try:
        while True:
            claim_started_at = time.perf_counter()
            batch = claim_batch(
                db,
                batch_size,
                prefer_missing_backend=prefer_missing_backend,
                min_image_side=min_image_side,
                conn=conn,
            )
            claim_elapsed = time.perf_counter() - claim_started_at
            if not batch:
                print(
                    f"{prefix} claim={claim_elapsed:.2f}s rows=0 "
                    f"Sem mais linhas. Encerrando ({processed} processadas)."
                )
                break
            print(f"{prefix} claim={claim_elapsed:.2f}s rows={len(batch)}")

            for line_id, image_path, bbox, line_image, tesseract_text in batch:
                line_started_at = time.perf_counter()
                prep_elapsed = 0.0
                db_elapsed = 0.0
                image_source = "line_image"
                stage = "prepare"
                current_backend: BackendSpec | None = None
                api_started_at: float | None = None
                to_send: bytes | None = None
                user_prompt = USER_PROMPT
                try:
                    context: str|None = None

                    image_path = Path(image_path) if image_path else None
                    prep_started_at = time.perf_counter()

                    if image_path and should_append_extra_context:
                        volume_path = image_path.parent.parent
                        txt_path = txt_path_for_image(image_path, volume_path / "text")

                        print(txt_path)

                        if txt_path.exists():
                            with open(txt_path, "r", encoding="utf-8") as f:
                                context = f.read()

                    # Prefer crop from original preprocessed image; fallback to stored line_image bytes
                    cropped_bytes = None
                    try:
                        if image_path:
                            img = cv2.imread(str(image_path))
                            if img is not None:
                                _, img_proc = preprocess_image(img)
                                try:
                                    cropped_bytes = crop_from_preprocessed(img_proc, bbox)
                                except Exception:
                                    cropped_bytes = None
                    except Exception as e:
                        print(
                            f"{prefix} warning: unable to crop from original image id={line_id}: {e}"
                        )

                    to_send = cropped_bytes if cropped_bytes is not None else line_image
                    image_source = "crop" if cropped_bytes is not None else "line_image"
                    stage = "validate_image"
                    image_width, image_height = encoded_image_dimensions(to_send)
                    if image_is_too_small(
                        image_width,
                        image_height,
                        min_image_side,
                    ):
                        db_started_at = time.perf_counter()
                        mark_line_skipped(db, line_id, conn=conn)
                        db_elapsed += time.perf_counter() - db_started_at
                        prep_elapsed = time.perf_counter() - prep_started_at
                        total_elapsed = time.perf_counter() - line_started_at
                        print(
                            f"{prefix} IGNORADO id={line_id} reason=image_too_small "
                            f"image={image_width}x{image_height} "
                            f"min_side={min_image_side} src={image_source} "
                            f"prep={prep_elapsed:.2f}s db={db_elapsed:.2f}s "
                            f"total={total_elapsed:.2f}s",
                            flush=True,
                        )
                        processed += 1
                        continue

                    user_prompt = USER_PROMPT

                    if context and should_append_extra_context:
                        context = unicodedata.normalize("NFC", context)
                        user_prompt = f"PAGE CONTEXT: <context>\n{context}\n</context>\nTranscribe only the image:"
                    prep_elapsed = time.perf_counter() - prep_started_at

                    # Só conta como tentativa quando a imagem chega à inferência.
                    try:
                        increment_runs(db, line_id, conn=conn)
                    except Exception:
                        pass

                    versions: list[dict] = []
                    backend_timings: list[str] = []
                    for backend in backends:
                        current_backend = backend
                        backend_weight = backend.weight
                        stage = "api"
                        api_started_at = time.perf_counter()

                        def call_backend() -> str:
                            if backend.provider == "ollama":
                                return call_ollama_vision(
                                    to_send,
                                    backend.model,
                                    base_url,
                                    user_prompt=user_prompt,
                                )
                            if backend.provider == "openai":
                                return openai_process_image(
                                    image_bytes=to_send,
                                    model=backend.model,
                                    base_url=openai_base_url,
                                    api_key=openai_api_key,
                                    user_prompt=user_prompt,
                                )
                            raise ValueError(f"backend desconhecido: {backend.provider}")

                        def log_rate_limit_retry(
                            retry_number: int,
                            retry_total: int,
                            sleep_seconds: float,
                        ) -> None:
                            print(
                                f"{prefix} 429 id={line_id} "
                                f"backend={backend.provider}:{backend.model} "
                                f"retry={retry_number}/{retry_total} "
                                f"backoff={sleep_seconds:.2f}s",
                                flush=True,
                            )

                        def call_backend_with_slot() -> str:
                            return _call_with_api_slot(
                                call_backend,
                                semaphore=api_semaphore,
                                on_wait=lambda: print(
                                    f"{prefix} AGUARDANDO id={line_id} "
                                    f"backend={backend.provider}:{backend.model} "
                                    "reason=api_concurrency",
                                    flush=True,
                                ),
                            )

                        raw = _call_with_429_backoff(
                            call_backend_with_slot,
                            retries=rate_limit_retries,
                            base_delay=rate_limit_backoff_base,
                            max_delay=rate_limit_backoff_max,
                            on_retry=log_rate_limit_retry,
                        )
                        backend_elapsed = time.perf_counter() - api_started_at
                        api_started_at = None
                        stage = "normalize_response"
                        text = strip_think(raw)
                        text = unicodedata.normalize("NFC", text)
                        versions.append(
                            {
                                "provider": backend.provider,
                                "model": backend.model,
                                "text": text,
                                "score": agreement_score(tesseract_text or "", text),
                                "weight": backend_weight,
                            }
                        )
                        backend_timings.append(
                            f"{backend.provider}:{backend.model}={backend_elapsed:.2f}s"
                        )
                        stage = "save_version"
                        db_started_at = time.perf_counter()
                        save_line_version(
                            db,
                            run_id,
                            line_id,
                            backend.provider,
                            backend.model,
                            text,
                            source_score=versions[-1]["score"],
                            meta_json=json.dumps({"user_prompt": user_prompt[:200]}),
                            conn=conn,
                        )
                        db_elapsed += time.perf_counter() - db_started_at
                    current_backend = None
                    stage = "consensus"
                    qwen_text, score = consensus_from_versions(versions, tesseract_text or "")
                    score_llm = llm_best_score(versions, tesseract_text or "")
                    stage = "save_result"
                    db_started_at = time.perf_counter()
                    save_result(
                        db,
                        line_id,
                        qwen_text,
                        score,
                        score_llm,
                        "inferred",
                        conn=conn,
                    )
                    db_elapsed += time.perf_counter() - db_started_at
                    marker = "✓" if score >= 0.8 else ("△" if score >= 0.5 else "✗")
                    total_elapsed = time.perf_counter() - line_started_at
                    print(
                        f"{prefix} id={line_id} score={score:.2f} score_llm={score_llm:.2f} {marker}  "
                        f"src={image_source} prep={prep_elapsed:.2f}s db={db_elapsed:.2f}s total={total_elapsed:.2f}s  "
                        f"api={', '.join(backend_timings)}  "
                        f"tess={repr((tesseract_text or '')[:64])}  consensus={repr(qwen_text[:64])}"
                    )
                except Exception as e:
                    now = time.perf_counter()
                    api_elapsed = (
                        now - api_started_at
                        if api_started_at is not None and stage == "api"
                        else None
                    )
                    details = format_inference_error(
                        e,
                        stage=stage,
                        backend=current_backend,
                        total_elapsed=now - line_started_at,
                        api_elapsed=api_elapsed,
                        image_source=image_source,
                        image_bytes=to_send,
                        prompt_chars=len(user_prompt),
                    )
                    print(f"{prefix} ERRO id={line_id} {details}", flush=True)
                    try:
                        save_result(db, line_id, "", 0.0, 0.0, "error", conn=conn)
                    except Exception as save_exc:
                        print(f"{prefix} ERRO ao salvar status=error id={line_id}: {save_exc}")

                processed += 1
                if delay > 0:
                    time.sleep(delay)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Inferência batch via Ollama/OpenAI")
    parser.add_argument("--db", default="ocr.db")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--openai-base-url", default=DEFAULT_OPENAI_BASE_URL)
    parser.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--jobs", type=int, default=1, help="Workers paralelos")
    parser.add_argument(
        "--api-concurrency",
        type=int,
        default=DEFAULT_API_CONCURRENCY,
        help=(
            "Máximo global de chamadas API simultâneas entre os workers "
            "(default: mesmo valor de --jobs)"
        ),
    )
    parser.add_argument(
        "--rate-limit-retries",
        type=int,
        default=DEFAULT_RATE_LIMIT_RETRIES,
        help="Retries adicionais para respostas HTTP 429",
    )
    parser.add_argument(
        "--rate-limit-backoff-base",
        type=float,
        default=DEFAULT_RATE_LIMIT_BACKOFF_BASE,
        help="Espera inicial em segundos para HTTP 429",
    )
    parser.add_argument(
        "--rate-limit-backoff-max",
        type=float,
        default=DEFAULT_RATE_LIMIT_BACKOFF_MAX,
        help="Teto do backoff exponencial em segundos para HTTP 429",
    )
    parser.add_argument(
        "--rerun-tesseract",
        action="store_true",
        help="Reprocessa apenas linhas com agreement_score < 1.0 usando Tesseract.",
    )
    parser.add_argument(
        "--rerun-tesseract-empty",
        action="store_true",
        help="Reprocessa apenas linhas com tesseract_text vazio usando Tesseract.",
    )
    parser.add_argument(
        "--recalc-agreement-score",
        action="store_true",
        help="Atualiza agreement_score no DB sem rerodar OCR.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria reprocessado sem gravar mudanças.",
    )
    parser.add_argument(
        "--tesseract-lang",
        default="lat",
        help="Idioma/traineddata para o rerun Tesseract (ex.: lat, grc, lat+grc).",
    )
    parser.add_argument(
        "--tessdata-dir",
        default=None,
        help="Diretório opcional com os traineddata do Tesseract.",
    )
    parser.add_argument(
        "--tesseract-reference",
        default="auto",
        choices=["auto", "reviewed", "consensus"],
        help="Referência usada no score do rerun Tesseract: auto, reviewed ou consensus.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Linhas por lote por worker",
    )
    parser.add_argument(
        "--min-image-side",
        type=int,
        default=DEFAULT_MIN_IMAGE_SIDE,
        help=(
            "Ignora imagens cuja largura ou altura seja menor que este valor "
            f"(default: {DEFAULT_MIN_IMAGE_SIDE})"
        ),
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        help="Backend no formato provider:model[@peso]. Pode repetir.",
    )
    parser.add_argument(
        "--reprocess-below",
        type=float,
        default=None,
        help=(
            "Marca como pending entradas cujo agreement_score < VAL (0.0-1.0) "
            "antes de iniciar os workers, para que sejam reprocessadas"
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificador opcional da execução para registrar versões em line_versions.",
    )
    parser.add_argument(
        "--prefer-missing-backend",
        default=None,
        help=(
            "Prioriza linhas pending que ainda não possuem versão em "
            "line_versions para o backend exato provider:model informado."
        ),
    )
    args = parser.parse_args()
    args.min_image_side = getattr(args, "min_image_side", DEFAULT_MIN_IMAGE_SIDE)
    args.rate_limit_retries = getattr(
        args,
        "rate_limit_retries",
        DEFAULT_RATE_LIMIT_RETRIES,
    )
    args.rate_limit_backoff_base = getattr(
        args,
        "rate_limit_backoff_base",
        DEFAULT_RATE_LIMIT_BACKOFF_BASE,
    )
    args.rate_limit_backoff_max = getattr(
        args,
        "rate_limit_backoff_max",
        DEFAULT_RATE_LIMIT_BACKOFF_MAX,
    )
    args.api_concurrency = getattr(
        args,
        "api_concurrency",
        DEFAULT_API_CONCURRENCY,
    )
    if args.min_image_side < 1:
        parser.error("--min-image-side deve ser maior que zero")
    if args.api_concurrency is not None and args.api_concurrency < 1:
        parser.error("--api-concurrency deve ser maior que zero")
    if args.rate_limit_retries < 0:
        parser.error("--rate-limit-retries não pode ser negativo")
    if args.rate_limit_backoff_base < 0:
        parser.error("--rate-limit-backoff-base não pode ser negativo")
    if args.rate_limit_backoff_max < args.rate_limit_backoff_base:
        parser.error(
            "--rate-limit-backoff-max deve ser maior ou igual a "
            "--rate-limit-backoff-base"
        )
    run_id = args.run_id or f"run-{uuid.uuid4().hex}"

    # Garantir que a coluna `runs` exista antes de tocar no banco
    ensure_runs_column(args.db)
    ensure_score_llm_column(args.db)
    ensure_versions_table(args.db)
    ensure_claim_indexes(args.db)
    ensure_search_update_trigger(args.db)

    if args.rerun_tesseract:
        rerun_tesseract_lines(
            args.db,
            lang=args.tesseract_lang,
            tessdata_dir=args.tessdata_dir,
            limit=args.limit,
            delay=args.delay,
            dry_run=args.dry_run,
            reference_mode=args.tesseract_reference,
            jobs=args.jobs,
        )
        return

    if args.rerun_tesseract_empty:
        rerun_tesseract_lines(
            args.db,
            lang=args.tesseract_lang,
            tessdata_dir=args.tessdata_dir,
            limit=args.limit,
            delay=args.delay,
            dry_run=args.dry_run,
            reference_mode=args.tesseract_reference,
            jobs=args.jobs,
            empty_only=True,
        )
        return

    if args.recalc_agreement_score:
        recalc_agreement_scores(
            args.db,
            reference_mode=args.tesseract_reference,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        return

    conn = connect_db(args.db)
    recovered = conn.execute(
        "UPDATE lines SET status='pending', updated_at=datetime('now') WHERE status='processing'"
    ).rowcount
    conn.commit()
    conn.close()
    if recovered:
        print(f"[INFO] {recovered} linhas recuperadas de processing para pending")

    initial_prefer_missing_backend = (
        parse_backend_identity(args.prefer_missing_backend)
        if args.prefer_missing_backend
        else None
    )
    initial_claimable = count_claimable_lines(
        args.db,
        prefer_missing_backend=initial_prefer_missing_backend,
        min_image_side=args.min_image_side,
    )
    if initial_claimable > 0:
        _run_worker_pass(
            args=args,
            run_id=run_id,
            pass_label="pendentes",
            limit=args.limit,
        )
    else:
        print("[INFO] Nenhuma linha elegivel na primeira passagem.")

    if args.reprocess_below is not None:
        if not (0.0 <= args.reprocess_below <= 1.0):
            print("[ERROR] --reprocess-below deve estar entre 0.0 e 1.0")
            return
        conn = connect_db(args.db)
        to_requeue = conn.execute(
            "SELECT COUNT(*) FROM lines WHERE (status='inferred' OR status='error') AND IFNULL(agreement_score,0) < ?",
            (args.reprocess_below,),
        ).fetchone()[0]
        if to_requeue:
            conn.execute(
                "UPDATE lines SET status='pending', updated_at=datetime('now') WHERE (status='inferred' OR status='error') AND IFNULL(agreement_score,0) < ?",
                (args.reprocess_below,),
            )
            conn.commit()
        conn.close()
        print(
            f"[INFO] {to_requeue} linhas com agreement_score < {args.reprocess_below} marcadas como pending para reprocessamento"
        )
        if to_requeue > 0:
            _run_worker_pass(
                args=args,
                run_id=run_id,
                pass_label="reprocesso",
                limit=args.limit,
            )

    conn = connect_db(args.db)
    done = conn.execute(
        "SELECT COUNT(*) FROM lines WHERE status='inferred'"
    ).fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM lines WHERE status='error'").fetchone()[
        0
    ]
    skipped = conn.execute(
        "SELECT COUNT(*) FROM lines WHERE status='skipped'"
    ).fetchone()[0]
    conn.close()
    print(f"\n[DONE] inferred={done}  errors={errors}  skipped={skipped}")


def _run_worker_pass(args: argparse.Namespace, run_id: str, pass_label: str, limit: int | None) -> None:
    prefer_missing_backend = (
        parse_backend_identity(args.prefer_missing_backend)
        if args.prefer_missing_backend
        else None
    )
    claimable = count_claimable_lines(
        args.db,
        prefer_missing_backend=prefer_missing_backend,
        min_image_side=getattr(
            args,
            "min_image_side",
            DEFAULT_MIN_IMAGE_SIDE,
        ),
    )
    if claimable == 0:
        print(f"[INFO] Nenhuma linha pendente para {pass_label}.")
        return

    effective = min(claimable, limit) if limit else claimable
    configured_api_concurrency = getattr(
        args,
        "api_concurrency",
        DEFAULT_API_CONCURRENCY,
    )
    api_concurrency = min(args.jobs, configured_api_concurrency or args.jobs)
    print(
        f"[INFO] {effective} linhas para {pass_label} com {args.jobs} worker(s), "
        f"api_concurrency={api_concurrency}"
    )

    worker_args = [
        {
            "db": args.db,
            "base_url": args.base_url,
            "openai_base_url": args.openai_base_url,
            "openai_api_key": args.openai_api_key,
            "backends": [parse_backend_spec(x) for x in (args.backend or [f"ollama:{args.model}"])],
            "delay": args.delay,
            "rate_limit_retries": getattr(
                args,
                "rate_limit_retries",
                DEFAULT_RATE_LIMIT_RETRIES,
            ),
            "rate_limit_backoff_base": getattr(
                args,
                "rate_limit_backoff_base",
                DEFAULT_RATE_LIMIT_BACKOFF_BASE,
            ),
            "rate_limit_backoff_max": getattr(
                args,
                "rate_limit_backoff_max",
                DEFAULT_RATE_LIMIT_BACKOFF_MAX,
            ),
            "batch_size": args.batch_size,
            "min_image_side": getattr(
                args,
                "min_image_side",
                DEFAULT_MIN_IMAGE_SIDE,
            ),
            "worker_id": i,
            "run_id": run_id,
            "prefer_missing_backend": prefer_missing_backend,
        }
        for i in range(args.jobs)
    ]

    if args.jobs == 1:
        worker(worker_args[0])
    elif api_concurrency == args.jobs:
        with mp.Pool(processes=args.jobs) as pool:
            pool.map(worker, worker_args)
    else:
        with mp.Manager() as manager:
            api_semaphore = manager.BoundedSemaphore(api_concurrency)
            for current_worker_args in worker_args:
                current_worker_args["api_semaphore"] = api_semaphore
            with mp.Pool(processes=args.jobs) as pool:
                pool.map(worker, worker_args)


if __name__ == "__main__":
    main()
