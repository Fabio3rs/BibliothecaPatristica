#!/usr/bin/env python3
"""
Ingesta keywords_json da tabela resumos e popula tabelas normalizadas:
  - keywords (canônico)
  - keyword_alias (sinônimos/variantes)
  - keyword_category (categorias livres)
  - keyword_occurrence (pivô keyword x página, preservando rank)

Idempotente: pode rodar múltiplas vezes; usa UPSERT para evitar duplicatas e
atualiza updated_at nas colisões. Agora com:
- cache em memória de keywords
- leitura em batches (LIMIT/OFFSET)
- executemany/UPSERT em bloco
- opção de parsing direto no SQLite via json_each
- modo rápido opcional (synchronous=OFF) para cargas grandes
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import unicodedata
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Garantir que o diretório raiz do projeto esteja no sys.path para importar keywords_serial
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from keywords_serial import (
    clean_keywords_structure,
    normalize_keywords_payload,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def strip_markdown_wrappers(text: str) -> str:
    """
    Remove marcas simples de markdown/bullet que chegam como parte das keywords.
    - bullets iniciais: *, -, +, •, >, # (com espaço opcional)
    - code/ênfase simétrica em volta (*...*, **...**, __...__, `...`)
    """
    t = (text or "").strip()
    # Remove bullets/headers quoting no início
    t = re.sub(r"^(?:[>#]+|\*+|[-+•\u2022]+|#+)\s*", "", t)
    # Descasca wrappers simétricos comuns
    while len(t) >= 2 and t[0] == t[-1] and t[0] in "*_`'\"":
        t = t[1:-1].strip()
    return t


def clean_keyword_original(text: str) -> str:
    """Limpa keyword para armazenar como original (sem aspas/pontuação de borda)."""
    text = strip_markdown_wrappers(text)
    text = unicodedata.normalize("NFKC", text or "")
    text = " ".join(text.split())
    strip_chars = " \"'«»“”‘’()[]{}|\\/–—-:;.,!?·•*&"
    return text.strip(strip_chars)


def normalize_kw(text: str) -> str:
    """Normaliza keyword para chave canônica.

    Passos:
    - NFKD + remoção de diacríticos (acentos) para colapsar variantes como "simōn"/"simón".
    - NFKC para recompor, trim de whitespace/pontuação leve/aspas envoltórias, casefold.
    """
    text = strip_markdown_wrappers(text)
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = unicodedata.normalize("NFKC", text)
    text = " ".join(text.split())
    # remove aspas/pontuação só nas extremidades para evitar keywords iniciando por """
    strip_chars = " \"'«»“”‘’()[]{}|\\/–—-:;.,!?·•*&"
    text = text.strip(strip_chars)
    return text.casefold()


def normalize_word(text: str) -> str:
    """Normalização pedante para campo word (strip + lower)."""
    return (text or "").strip().lower()


def normalize_category(label: str) -> Tuple[str, str]:
    """Retorna (label_norm, label_original_trimmed)."""
    orig = " ".join((label or "").split())
    norm = orig.casefold()
    return norm, orig


def chunked(seq: List, size: int) -> Iterable[List]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# Keywords com menos de N caracteres normalizados serão marcados como ruído.
MIN_KEYWORD_LEN_NOISE = 3


def is_noise_norm(norm: str) -> bool:
    return len(norm) < MIN_KEYWORD_LEN_NOISE


# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY,
    keyword_norm TEXT NOT NULL UNIQUE,
    keyword_original TEXT NOT NULL,
    -- Novos campos para pipeline de ranking/clustering
    word TEXT UNIQUE,
    categoria TEXT,
    cluster_id INTEGER,
    cluster_label TEXT,
    embedding BLOB,
    -- Campos existentes
    is_noise INTEGER DEFAULT 0,
    hdbscan_group_id INTEGER,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','validated','rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keyword_alias (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    alias_norm TEXT NOT NULL,
    alias_original TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(keyword_id, alias_norm)
);

CREATE TABLE IF NOT EXISTS keyword_category (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    category_label TEXT NOT NULL,
    source_label TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(keyword_id, category_label)
);

