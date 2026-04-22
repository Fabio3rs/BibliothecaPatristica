"""Versionamento de resultados OCR em SQLite (WAL).

Cada execução de OCR gera um registro em ``ocr_results`` vinculado a uma
combinação engine+modelo+prompt catalogada em ``ocr_engine_versions``.

Prompts e textos são armazenados inline; diffs são calculados dinamicamente.
SHA256 é usado em toda comparação de igualdade (texto, imagem, prompts).

Veja ``docs/VERSIONING_PLAN.md`` para o plano completo de design.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path("data/ocr_versions.db")

_SCHEMA_SQL = """\
-- Catálogo de combinações engine+modelo+prompt.
-- Cada combinação única recebe um ID; prompts completos são guardados aqui.
CREATE TABLE IF NOT EXISTS ocr_engine_versions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    engine                TEXT NOT NULL,
    model                 TEXT,
    prompt_key            TEXT,
    system_prompt_hash    TEXT NOT NULL,
    system_prompt_content TEXT NOT NULL,
    user_prompt_hash      TEXT NOT NULL DEFAULT '',
    user_prompt_content   TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_version
    ON ocr_engine_versions(engine, COALESCE(model, ''), system_prompt_hash, user_prompt_hash);

-- Cada execução de OCR para uma página/imagem.
CREATE TABLE IF NOT EXISTS ocr_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id           TEXT NOT NULL,
    page_num            INTEGER NOT NULL,
    image_path          TEXT NOT NULL,
    image_hash          TEXT NOT NULL,
    engine_version_id   INTEGER NOT NULL REFERENCES ocr_engine_versions(id),
    text_content        TEXT NOT NULL,
    text_hash           TEXT NOT NULL,
    is_current          INTEGER NOT NULL DEFAULT 1,
    reprocess_reason    TEXT,
    meta_json           TEXT,
    duration_ms         REAL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ocr_results_vol_page
    ON ocr_results(volume_id, page_num);
CREATE INDEX IF NOT EXISTS idx_ocr_results_image_path
    ON ocr_results(image_path);
CREATE INDEX IF NOT EXISTS idx_ocr_results_is_current
    ON ocr_results(is_current);
CREATE INDEX IF NOT EXISTS idx_ocr_results_text_hash
    ON ocr_results(text_hash);
CREATE INDEX IF NOT EXISTS idx_ocr_results_image_hash
    ON ocr_results(image_hash);

-- Índice composto para idempotência rápida (backfill / record_ocr_result)
CREATE INDEX IF NOT EXISTS idx_ocr_results_dedup
    ON ocr_results(image_hash, engine_version_id, text_hash);

-- Índice composto para UPDATE is_current (desativar versão anterior)
CREATE INDEX IF NOT EXISTS idx_ocr_results_vol_page_current
    ON ocr_results(volume_id, page_num, is_current)
    WHERE is_current = 1;
"""


# ---------------------------------------------------------------------------
# Utilitários de hash
# ---------------------------------------------------------------------------


def sha256_str(text: str) -> str:
    """SHA256 hex de uma string UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    """SHA256 hex do conteúdo binário de um arquivo."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Conexão e schema
# ---------------------------------------------------------------------------


def connect_versions_db(path: Path) -> sqlite3.Connection:
    """Abre conexão com WAL + busy_timeout.  Padrão canônico do projeto."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def init_versions_schema(con: sqlite3.Connection) -> None:
    """Cria tabelas e índices se não existirem (idempotente)."""
    con.executescript(_SCHEMA_SQL)
    con.commit()


