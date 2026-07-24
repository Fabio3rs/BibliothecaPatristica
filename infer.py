#!/usr/bin/env python3
"""
infer.py — inferência via Ollama/OpenAI em batch, atualiza SQLite
Suporta paralelismo via --jobs (workers independentes, lotes atômicos via WAL)
"""

import argparse
import base64
import json
import os
import re
import socket
import sqlite3
import time
import uuid
from typing import Optional
import unicodedata
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
            order_prefix = ""
            params: list[object] = []
            where_clause = "WHERE status = 'pending'"
            if prefer_missing_backend is not None:
                order_prefix = """
                    CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM line_versions lv
                            WHERE lv.line_id = lines.id
                              AND lv.provider = ?
                              AND lv.model = ?
                        ) THEN 0
                        ELSE 1
                    END ASC,
                """
                params.extend(
                    [prefer_missing_backend.provider, prefer_missing_backend.model]
                )
                where_clause = """
                    WHERE status = 'pending'
                       OR (
                            status IN ('inferred', 'error')
                            AND NOT EXISTS (
                                SELECT 1
                                FROM line_versions lv
                                WHERE lv.line_id = lines.id
                                  AND lv.provider = ?
                                  AND lv.model = ?
                            )
                       )
                """
                params.extend(
                    [prefer_missing_backend.provider, prefer_missing_backend.model]
                )

            params.append(batch_size)
            ids = [
                r[0]
                for r in conn.execute(
                    f"""
                    SELECT id
                    FROM lines
                    {where_clause}
                    ORDER BY {order_prefix}
                             IFNULL(agreement_score, 0) ASC,
                             id ASC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            ]
            if not ids:
                conn.commit()
                return []
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"""
                UPDATE lines
                SET status = 'processing', updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                ids,
            )
            rows = conn.execute(
                f"""
                SELECT id, image_path, bbox, line_image, tesseract_text
                FROM lines
                WHERE id IN ({placeholders})
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
    parts = ["--psm 13", "--oem 1"]
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


def make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=0))

    def _connect_with_keepalive(self):
        _orig_connect(self)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)

    _uc.HTTPConnection.connect = _connect_with_keepalive
    session.mount("https://", adapter)
    return session


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


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
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=130) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "").strip()

def log_openai_error(response: requests.Response) -> None:
    try:
        body = response.json()
    except ValueError:
        body = response.text

    interesting_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower().startswith("x-ratelimit")
        or key.lower() in {"retry-after", "x-request-id"}
    }

    print(
        f"[HTTP {response.status_code}] "
        f"body={body!r} headers={interesting_headers!r}"
    )


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

    if r.status_code != 200:
        log_openai_error(r)

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

        x1 = max(0, x - 2)
        y1 = max(0, y - 2)
        x2 = min(img.shape[1], x + w + 2)
        y2 = min(img.shape[0], y + h + 2)
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
            batch = claim_batch(
                db,
                batch_size,
                prefer_missing_backend=prefer_missing_backend,
                conn=conn,
            )
            if not batch:
                print(f"{prefix} Sem mais linhas. Encerrando ({processed} processadas).")
                break

            for line_id, image_path, bbox, line_image, tesseract_text in batch:
                try:
                    # incrementa contador de tentativas antes de cada passagem pela LLM
                    try:
                        increment_runs(db, line_id, conn=conn)
                    except Exception:
                        # não deve bloquear o processamento se increment falhar
                        pass

                    context: str|None = None

                    image_path = Path(image_path) if image_path else None

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

                    user_prompt = USER_PROMPT

                    if context and should_append_extra_context:
                        context = unicodedata.normalize("NFC", context)
                        user_prompt = f"PAGE CONTEXT: <context>\n{context}\n</context>\nTranscribe only the image:"

                    versions: list[dict] = []
                    for backend in backends:
                        backend_weight = backend.weight
                        if backend.provider == "ollama":
                            raw = call_ollama_vision(
                                to_send, backend.model, base_url, user_prompt=user_prompt
                            )
                        elif backend.provider == "openai":
                            raw = openai_process_image(
                                image_bytes=to_send,
                                model=backend.model,
                                base_url=openai_base_url,
                                api_key=openai_api_key,
                                user_prompt=user_prompt,
                            )
                        else:
                            raise ValueError(f"backend desconhecido: {backend.provider}")
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
                    qwen_text, score = consensus_from_versions(versions, tesseract_text or "")
                    score_llm = llm_best_score(versions, tesseract_text or "")
                    save_result(
                        db,
                        line_id,
                        qwen_text,
                        score,
                        score_llm,
                        "inferred",
                        conn=conn,
                    )
                    marker = "✓" if score >= 0.8 else ("△" if score >= 0.5 else "✗")
                    print(
                        f"{prefix} id={line_id} score={score:.2f} score_llm={score_llm:.2f} {marker}  "
                        f"tess={repr((tesseract_text or '')[:64])}  consensus={repr(qwen_text[:64])}"
                    )
                except Exception as e:
                    print(f"{prefix} ERRO id={line_id}: {e}")
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
    run_id = args.run_id or f"run-{uuid.uuid4().hex}"

    # Garantir que a coluna `runs` exista antes de tocar no banco
    ensure_runs_column(args.db)
    ensure_score_llm_column(args.db)
    ensure_versions_table(args.db)

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
    pending = conn.execute("SELECT COUNT(*) FROM lines WHERE status='pending'").fetchone()[0]
    conn.close()
    if recovered:
        print(f"[INFO] {recovered} linhas recuperadas de processing para pending")

    if pending > 0:
        _run_worker_pass(
            args=args,
            run_id=run_id,
            pass_label="pendentes",
            limit=args.limit,
        )
    else:
        print("[INFO] Nenhuma linha pendente na primeira passagem.")

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
    conn.close()
    print(f"\n[DONE] inferred={done}  errors={errors}")


def _run_worker_pass(args: argparse.Namespace, run_id: str, pass_label: str, limit: int | None) -> None:
    conn = connect_db(args.db)
    pending = conn.execute("SELECT COUNT(*) FROM lines WHERE status='pending'").fetchone()[0]
    conn.close()
    if pending == 0:
        print(f"[INFO] Nenhuma linha pendente para {pass_label}.")
        return

    effective = min(pending, limit) if limit else pending
    print(f"[INFO] {effective} linhas para {pass_label} com {args.jobs} worker(s)")

    worker_args = [
        {
            "db": args.db,
            "base_url": args.base_url,
            "openai_base_url": args.openai_base_url,
            "openai_api_key": args.openai_api_key,
            "backends": [parse_backend_spec(x) for x in (args.backend or [f"ollama:{args.model}"])],
            "delay": args.delay,
            "batch_size": args.batch_size,
            "worker_id": i,
            "run_id": run_id,
            "prefer_missing_backend": (
                parse_backend_identity(args.prefer_missing_backend)
                if args.prefer_missing_backend
                else None
            ),
        }
        for i in range(args.jobs)
    ]

    if args.jobs == 1:
        worker(worker_args[0])
    else:
        with mp.Pool(processes=args.jobs) as pool:
            pool.map(worker, worker_args)


if __name__ == "__main__":
    main()