CREATE TABLE IF NOT EXISTS keyword_embedding (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    prompt_text TEXT,
    ranked_from_model INTEGER,
    embedding_hash TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kw_emb_hash ON keyword_embedding(embedding_hash);

CREATE TABLE IF NOT EXISTS keyword_occurrence (
    id INTEGER PRIMARY KEY,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    pagina_id INTEGER REFERENCES resumos(id),
    documento TEXT,
    pagina_num INTEGER,
    keywords_source TEXT,
    keywords_modelo TEXT,
    rank_in_page INTEGER,
    rank_position INTEGER,
    count_in_page INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(keyword_id, pagina_id)
);

CREATE INDEX IF NOT EXISTS idx_kw_occ_keyword ON keyword_occurrence(keyword_id);
CREATE INDEX IF NOT EXISTS idx_kw_occ_doc_page ON keyword_occurrence(documento, pagina_num);
CREATE INDEX IF NOT EXISTS idx_keywords_noise_status ON keywords(is_noise, status);
CREATE INDEX IF NOT EXISTS idx_keywords_hdbscan_group ON keywords(hdbscan_group_id);
CREATE INDEX IF NOT EXISTS idx_kw_occ_pagina ON keyword_occurrence(pagina_id);
"""


# -----------------------------------------------------------------------------
# DB ops
# -----------------------------------------------------------------------------


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()
    # Migrações idempotentes para novos campos
    for sql in [
        "ALTER TABLE keywords ADD COLUMN word TEXT",
        "ALTER TABLE keywords ADD COLUMN categoria TEXT",
        "ALTER TABLE keywords ADD COLUMN cluster_id INTEGER",
        "ALTER TABLE keywords ADD COLUMN cluster_label TEXT",
        "ALTER TABLE keywords ADD COLUMN embedding BLOB",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_keywords_word ON keywords(word)",
        "CREATE INDEX IF NOT EXISTS idx_keywords_cluster ON keywords(cluster_id)",
    ]:
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass
    for sql in [
        "ALTER TABLE keyword_occurrence ADD COLUMN pagina_id INTEGER REFERENCES resumos(id)",
        "ALTER TABLE keyword_occurrence ADD COLUMN rank_position INTEGER",
    ]:
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_kw_occ_pagina ON keyword_occurrence(pagina_id)")
    except sqlite3.OperationalError:
        pass
    # Backfill word a partir de keyword_norm
    try:
        con.execute(
            "UPDATE keywords SET word = LOWER(TRIM(keyword_norm)) WHERE word IS NULL OR word = ''"
        )
    except sqlite3.OperationalError:
        pass
    # Views RRF
    con.executescript(
        """
        DROP VIEW IF EXISTS view_global_keyword_score;
        CREATE VIEW IF NOT EXISTS view_global_keyword_score AS
            SELECT keyword_id,
                   SUM(1.0 / (60.0 + rank_position)) AS importance_score,
                   COUNT(pagina_id) AS freq
            FROM keyword_occurrence
            WHERE rank_position IS NOT NULL
            GROUP BY keyword_id;

        DROP VIEW IF EXISTS view_cluster_mass_score;
        CREATE VIEW IF NOT EXISTS view_cluster_mass_score AS
            SELECT k.cluster_id,
                   SUM(g.importance_score) AS massa_teologica
            FROM keywords k
            JOIN view_global_keyword_score g ON g.keyword_id = k.id
            WHERE k.cluster_id IS NOT NULL
            GROUP BY k.cluster_id;
        """
    )


def connect_db(path: Path) -> sqlite3.Connection:
    """Abre conexão SQLite com pragmas seguros e WAL ativado."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def set_fast_mode(con: sqlite3.Connection, enable: bool) -> None:
    con.execute("PRAGMA synchronous = OFF;" if enable else "PRAGMA synchronous = NORMAL;")
    if enable:
        con.execute("PRAGMA temp_store = MEMORY;")


def load_keyword_cache(
    con: sqlite3.Connection, max_cache_size: int | None = None
) -> Dict[str, Tuple[int, str]]:
    """Carrega keyword_norm -> (id, keyword_original) em memória."""
    cache: Dict[str, Tuple[int, str]] = {}
    cur = con.execute("SELECT keyword_norm, id, keyword_original FROM keywords")
    for row in cur:
        cache[row["keyword_norm"]] = (int(row["id"]), row["keyword_original"])
        if max_cache_size and len(cache) >= max_cache_size:
            break
    return cache


def fetch_ids_for_norms(
    con: sqlite3.Connection, norms: List[str]
) -> Dict[str, Tuple[int, str]]:
    found: Dict[str, Tuple[int, str]] = {}
    if not norms:
        return found
    placeholders = ",".join("?" for _ in norms)
    sql = f"SELECT keyword_norm, id, keyword_original FROM keywords WHERE keyword_norm IN ({placeholders})"
    for row in con.execute(sql, norms):
        found[row["keyword_norm"]] = (int(row["id"]), row["keyword_original"])
    return found


def upsert_keyword(
    con: sqlite3.Connection,
    kw_norm: str,
    kw_original: str,
) -> Tuple[int, bool]:
    """Upsert keyword. Retorna (keyword_id, is_new_or_alias_added)."""
    word = normalize_word(kw_original)
    con.execute(
        """
        INSERT INTO keywords (keyword_norm, keyword_original, word)
        VALUES (?, ?, ?)
        ON CONFLICT(keyword_norm) DO UPDATE
            SET updated_at=CURRENT_TIMESTAMP,
                word=COALESCE(keywords.word, excluded.word)
        """,
        (kw_norm, kw_original, word),
    )
    row = con.execute(
        "SELECT id, keyword_original FROM keywords WHERE keyword_norm = ?",
        (kw_norm,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"keyword upsert failed for norm='{kw_norm}'")
    kw_id = int(row["id"])

    # Se o original novo for diferente, salva como alias
    if kw_original and kw_original != row["keyword_original"]:
        upsert_alias(con, kw_id, kw_norm, kw_original)
        return kw_id, True

    return kw_id, False


def upsert_alias(
    con: sqlite3.Connection,
    keyword_id: int,
    alias_norm: str,
    alias_original: str,
) -> None:
    con.execute(
        """
        INSERT INTO keyword_alias (keyword_id, alias_norm, alias_original)
        VALUES (?, ?, ?)
        ON CONFLICT(keyword_id, alias_norm) DO UPDATE
            SET alias_original=excluded.alias_original,
                updated_at=CURRENT_TIMESTAMP
        """,
        (keyword_id, alias_norm, alias_original),
    )


def upsert_category(
    con: sqlite3.Connection,
    keyword_id: int,
    label_norm: str,
    label_orig: str,
) -> None:
    con.execute(
        """
        INSERT INTO keyword_category (keyword_id, category_label, source_label)
        VALUES (?, ?, ?)
        ON CONFLICT(keyword_id, category_label) DO UPDATE
            SET updated_at=CURRENT_TIMESTAMP
        """,
        (keyword_id, label_norm, label_orig),
    )


def upsert_occurrence(
    con: sqlite3.Connection,
    keyword_id: int,
    documento: str,
    pagina_num: int,
    source: str,
    modelo: str,
    rank_in_page: int,
    count_in_page: int = 1,
) -> None:
    con.execute(
        """
        INSERT INTO keyword_occurrence
            (keyword_id, documento, pagina_num, keywords_source, keywords_modelo,
             rank_in_page, count_in_page)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(keyword_id, documento, pagina_num, keywords_modelo, keywords_source)
            DO UPDATE SET
                rank_in_page=excluded.rank_in_page,
                count_in_page=excluded.count_in_page,
                updated_at=CURRENT_TIMESTAMP
        """,
        (
            keyword_id,
            documento,
            pagina_num,
            source,
            modelo,
            rank_in_page,
            count_in_page,
        ),
    )


# -----------------------------------------------------------------------------
# Ingest logic
# -----------------------------------------------------------------------------


def iter_resumos_rows(
    con: sqlite3.Connection,
    doc: str | None,
    limit_pages: int | None,
) -> Iterable[sqlite3.Row]:
    base_sql = """
        SELECT id AS pagina_id, documento, pagina_num, keywords_json, keywords_source, keywords_modelo
        FROM resumos
        WHERE keywords_json != ''
    """
    params: List[object] = []
    if doc:
        base_sql += " AND documento = ?"
        params.append(doc)
    base_sql += " ORDER BY documento, pagina_num"
    if limit_pages:
        base_sql += " LIMIT ?"
        params.append(limit_pages)
    for row in con.execute(base_sql, params):
        yield row


def fetch_pages_batch(
    con: sqlite3.Connection,
    doc: str | None,
    offset: int,
    batch_size: int,
    limit_pages: int | None,
) -> List[sqlite3.Row]:
    """Lê páginas (sem expandir keywords) em batch via LIMIT/OFFSET."""
    sql = """
        SELECT id AS pagina_id, documento, pagina_num, keywords_json, keywords_source, keywords_modelo
        FROM resumos
        WHERE keywords_json != ''
    """
    params: List[object] = []
    if doc:
        sql += " AND documento = ?"
        params.append(doc)
    sql += " ORDER BY documento, pagina_num LIMIT ? OFFSET ?"
    params.extend([batch_size, offset])
    rows = list(con.execute(sql, params))
    if limit_pages is not None:
        rows = rows[: max(0, limit_pages - offset)]
    return rows


def fetch_keywords_sql_batch(
    con: sqlite3.Connection,
    doc: str | None,
    offset_pages: int,
    batch_size: int,
    limit_pages: int | None,
    max_keywords_per_page: int,
) -> List[sqlite3.Row]:
    """
    Usa json_each para expandir keywords no SQLite, preservando ordem.
    Retorna linhas com colunas: documento, pagina_num, keywords_source, keywords_modelo,
    rank, keyword, categorias_json.
    """
    sql = """
        WITH sub AS (
            SELECT id AS pagina_id, documento, pagina_num, keywords_json, keywords_source, keywords_modelo,
                   json_extract(keywords_json, '$.categorias') AS categorias_json
            FROM resumos
            WHERE keywords_json != ''
    """
    params: List[object] = []
    if doc:
        sql += " AND documento = ?"
        params.append(doc)
    sql += " ORDER BY documento, pagina_num LIMIT ? OFFSET ?)"
    params.extend([batch_size, offset_pages])

    sql += """
        SELECT
            sub.pagina_id,
            sub.documento,
            sub.pagina_num,
            sub.keywords_source,
            sub.keywords_modelo,
            CAST(json_each.key AS INT) AS rank,
            json_each.value AS keyword,
            sub.categorias_json
        FROM sub,
             json_each(
                COALESCE(
                    json_extract(sub.keywords_json, '$.keywords_ranking'),
                    json_extract(sub.keywords_json, '$.keywords')
                )
             )
    """
    if max_keywords_per_page:
        sql += " WHERE CAST(json_each.key AS INT) < ?"
        params.append(max_keywords_per_page)
    sql += " ORDER BY sub.documento, sub.pagina_num, CAST(json_each.key AS INT)"

    rows = list(con.execute(sql, params))
    return rows


def ingest(
    src_con: sqlite3.Connection,
    dst_con: sqlite3.Connection,
    doc: str | None,
    limit_pages: int | None,
    max_keywords_per_page: int,
    rows_batch: int,
    commit_every_batch: int,
    parse_in_sql: bool,
    fast_unsafe: bool,
    max_cache_size: int | None,
) -> Tuple[int, int, int, Dict[str, int]]:
    """
    Retorna (pages_processed, keywords_touched/alias, occurrences_processed, stats).
    Batching com cache em memória e executemany.
    """
    cache = load_keyword_cache(dst_con, max_cache_size=max_cache_size)
    pages = 0
    kws_touched = 0  # novos ou alias gravado
    occ_processed = 0
    stats = {
        "truncated_pages": 0,
        "parse_issue": 0,
        "empty_keywords": 0,
        "alias_added": 0,
        "noise_keywords": 0,
        "batches": 0,
    }

    if fast_unsafe:
        set_fast_mode(dst_con, True)

    offset = 0
    while True:
        batch_start = time.time()
        page_records = []

        if parse_in_sql:
            rows = fetch_keywords_sql_batch(
                src_con,
                doc=doc,
                offset_pages=offset,
                batch_size=rows_batch,
                limit_pages=limit_pages,
                max_keywords_per_page=max_keywords_per_page,
            )
            if not rows:
                break
            # Agrupa por página preservando ordem (já ordenado na query)
            grouped: Dict[Tuple[str, int], dict] = {}
            for r in rows:
                key = (r["documento"], r["pagina_num"])
                if key not in grouped:
                    grouped[key] = {
                        "pagina_id": r["pagina_id"],
                        "documento": r["documento"],
                        "pagina_num": r["pagina_num"],
                        "keywords_source": r["keywords_source"] or "",
                        "keywords_modelo": r["keywords_modelo"] or "",
                        "keywords_ranked": [],
                        "categorias": {},
                        "categorias_json": r["categorias_json"],
                    }
                grouped[key]["keywords_ranked"].append((r["keyword"], r["rank"]))
            # Parse categorias uma vez por página
            for rec in grouped.values():
                cat_raw = rec.get("categorias_json")
                if cat_raw:
                    try:
                        cat_obj = json.loads(cat_raw)
                        if isinstance(cat_obj, dict):
                            rec["categorias"] = {
                                k: v for k, v in cat_obj.items() if isinstance(v, list)
                            }
                    except Exception:
                        stats["parse_issue"] += 1
            page_records = list(grouped.values())
            pages_in_batch = len(page_records)
        else:
            rows = fetch_pages_batch(
                src_con,
                doc=doc,
                offset=offset,
                batch_size=rows_batch,
                limit_pages=limit_pages,
            )
            if not rows:
                break
            for r in rows:
                payload = r["keywords_json"]
                parsed, parse_issue = normalize_keywords_payload(payload)
                cleaned, _clean_issues = clean_keywords_structure(parsed)
                keywords = cleaned.get("keywords_ranking") or cleaned.get("keywords") or []
                categorias = cleaned.get("categorias", {})
                if parse_issue:
                    stats["parse_issue"] += 1
                if not keywords:
                    stats["empty_keywords"] += 1
                    continue
                if max_keywords_per_page and len(keywords) > max_keywords_per_page:
                    keywords = keywords[:max_keywords_per_page]
                    stats["truncated_pages"] += 1
                page_records.append(
                    {
                        "pagina_id": r["pagina_id"],
                        "documento": r["documento"],
                        "pagina_num": r["pagina_num"],
                        "keywords_source": r["keywords_source"] or "",
                        "keywords_modelo": r["keywords_modelo"] or "",
                        "keywords_ranked": list(enumerate(keywords)),
                        "categorias": categorias,
                    }
                )
            pages_in_batch = len(rows)

        if limit_pages is not None and pages >= limit_pages:
            break

        # Stage keywords: collect unique norms em batch (inclui termos só em categorias)
        batch_norm_to_original: Dict[str, str] = {}
        noise_norm_to_original: Dict[str, str] = {}
        noise_norms: set[str] = set()
        page_kw_ids: Dict[Tuple[str, int], List[Tuple[Optional[int], str]]] = {}
        for rec in page_records:
            kw_entries: List[Tuple[int, str]] = []
            for rank_pos, kw in rec["keywords_ranked"]:
                kw_clean = clean_keyword_original(kw)
                norm = normalize_kw(kw)
                word_norm = kw.strip().lower()
                if not norm:
                    continue
                if is_noise_norm(norm):
                    noise_norms.add(norm)
                    noise_norm_to_original.setdefault(norm, kw_clean)
                    continue
                if norm not in batch_norm_to_original:
                    batch_norm_to_original[norm] = kw_clean
                kw_entries.append((rank_pos, norm))
            # Termos que aparecem só nas categorias
            cat_terms: List[str] = []
            for items in (rec.get("categorias") or {}).values():
                if isinstance(items, list):
                    cat_terms.extend([it for it in items if isinstance(it, str)])
            for kw in cat_terms:
                kw_clean = clean_keyword_original(kw)
                norm = normalize_kw(kw)
                if not norm or is_noise_norm(norm):
                    continue
                if norm not in batch_norm_to_original:
                    batch_norm_to_original[norm] = kw_clean
                # evita duplicar se já veio no ranking
                if all(norm != n for _, n in kw_entries):
                    kw_entries.append((None, norm))  # rank ausente
            page_kw_ids[(rec["documento"], rec["pagina_num"])] = kw_entries

        new_norms = [n for n in batch_norm_to_original if n not in cache]
        # bulk insert new norms
        cache_len_before = len(cache)
        if new_norms:
            dst_con.executemany(
                "INSERT OR IGNORE INTO keywords (keyword_norm, keyword_original, word) VALUES (?, ?, ?)",
                [(n, batch_norm_to_original[n], normalize_word(batch_norm_to_original[n])) for n in new_norms],
            )
            fetched = fetch_ids_for_norms(dst_con, new_norms)
            cache.update(fetched)
        if len(cache) > cache_len_before:
            kws_touched += len(cache) - cache_len_before

        # Inserir keywords curtas como ruído (is_noise=1)
        noise_new = [n for n in noise_norm_to_original if n not in cache]
        if noise_new:
            dst_con.executemany(
                "INSERT OR IGNORE INTO keywords (keyword_norm, keyword_original, word, is_noise) VALUES (?, ?, ?, 1)",
                [(n, noise_norm_to_original[n], normalize_word(noise_norm_to_original[n])) for n in noise_new],
            )
            fetched_noise = fetch_ids_for_norms(dst_con, noise_new)
            cache.update(fetched_noise)
            stats["noise_keywords"] += len(noise_new)

        # Resolve ids for all norms in batch
        alias_rows = []
        for norm, orig in batch_norm_to_original.items():
            if norm not in cache:
                continue
            kw_id, stored_orig = cache[norm]
            if orig and orig != stored_orig:
                alias_rows.append((kw_id, norm, orig))
                cache[norm] = (kw_id, stored_orig)  # keep canonical original
        if alias_rows:
            dst_con.executemany(
                """
                INSERT INTO keyword_alias (keyword_id, alias_norm, alias_original)
                VALUES (?, ?, ?)
                ON CONFLICT(keyword_id, alias_norm) DO UPDATE SET
                    alias_original=excluded.alias_original,
                    updated_at=CURRENT_TIMESTAMP
                """,
                alias_rows,
            )
            stats["alias_added"] += len(alias_rows)
            kws_touched += len(alias_rows)

        # Build occurrences rows
        occ_rows = []
        for rec in page_records:
            norms_ranked = page_kw_ids[(rec["documento"], rec["pagina_num"])]
            for rank_pos, norm in norms_ranked:
                entry = cache.get(norm)
                if not entry:
                    continue
                kw_id = entry[0]
                occ_rows.append(
                    (
                        kw_id,
                        rec.get("pagina_id"),
                        rec["documento"],
                        rec["pagina_num"],
                        rec["keywords_source"],
                        rec["keywords_modelo"],
                        None if rank_pos is None else rank_pos + 1,  # rank_in_page
                        rank_pos,      # rank_position 0-based para RRF (pode ser None)
                        1,
                    )
                )

        if occ_rows:
            dst_con.executemany(
                """
                INSERT INTO keyword_occurrence
                    (keyword_id, pagina_id, documento, pagina_num, keywords_source, keywords_modelo,
                     rank_in_page, rank_position, count_in_page)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword_id, pagina_id)
                    DO UPDATE SET
                        rank_in_page=excluded.rank_in_page,
                        rank_position=excluded.rank_position,
                        count_in_page=excluded.count_in_page,
                        updated_at=CURRENT_TIMESTAMP
                """,
                occ_rows,
            )
            occ_processed += len(occ_rows)

        # Categories
        cat_rows = []
        for rec in page_records:
            categorias = rec.get("categorias") or {}
            for cat_label, items in categorias.items():
                cat_norm, cat_orig = normalize_category(cat_label)
                for kw in items:
                    norm = normalize_kw(kw)
                    entry = cache.get(norm)
                    if not entry:
                        continue
                    kw_id = entry[0]
                    cat_rows.append((kw_id, cat_norm, cat_orig))
        if cat_rows:
            dst_con.executemany(
                """
                INSERT INTO keyword_category (keyword_id, category_label, source_label)
                VALUES (?, ?, ?)
                ON CONFLICT(keyword_id, category_label) DO UPDATE
                    SET updated_at=CURRENT_TIMESTAMP
                """,
                cat_rows,
            )

        pages += pages_in_batch
        stats["batches"] += 1
        if stats["batches"] % commit_every_batch == 0:
            dst_con.commit()

        # opcional: limite global
        if limit_pages is not None and pages >= limit_pages:
            break

        offset += rows_batch

        elapsed = time.time() - batch_start
        if stats["batches"] % 10 == 0:
            print(
                f"[batch {stats['batches']}] pages={pages} occ={occ_processed} cache={len(cache)} time={elapsed:.2f}s"
            )

    dst_con.commit()
    if fast_unsafe:
        set_fast_mode(dst_con, False)
    return pages, kws_touched, occ_processed, stats


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ingesta de keywords_json (resumos) para tabelas normalizadas."
    )
    p.add_argument(
        "--resumos-db", type=Path, default=Path("data/patristica_resumos.db")
    )
    p.add_argument("--out-db", type=Path, default=Path("data/patristica_keywords.db"))
    p.add_argument("--doc", help="Documento específico (ex.: PL001).")
    p.add_argument("--limit", type=int, help="Limite de páginas para teste.")
    p.add_argument(
        "--max-keywords-per-page",
        type=int,
        default=40,
        help="Trunca listas muito grandes (0 para ilimitado).",
    )
    p.add_argument(
        "--rows-batch",
        type=int,
        default=2000,
        help="Quantidade de páginas lidas por batch (LIMIT/OFFSET).",
    )
    p.add_argument(
        "--commit-every-batch",
        type=int,
        default=1,
        help="Frequência de commit em batches (1=commit a cada batch).",
    )
    p.add_argument(
        "--parse-in-sql",
        action="store_true",
        help="Usa json_each no SQLite para expandir keywords (mais rápido, preserva ordem).",
    )
    p.add_argument(
        "--fast-unsafe",
        action="store_true",
        help="PRAGMA synchronous=OFF e temp_store=MEMORY durante ingestão (mais rápido, menos seguro).",
    )
    p.add_argument(
        "--max-cache-size",
        type=int,
        help="Limite opcional de keywords carregadas no cache; se ausente, carrega todas.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    with connect_db(args.resumos_db) as src_con, connect_db(args.out_db) as dst_con:
        ensure_schema(dst_con)

        initial_changes = dst_con.total_changes
        pages, kws, occs, stats = ingest(
            src_con=src_con,
            dst_con=dst_con,
            doc=args.doc,
            limit_pages=args.limit,
            max_keywords_per_page=args.max_keywords_per_page,
            rows_batch=args.rows_batch,
            commit_every_batch=args.commit_every_batch,
            parse_in_sql=args.parse_in_sql,
            fast_unsafe=args.fast_unsafe,
            max_cache_size=args.max_cache_size,
        )
        db_changes = dst_con.total_changes - initial_changes

    print(
        f"Ingestão concluída: {pages} páginas processadas, "
        f"{kws} keywords tocadas/novas, {occs} ocorrências processadas, "
        f"{db_changes} mudanças reais no DB. "
        f"Parse_issues={stats['parse_issue']}, "
        f"vazias={stats['empty_keywords']}, "
        f"truncadas={stats['truncated_pages']}."
    )


if __name__ == "__main__":
    main()