def open_versions_db(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Abre e inicializa; chamada DENTRO do worker, após o fork."""
    con = connect_versions_db(path)
    init_versions_schema(con)
    return con


# ---------------------------------------------------------------------------
# Registro de engine/modelo/prompt
# ---------------------------------------------------------------------------


def get_or_create_engine_version(
    con: sqlite3.Connection,
    *,
    engine: str,
    model: str | None,
    prompt_key: str,
    system_prompt: str,
    user_prompt: str = "",
) -> int:
    """Retorna o ``id`` da combinação engine+model+prompts, criando se necessário.

    O hash é calculado sobre o conteúdo integral dos prompts. Para templates
    com ``{placeholder}``, passe o template cru — **não** o valor renderizado.
    """
    sys_hash = sha256_str(system_prompt)
    usr_hash = sha256_str(user_prompt) if user_prompt else ""

    model_coalesced = model or ""

    row = con.execute(
        """SELECT id FROM ocr_engine_versions
           WHERE engine = ?
             AND COALESCE(model, '') = ?
             AND system_prompt_hash = ?
             AND user_prompt_hash = ?""",
        (engine, model_coalesced, sys_hash, usr_hash),
    ).fetchone()

    if row is not None:
        return row["id"]

    try:
        cur = con.execute(
            """INSERT INTO ocr_engine_versions
               (engine, model, prompt_key, system_prompt_hash, system_prompt_content,
                user_prompt_hash, user_prompt_content)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (engine, model, prompt_key, sys_hash, system_prompt, usr_hash, user_prompt),
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]
    except sqlite3.IntegrityError:
        # Race condition: outro processo/thread inseriu entre o SELECT e o INSERT.
        # Re-consulta para obter o id que foi inserido pelo outro.
        row = con.execute(
            """SELECT id FROM ocr_engine_versions
               WHERE engine = ?
                 AND COALESCE(model, '') = ?
                 AND system_prompt_hash = ?
                 AND user_prompt_hash = ?""",
            (engine, model_coalesced, sys_hash, usr_hash),
        ).fetchone()
        if row is not None:
            return row["id"]
        raise  # IntegrityError por outro motivo inesperado


# ---------------------------------------------------------------------------
# Registro de resultado OCR
# ---------------------------------------------------------------------------


def record_ocr_result(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    page_num: int,
    image_path: Path | str,
    engine_version_id: int,
    text_content: str,
    reprocess_reason: str | None = None,
    duration_ms: float | None = None,
    meta_json: str | None = None,
    image_hash: str | None = None,
) -> int:
    """Registra uma versão de OCR para uma página.

    * Idempotente: mesma ``image_hash`` + ``engine_version_id`` + ``text_hash``
      → retorna o ``id`` existente sem duplicar.
    * Atômico: usa ``BEGIN IMMEDIATE`` para ``UPDATE is_current + INSERT``.
    * Se ``image_hash`` não for fornecido, calcula a partir de ``image_path``.
    """
    if image_hash is None:
        image_hash = sha256_file(image_path)
    text_hash = sha256_str(text_content)

    # -- Idempotência -------------------------------------------------------
    existing = con.execute(
        """SELECT id FROM ocr_results
           WHERE image_hash = ? AND engine_version_id = ? AND text_hash = ?""",
        (image_hash, engine_version_id, text_hash),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    # -- Transação atômica: desativar anteriores + inserir novo -------------
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            """UPDATE ocr_results
               SET is_current = 0, updated_at = datetime('now')
               WHERE volume_id = ? AND page_num = ? AND is_current = 1""",
            (volume_id, page_num),
        )
        cur = con.execute(
            """INSERT INTO ocr_results
               (volume_id, page_num, image_path, image_hash, engine_version_id,
                text_content, text_hash, is_current, reprocess_reason, meta_json,
                duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                volume_id,
                page_num,
                str(image_path),
                image_hash,
                engine_version_id,
                text_content,
                text_hash,
                reprocess_reason,
                meta_json,
                duration_ms,
            ),
        )
        new_id = cur.lastrowid
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return new_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def get_current_result(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> sqlite3.Row | None:
    """Retorna o ``ocr_result`` com ``is_current=1`` para a página, ou ``None``."""
    return con.execute(
        """SELECT * FROM ocr_results
           WHERE volume_id = ? AND page_num = ? AND is_current = 1""",
        (volume_id, page_num),
    ).fetchone()


def get_history(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> list[sqlite3.Row]:
    """Retorna todas as versões da página, mais recente primeiro."""
    return con.execute(
        """SELECT * FROM ocr_results
           WHERE volume_id = ? AND page_num = ?
           ORDER BY created_at DESC, id DESC""",
        (volume_id, page_num),
    ).fetchall()


def get_engine_version(
    con: sqlite3.Connection,
    engine_version_id: int,
) -> sqlite3.Row | None:
    """Retorna os detalhes de uma ``ocr_engine_version`` pelo ``id``."""
    return con.execute(
        "SELECT * FROM ocr_engine_versions WHERE id = ?",
        (engine_version_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Diff dinâmico
# ---------------------------------------------------------------------------


def _diff_stat(old_lines: list[str], new_lines: list[str]) -> dict[str, int]:
    """Calcula estatísticas simples de diff baseadas em SequenceMatcher."""
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    insertions = 0
    deletions = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            deletions += i2 - i1
            insertions += j2 - j1
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "insert":
            insertions += j2 - j1
    return {
        "insertions": insertions,
        "deletions": deletions,
        "old_len": len(old_lines),
        "new_len": len(new_lines),
    }


def compute_diff(
    result_a: sqlite3.Row,
    result_b: sqlite3.Row,
    mode: str = "unified",
) -> dict[str, Any]:
    """Calcula diff entre dois ``ocr_results``.

    Atalho O(1) via ``text_hash`` quando os textos são idênticos.

    *mode*: ``"unified"`` (inclui diff + stat) ou ``"stat_only"`` (só contagens).
    """
    text_equal = result_a["text_hash"] == result_b["text_hash"]
    base: dict[str, Any] = {
        "from_id": result_a["id"],
        "to_id": result_b["id"],
        "text_equal": text_equal,
    }
    if text_equal:
        base["unified"] = ""
        base["stat"] = {}
        return base

    old_lines = result_a["text_content"].splitlines(keepends=True)
    new_lines = result_b["text_content"].splitlines(keepends=True)

    base["stat"] = _diff_stat(old_lines, new_lines)

    if mode == "stat_only":
        base["unified"] = ""
    else:
        base["unified"] = "".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"v{result_a['id']} ({result_a['created_at']})",
                tofile=f"v{result_b['id']} ({result_b['created_at']})",
            )
        )
    return base


def compute_diff_chain(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> list[dict[str, Any]]:
    """Retorna diffs entre versões consecutivas (mais antiga → mais recente)."""
    history = get_history(con, volume_id, page_num)
    history = list(reversed(history))  # ASC
    return [
        compute_diff(history[i], history[i + 1])
        for i in range(len(history) - 1)
    ]


# ---------------------------------------------------------------------------
# Utilitários de manutenção
# ---------------------------------------------------------------------------


def revert_to(
    con: sqlite3.Connection,
    result_id: int,
    txt_dir: Path,
) -> None:
    """Restaura o ``.txt`` para a versão ``result_id`` e marca ``is_current=1``.

    Levanta ``ValueError`` se ``result_id`` não existir.
    """
    row = con.execute(
        "SELECT * FROM ocr_results WHERE id = ?", (result_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"ocr_result id={result_id} não encontrado")

    volume_id = row["volume_id"]
    page_num = row["page_num"]

    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            """UPDATE ocr_results
               SET is_current = 0, updated_at = datetime('now')
               WHERE volume_id = ? AND page_num = ? AND is_current = 1""",
            (volume_id, page_num),
        )
        con.execute(
            """UPDATE ocr_results
               SET is_current = 1, updated_at = datetime('now')
               WHERE id = ?""",
            (result_id,),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # Restaura o arquivo .txt no filesystem
    txt_path = txt_dir / f"{page_num:06d}.txt"
    txt_path.write_text(row["text_content"], encoding="utf-8")


def print_history(
    volume_id: str,
    page_num: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Imprime histórico legível no stdout."""
    con = open_versions_db(db_path)
    history = get_history(con, volume_id, page_num)
    if not history:
        print(f"Nenhum registro para {volume_id} p.{page_num}")
        return

    for r in history:
        ev = get_engine_version(con, r["engine_version_id"])
        current = "→ CURRENT" if r["is_current"] else ""
        meta = ""
        if r["meta_json"]:
            meta = f"  meta={r['meta_json']}"
        print(
            f"  [{r['id']}] {r['created_at']}  "
            f"{ev['engine']}/{ev['model'] or '-'}  "
            f"prompt_key={ev['prompt_key']}  "
            f"reason={r['reprocess_reason'] or '-'}  "
            f"text_hash={r['text_hash'][:16]}…  "
            f"{current}{meta}"
        )
    con.close()
