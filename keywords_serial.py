#!/usr/bin/env python3
"""
keywords_serial.py – Extração de keywords página-a-página via Ollama ou OpenAI.

Lê dados do DB de resumos (patristica_resumos.db) e extrai keywords de cada
página. Por padrão opera em DRY RUN (imprime no stdout sem gravar).

Fontes de input configuráveis (--source):
  - resumo_pagina  : usa só o resumo da página (rápido, menos tokens)
  - resumo_global  : usa só o resumo global acumulado
  - pagina_texto   : usa o texto OCR completo da página
  - resumo+pagina  : usa resumo da página + texto OCR (mais completo)
  - tudo            : usa resumo_pagina + resumo_global + pagina_texto

Uso (dry run – padrão):
    python keywords_serial.py --doc PG001 --limit 5
    python keywords_serial.py --doc PG001 --source pagina_texto --limit 3
    python keywords_serial.py --doc PG001 --source resumo+pagina

Uso (gravar no DB):
    python keywords_serial.py --doc PG001 --write
    python keywords_serial.py --all --write --provider openai --model gpt-5-mini

Tweaking rápido:
    python keywords_serial.py --doc PG001 --page 4 --source resumo+pagina
    python keywords_serial.py --doc PG001 --page 4 --source pagina_texto --model qwen3:8b
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, List, Optional, TextIO, Tuple

# Validação linguística de keywords (NLTK + CLTK)
from keyword_integrity import IntegrityStatus, ValidationEvidence
from facsimile_transport import encode_facsimile_for_transport
from keyword_sanitization import (
    HARD_SANITIZATION_ISSUES,
    clean_keyword_text,
    keyword_normalization_key,
    remove_unicode_format_chars,
    sanitize_keyword_item,
    strip_markdown_wrappers,
)

# Heurísticas de ruído/rejeição de página OCR
from scripts.limpeza_ocr import clean_ocr_text_optimized, classify_page_noise
from tools.indexing.index_target_locator import resolve_paired_page_image
from tools.scripture.book_catalog import canonical_book_key
from tools.scripture.citation_index import ScanTask, scan_file_task
from scripture_ref_normalizer import (
    extract_citations_from_value_cached,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUMOS_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_KEYWORDS_DB = PROJECT_ROOT / "data" / "patristica_keywords.db"
DEFAULT_CORPUS_ROOT = PROJECT_ROOT / "teste"
DEFAULT_MODEL = "qwen3:30b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_OLLAMA = 120  # Ollama local é rápido
DEFAULT_TIMEOUT_OPENAI = 300  # OpenAI com reasoning pode demorar

DEFAULT_NUM_CTX = 16384
TOKEN_RESERVE_OUTPUT = 1024  # keywords precisam de menos output
TOKEN_RESERVE_SYSTEM = 400
CHARS_PER_TOKEN = 3.8
RECOMMENDED_MIN_KEYWORDS = 5
RECOMMENDED_MAX_KEYWORDS = 20
MAX_KEYWORDS_LENGTH = 180
OUTLIER_LOW_RATIO = 0.4
OUTLIER_HIGH_RATIO = 2.0

MAX_TOKENS_DEFAULT = 6 * 1024
MAX_FACSIMILE_BYTES = 20 * 1024 * 1024
MAX_SCRIPTURE_EVIDENCE_GROUPS = 12
AMBIGUOUS_CHAPTER_ONLY_SCRIPTURE_RE = re.compile(
    r"(?i)^\s*(?:num|nm|col)\.?\s+[ivxlcdm\d]+\s*$"
)

# Integridade via serviço HTTP (opcional)
INTEGRITY_HTTP_URL = os.getenv("KW_INTEGRITY_HTTP_URL", "").strip()
INTEGRITY_HTTP_TIMEOUT = float(os.getenv("KW_INTEGRITY_HTTP_TIMEOUT", "60.0"))

# Flags de integridade (controladas via CLI)
INTEGRITY_ENABLED = False
INTEGRITY_DEBUG = False


def set_integrity_flags(enabled: bool, debug: bool) -> None:
    global INTEGRITY_ENABLED, INTEGRITY_DEBUG
    INTEGRITY_ENABLED = enabled
    INTEGRITY_DEBUG = debug


VALID_SOURCES = [
    "resumo_pagina",
    "resumo_global",
    "pagina_texto",
    "resumo+pagina",
    "tudo",
]


TEMPERATURE_DEFAULT = 0.1
TOP_P_DEFAULT = 0.4


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("keywords_serial")

# ---------------------------------------------------------------------------
# SQLite – leitura do DB de resumos
# ---------------------------------------------------------------------------


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"DB de resumos não encontrado: {path}")
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def connect_readwrite(path: Path) -> sqlite3.Connection:
    """Abre DB de resumos em modo leitura/escrita (para gravar keywords)."""
    if not path.exists():
        raise FileNotFoundError(f"DB de resumos não encontrado: {path}")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def connect_keywords_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_keywords_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS keywords (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            documento     TEXT    NOT NULL,
            pagina_num    INTEGER NOT NULL,
            source        TEXT    NOT NULL,
            keywords_json TEXT    NOT NULL,
            keywords_raw  TEXT    NOT NULL,
            modelo        TEXT    NOT NULL,
            prompt_hash   TEXT    NOT NULL DEFAULT '',
            criado_em     TEXT    NOT NULL,
            UNIQUE(documento, pagina_num, source, modelo)
        );
        CREATE INDEX IF NOT EXISTS idx_kw_doc
            ON keywords(documento);
        CREATE INDEX IF NOT EXISTS idx_kw_doc_page
            ON keywords(documento, pagina_num);
        """
    )
    con.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_keywords_columns(con: sqlite3.Connection) -> None:
    """Adiciona colunas de keywords na tabela resumos se não existirem."""
    for col, typedef in [
        ("keywords_json", "TEXT NOT NULL DEFAULT ''"),
        ("keywords_source", "TEXT NOT NULL DEFAULT ''"),
        ("keywords_modelo", "TEXT NOT NULL DEFAULT ''"),
    ]:
        try:
            con.execute(f"ALTER TABLE resumos ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # coluna já existe
    con.commit()


def list_documents(con: sqlite3.Connection) -> List[str]:
    rows = con.execute(
        "SELECT DISTINCT documento FROM resumos ORDER BY documento"
    ).fetchall()
    return [r["documento"] for r in rows]


def fetch_pages(
    con: sqlite3.Connection,
    documento: str,
    page: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    """Busca páginas do DB de resumos."""
    sql = "SELECT * FROM resumos WHERE documento = ?"
    params: list = [documento]
    if page is not None:
        sql += " AND pagina_num = ?"
        params.append(page)
    sql += " ORDER BY pagina_num"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Evidência física da página (OCR, citações e fac-símile)
# ---------------------------------------------------------------------------


def resolve_page_ocr_path(
    row: dict,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> Path | None:
    """Resolve ``pagina_file`` sem confundir sufixo físico com página editorial."""

    raw = str(row.get("pagina_file") or "").strip()
    document = str(row.get("documento") or "").strip()
    if not raw or not document:
        return None

    source = Path(raw).expanduser()
    candidates: list[Path] = []
    if source.is_absolute():
        candidates.append(source)
    else:
        candidates.extend(
            [
                PROJECT_ROOT / source,
                corpus_root / document / "text" / source.name,
                corpus_root / document / source,
            ]
        )

    existing = list(
        dict.fromkeys(path.resolve() for path in candidates if path.is_file())
    )
    return existing[0] if len(existing) == 1 else None


def resolve_page_facsimile(
    row: dict,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> Path | None:
    ocr_path = resolve_page_ocr_path(row, corpus_root=corpus_root)
    if ocr_path is None:
        return None
    paired = resolve_paired_page_image(str(ocr_path))
    if not paired:
        return None
    image_path = Path(paired)
    if not image_path.is_file() or image_path.stat().st_size > MAX_FACSIMILE_BYTES:
        return None
    return image_path


def collect_page_scripture_evidence(
    row: dict,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    max_groups: int = MAX_SCRIPTURE_EVIDENCE_GROUPS,
) -> dict[str, Any]:
    """Executa o detector conservador de citações sobre uma única página OCR.

    Reusa o mesmo detector que conhece blocos XML, aparato crítico, limites de
    capítulos e convenções históricas PG/PL/PO. O retorno é compacto e serve
    como evidência para o agente; não altera keywords nem bases.
    """

    document = str(row.get("documento") or "").strip()
    ocr_path = resolve_page_ocr_path(row, corpus_root=corpus_root)
    image_path = resolve_page_facsimile(row, corpus_root=corpus_root)
    base: dict[str, Any] = {
        "status": "ok" if ocr_path else "missing_ocr_file",
        "ocr_file": str(ocr_path) if ocr_path else None,
        "image_file": str(image_path) if image_path else None,
        "candidates": [],
    }
    if ocr_path is None:
        return base

    collection_match = re.match(r"[A-Za-z]+", document)
    collection = (collection_match.group(0) if collection_match else "").upper()
    if collection not in {"PG", "PL", "PO"}:
        collection = "PG"
    volume_root = ocr_path.parent.parent if ocr_path.parent.name == "text" else ocr_path.parent
    try:
        scan = scan_file_task(
            ScanTask(
                volume_id=document,
                collection=collection,
                source_root=str(volume_root),
                file_path=str(ocr_path),
                physical_index=int(row.get("pagina_num") or 0),
                is_index_source=False,
                observed_aliases=(),
                estimator_pages=(),
                profile_fingerprint="keywords-scripture-evidence-v1",
            )
        )
    except Exception as exc:
        log.warning(
            "[%s] p%s detector bíblico falhou em %s: %s",
            document,
            row.get("pagina_num"),
            ocr_path,
            exc,
        )
        base["status"] = "detector_error"
        base["error"] = type(exc).__name__
        return base

    priority = {
        "critical_apparatus": 0,
        "note": 1,
        "body": 2,
        "header": 3,
        "footer": 4,
        "index": 5,
    }
    groups = [
        group
        for group in scan.get("groups") or []
        if not (
            group.get("detector_rule") == "explicit_book_chapter"
            and AMBIGUOUS_CHAPTER_ONLY_SCRIPTURE_RE.fullmatch(
                str(group.get("citation_raw") or "")
            )
        )
    ]
    groups = sorted(
        groups,
        key=lambda item: (
            priority.get(str(item.get("context_kind") or ""), 9),
            -float(item.get("confidence") or 0.0),
            int(item.get("source_start") or 0),
        ),
    )
    candidates: list[dict[str, Any]] = []
    for group in groups[: max(0, max_groups)]:
        occurrences = []
        for occurrence in group.get("occurrences") or []:
            occurrences.append(
                {
                    "book_key": occurrence.get("book_key"),
                    "ref_norm": occurrence.get("ref_norm"),
                    "chapter": occurrence.get("chapter_start"),
                    "verse": occurrence.get("verse_start"),
                    "chapter_end": occurrence.get("chapter_end"),
                    "verse_end": occurrence.get("verse_end"),
                    "status": occurrence.get("normalization_status"),
                }
            )
        candidates.append(
            {
                "raw": group.get("citation_raw"),
                "snippet": group.get("snippet_raw"),
                "context": group.get("context_kind"),
                "rule": group.get("detector_rule"),
                "confidence": group.get("confidence"),
                "occurrences": occurrences,
            }
        )
    base["candidates"] = candidates
    base["candidate_count"] = len(groups)
    base["truncated"] = len(groups) > len(candidates)
    return base


def _scripture_evidence_prompt_block(evidence: dict[str, Any]) -> str:
    candidates = evidence.get("candidates") or []
    if not candidates:
        return (
            "<evidencia_citacoes_regex>\n"
            "Nenhuma citação segura foi detectada automaticamente no OCR. "
            "Isto não prova ausência; não invente livro, capítulo ou versículo.\n"
            "</evidencia_citacoes_regex>\n"
        )
    compact = json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
    return (
        "<evidencia_citacoes_regex>\n"
        "Candidatos determinísticos extraídos do OCR; são evidência para conferir, "
        "não uma ordem para incluir todos. `context` distingue corpo, nota e aparato. "
        "Só devolva capítulo/versículo sustentado pelo OCR ou pelo fac-símile anexado.\n"
        f"{compact}\n"
        "</evidencia_citacoes_regex>\n"
    )


def validate_scripture_warrant(
    payload: KeywordsResult,
    evidence: dict[str, Any],
) -> list[str]:
    """Sinaliza referências explícitas sem par livro/capítulo/versículo no OCR."""

    supported: set[tuple[str, int, str | None]] = set()
    for group in evidence.get("candidates") or []:
        for occurrence in group.get("occurrences") or []:
            book_key = str(occurrence.get("book_key") or "")
            chapter = occurrence.get("chapter")
            verse = occurrence.get("verse")
            if book_key and isinstance(chapter, int):
                supported.add((book_key, chapter, str(verse) if verse is not None else None))

    values: list[str] = list(payload.get("keywords") or [])
    for items in (payload.get("categorias") or {}).values():
        if isinstance(items, list):
            values.extend(item for item in items if isinstance(item, str))

    issues: set[str] = set()
    for value in values:
        records = extract_citations_from_value_cached(
            value,
            source_kind="keyword_warrant",
            source_path="keywords",
            support_mode=True,
        )
        for record in records:
            chapter = record.get("number")
            if not isinstance(chapter, int):
                continue
            book_key = canonical_book_key(
                record.get("book_canonical") or record.get("book"),
                tradition="vulgate_migne",
            )
            if not book_key:
                continue
            verse = record.get("verse")
            key = (book_key, chapter, str(verse) if verse is not None else None)
            if key not in supported:
                issues.add(f"scripture_without_ocr_warrant[{record.get('normalized') or value}]")
    return sorted(issues)


def is_already_done(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    source: str,
    modelo: str,
) -> bool:
    row = con.execute(
        """SELECT 1 FROM keywords
           WHERE documento=? AND pagina_num=? AND source=?""",
        (documento, pagina_num, source),
    ).fetchone()
    return row is not None

    # O "modelo" foi removido da consulta porque não é necessário para verificar se as keywords já foram extraídas.
    # Se o modelo estiver presente na consulta, isso causa o resultado ser refeito ao mudar de modelo
    # row = con.execute(
    #     """SELECT 1 FROM keywords
    #        WHERE documento=? AND pagina_num=? AND source=? AND modelo=?""",
    #     (documento, pagina_num, source, modelo),
    # ).fetchone()
    # return row is not None


def is_already_done_resumos(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
) -> bool:
    """Verifica se keywords já foram extraídas para esta página na tabela resumos.

    Retorna False (= reprocessar) se keywords_json está vazio, nulo,
    ou contém uma lista vazia de keywords (ex: '{"keywords": []}').
    Usa json_array_length() do SQLite para filtrar direto na query.
    """
    row = con.execute(
        """SELECT 1 FROM resumos
           WHERE documento=? AND pagina_num=?
             AND keywords_json IS NOT NULL
             AND keywords_json != ''
             AND COALESCE(
                   json_array_length(json_extract(keywords_json, '$.keywords')),
                   json_array_length(json_extract(keywords_json, '$.keywords_ranking'))
                 ) > 0""",
        (documento, pagina_num),
    ).fetchone()
    return row is not None


def save_keywords(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    source: str,
    keywords_json: str,
    keywords_raw: str,
    modelo: str,
) -> None:
    con.execute(
        """INSERT OR REPLACE INTO keywords
           (documento, pagina_num, source, keywords_json, keywords_raw,
            modelo, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            documento,
            pagina_num,
            source,
            keywords_json,
            keywords_raw,
            modelo,
            _now_iso(),
        ),
    )
    con.commit()


def save_keywords_to_resumos(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    keywords_json: str,
    source: str,
    modelo: str,
) -> None:
    """Grava keywords diretamente na tabela resumos."""
    con.execute(
        """UPDATE resumos
           SET keywords_json = ?, keywords_source = ?, keywords_modelo = ?
           WHERE documento = ? AND pagina_num = ?""",
        (keywords_json, source, modelo, documento, pagina_num),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Estimativa de tokens e truncamento
# ---------------------------------------------------------------------------


def estimate_tokens(text: str, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    if not text:
        return 0
    return int(len(text) / chars_per_token) + 1


def truncate_to_budget(
    text: str,
    budget_tokens: int,
    chars_per_token: float = CHARS_PER_TOKEN,
    label: str = "input",
) -> str:
    """Trunca texto para caber num budget de tokens."""
    tokens = estimate_tokens(text, chars_per_token)
    if tokens <= budget_tokens:
        return text
    max_chars = int(budget_tokens * chars_per_token)
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars * 0.7:
        truncated = truncated[:last_nl]
    truncated = truncated.rstrip() + f"\n[... {label} truncado]"
    log.info(
        "%s truncado: %d→%d tokens (budget=%d)",
        label,
        tokens,
        estimate_tokens(truncated, chars_per_token),
        budget_tokens,
    )
    return truncated


# ---------------------------------------------------------------------------
# LLM – Ollama
# ---------------------------------------------------------------------------


def ollama_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: int = DEFAULT_TIMEOUT_OLLAMA,
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
    image_path: Path | None = None,
) -> str:
    url = f"{base_url}/api/chat"
    user_message: dict[str, Any] = {"role": "user", "content": prompt_user}
    if image_path is not None:
        encoded, _mime_type = encode_facsimile_for_transport(image_path)
        user_message["images"] = [encoded]

    payload: dict = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt_system},
            user_message,
        ],
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_ctx": num_ctx,
        },
    }

    # Desativa thinking em modelos qwen3/deepseek-r1 etc.
    if not think:
        payload["think"] = False
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Ollama falhou: {exc}") from exc

    # Log de estatísticas de performance
    if log.isEnabledFor(logging.DEBUG):
        eval_count = body.get("eval_count", 0)
        eval_duration = body.get("eval_duration", 0)
        prompt_eval_count = body.get("prompt_eval_count", 0)
        if eval_duration > 0:
            tps = eval_count / (eval_duration / 1e9)
            log.debug(
                "Ollama stats: %d prompt tokens, %d eval tokens, %.1f tok/s",
                prompt_eval_count,
                eval_count,
                tps,
            )

    content = (body.get("message") or {}).get("content", "")

    # Remover ```json ``` se o modelo retornar JSON dentro de markdown
    if content.startswith("```json") and content.endswith("```"):
        content = content[7:-3].strip()
    # Se retornar apenas ``` e ```, remover também
    elif content.startswith("```") and content.endswith("```"):
        content = content[3:-3].strip()

    return content.strip()


# ---------------------------------------------------------------------------
# LLM – OpenAI
# ---------------------------------------------------------------------------


def openai_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: int = DEFAULT_TIMEOUT_OPENAI,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    json_schema: dict | None = None,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
    image_path: Path | None = None,
) -> str:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Variável de ambiente {api_key_env} não definida.")
    url = f"{base_url}/chat/completions"

    response_format = {"type": "json_object"}

    # if json_schema:
    #     response_format = {"type": "json_schema", "json_schema": json_schema}

    user_content: str | list[dict[str, Any]] = prompt_user
    if image_path is not None:
        encoded, mime_type = encode_facsimile_for_transport(image_path)
        user_content = [
            {"type": "text", "text": prompt_user},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ]

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": user_content},
        ],
        "response_format": response_format,
    }
    if reasoning_effort and len(reasoning_effort) > 0:
        payload["reasoning_effort"] = reasoning_effort

    if not "gpt-5" in model:
        # Model não é gpt-5, portanto deve suportar temperatura
        payload["temperature"] = temperature
        payload["top_p"] = top_p
        payload["max_tokens"] = MAX_TOKENS_DEFAULT
    else:
        payload["verbosity"] = "low"
        payload["service_tier"] = "flex"

    data = json.dumps(payload).encode("utf-8")
    # Alguns provedores (ex.: Cerebras via Cloudflare) bloqueiam user-agents
    # padrão do urllib. Enviamos um UA explícito e cabeçalho Accept para
    # evitar falsos positivos de WAF.
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": os.getenv("OPENAI_USER_AGENT", "curl/8.5.0"),
    }

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            jsstr = resp.read().decode("utf-8")

            try:
                body = json.loads(jsstr)
            except json.JSONDecodeError as exc:
                print(jsstr)
                raise RuntimeError(f"OpenAI JSONDecodeError: {exc}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(detail, exc)
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI falhou: {exc}") from exc
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI retornou sem choices")
    content = choices[0].get("message", {}).get("content", "")
    return content.strip()


# ---------------------------------------------------------------------------
# Despacho unificado
# ---------------------------------------------------------------------------


def llm_chat(
    prompt_system: str,
    prompt_user: str,
    *,
    provider: str = "ollama",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: int = DEFAULT_TIMEOUT_OLLAMA,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
    json_schema: dict | None = None,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
    image_path: Path | None = None,
) -> str:
    # print(f'{prompt_system} {prompt_user}')
    if provider == "openai":
        return openai_chat(
            prompt_system=prompt_system,
            prompt_user=prompt_user,
            model=model,
            base_url=base_url,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            api_key_env=api_key_env,
            json_schema=json_schema,
            top_p=top_p,
            temperature=temperature,
            image_path=image_path,
        )
    return ollama_chat(
        prompt_system=prompt_system,
        prompt_user=prompt_user,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_ctx=num_ctx,
        think=think,
        top_p=top_p,
        temperature=temperature,
        image_path=image_path,
    )


# ---------------------------------------------------------------------------
# Prompt de keywords (edite à vontade para testar)
# ---------------------------------------------------------------------------

# SYSTEM_PROMPT = """\
# Você é um Especialista em Catalogação de Patrística. Sua tarefa é gerar metadados precisos cruzando o texto original e seu resumo técnico.

# ### DIRETRIZES DE EXTRAÇÃO:
# 1. VALIDAÇÃO DE ENTIDADES (Texto Original): Use o texto bruto para extrair a grafia exata de nomes próprios (Santos, Autores, Hereges), Cidades e Obras citadas. Ignore erros de OCR (corrigindo pequenas falhas conhecidas de OCR).
# 2. MAPEAMENTO TEMÁTICO (Resumo): Bússola temática: resumo_global e resumo_da_pagina para identificar os grandes temas teológicos (ex: 'Cristologia', 'Soteriologia', 'Eclesiologia', etc.), entretanto, cite a keyword apenas se a ver dentro do texto_original.
# 3. HIERARQUIA: Priorize keywords que apareçam em texto_original e que definam o núcleo do argumento teológico/narrativo/filosófico da página. Cite em ordem aproximada de importância, mais importante primeiro, em keywords_ranking.
# 4. LITERALIDADE: Preserve a literalidade dos textos originais, evitando paráfrases ou interpretações, usando traduções literais para PT-BR quando possível ou citando no original quando termo consagrado (latim/grego) (ex: 'Logos', 'Ousia', etc.). Se for uma citação bíblica, evite abreviar, cite o nome completo do livro.

# OBSERVAÇÕES:
# - resumo_global se trata do contexto geral da obra até agora, enquanto resumo_da_pagina foca em aspectos específicos desta página.
# - Se forem vários autores na página, extraia todos os nomes e trate-os como entidades separadas.
# - Normalização de Nomes: Para nomes de pessoas, use a forma canônica em português sempre que possível (ex: 'Ioannes Chrysostomus' -> 'João Crisóstomo'), a menos que seja um autor muito obscuro, mantendo a grafia do original.
# - Não abrevie livros bíblicos nem nomes de obras/autores; escreva o nome completo (ex.: ‘Apocalipse de João’, ‘Atos dos Apóstolos’).
# - texto_original é a âncora
# - notas são totalmente opcionais, não coloque explicações dentro de keywords_ranking ou categorias, se precisar explicar algo use "notas"

# ### FORMATO DE SAÍDA (JSON):
# Retorne EXCLUSIVAMENTE um JSON puro, sem markdown:
# {
#   "keywords_ranking": ["Termo 1", "Termo 2", "..."],
#   "categorias": {
#     "pessoas": ["Nome 1", "Nome 2"],
#     "obras_citadas": ["Obra A", "Obra B"],
#     "temas_teologicos": ["Tema X", "Tema Y"],
#     "termos_tecnicos_lat_gr": ["Termo 1", "Termo 2"]
#   },
#   "notas": { "Termo 1": "Nota sobre o termo 1 (opcional)" }
# }
# """

SYSTEM_PROMPT = """\
Você realiza indexação intelectual por facetas com warrant literário estrito em corpus patrístico.

Regras de Estrutura e Relação:
- keywords_ranking é o agregador central: inclua aqui TODOS os termos e conceitos relevantes da página, rigorosamente ordenados por importância dentro do texto.
- categorias serve para classificar e repetir os termos extraídos em 'keywords_ranking' em suas respectivas facetas. Todo termo categorizado deve ter correspondência direta com o ranking.

Regras de Sintaxe das Keywords (Anti-Prolixidade):
- Cada item no array 'keywords_ranking' DEVE ser um termo isolado, conceito composto ou referência direta (máximo de 3 a 4 palavras por item).
- PROIBIDO gerar frases completas, orações explicativas, verbos conjugados ou resumos de argumentos (ex: NÃO faça "Isaías 53 sobre morte e ressurreição").
- Se um argumento liga uma obra a um tema, quebre em itens separados no array (ex: item 1: "Isaías 53", item 2: "Servo Sofredor", item 3: "Ressurreição").
- Use resumo_global e resumo_da_pagina apenas como bússola temática; só inclua keyword se o termo ou conceito estiver explicitamente sustentado por texto_original.

Regras de Normalização:
- Normalize nomes de pessoas em forma canônica PT-BR quando houver forma consagrada.
- Obras e referências bíblicas por extenso, sem abreviações. Capítulo e versículo separados por vírgula quando explicitamente citados no texto original.
- Não separar nome dos livros dos seus capítulos e versículos, devem ficar em mesmo valor JSON para fazer sentido.
- Trate citações no texto principal, notas e aparato crítico como material pesquisável, mas não confunda números de nota, coluna ou página com capítulo/versículo.
- A seção evidencia_citacoes_regex contém candidatos mecânicos, não uma lista obrigatória. Confira o contexto e inclua apenas os que a página realmente sustenta.
- Se houver fac-símile anexado, use-o para conferir leitura, bloco e pontuação ambíguos do OCR; o fac-símile não autoriza inferir referência ausente.
- Preserve termos técnicos patrísticos consagrados em latim/grego transliterado quando for o uso mais estável.
- Corrija pequenas falhas de OCR sem inventar conteúdo.
- Qualquer explicação vai apenas em notas.

Retorne JSON puro:
{
  "keywords_ranking": [],
  "categorias": {
    "pessoas": [],
    "obras_citadas": [],
    "temas_teologicos": [],
    "termos_tecnicos_lat_gr": []
  },
  "notas": {}
}
"""

# Prompt para verificação de keywords

# SYSTEM_PROMPT_VERIFICACAO = """\
# Você é o Revisor Crítico de Metadados Patrísticos. Sua missão é validar o JSON prévio contra o `texto_original`.

# ### CRITÉRIOS DE AUDITORIA:
# 1. EXISTÊNCIA MATERIAL: A keyword existe no `texto_original`? (Exceção apenas para categorias de 'Temas Teológicos' que usem termos técnicos para descrever o assunto central, desde que o conceito esteja explícito).
# 2. CANONIZAÇÃO DE NOMES: Se o JSON prévio trouxe "Agostinho", mude para "Santo Agostinho". Se trouxe "Jo. Crisóstomo", mude para "João Crisóstomo", mas em caso de ambiguidade, preserve o original (keep).
# 3. ELIMINAÇÃO DE ABREVIAÇÕES: Bíblia e Obras devem estar por extenso. 'Gn 1,1' -> 'Gênesis 1,1'.
# 4. CONSOLIDAÇÃO SEMÂNTICA: Se houver "Logos" e "Verbo" (referindo-se ao mesmo conceito no texto), consolide no termo mais técnico ou frequente, deletando o redundante.
# 5. LIMPEZA DE OCR: Corrija termos como "Pa-dre" para "Padre" ou "Eglreja" para "Igreja".
# 6. HIERARQUIA DE SAÍDA: O JSON deve ser construído na ordem de importância teológica/narrativa. O termo que define o núcleo do argumento da página DEVE ser a primeira chave do objeto JSON.
# 7. TERMOS TÉCNICOS CONSOLIDADOS: O que for termo teológico/filosófico técnico consolidado da Patrística, deve ser mantido em latim ou grego transliterado latino.

# ### LÓGICA DE DECISÃO:
# - keep: O termo está perfeito e segue as diretrizes.
# - change: O termo existe, mas precisa de normalização, correção de grafia ou expansão.
# - delete: O termo é alucinação, não consta no texto, é uma paráfrase genérica ou é redundante.
# - merge: O termo foi absorvido por outro mais abrangente.

# ### FILTRO DE RUÍDO (IGNORAR SEMPRE):
# - Remova terminologias de coleções editoriais e referências de volume/coluna:
#   Ex: "Migne", "Patrologia Latina", "Patrologia Graeca", "PL", "PG", "Série Latina", "Série Grega".
# - Remova marcadores de numeração de página ou coluna do Migne:
#   Ex: "Col. 123", "Vol. 45", "Tomo VII", "Caput X".
# - Nota: Se o texto mencionar a "Vida de São Fulano escrita por Migne", remova o nome do editor (Migne) e mantenha apenas a entidade (São Fulano).

# ### FORMATO DE SAÍDA (JSON UTF-8 (com acentos e caracteres especiais) PURO):
# {
#   "terms": [
#     {
#       "original": "<termo original>",
#       "nota": "Breve justificativa técnica (máx 10 palavras)",
#       "decision": "keep | change | delete | merge",
#       "change_to": "Novo Termo ou Nome do Termo que o absorveu",
#       "is_canon_name": true/false
#     },
#     ...
#   ]
# }
# """.strip()

SYSTEM_PROMPT_VERIFICACAO = """\
Valide keywords_previa_json contra texto_original de acordo com indexação intelectual por facetas com warrant literário estrito em corpus patrístico.

Regras:
- Delete se o termo não estiver sustentado por texto_original, exceto tema teológico realmente explícito no conteúdo.
- Normalize nomes próprios para a forma canônica PT-BR quando inequívoca.
- Obras e referências bíblicas por extenso, sem abreviações. Capítulo e versículo separados por vírgula quando explicitamente citados no texto original.
- Não separar nome dos livros dos seus capítulos e versículos, devem ficar em mesmo valor JSON para fazer sentido.
- Confira referências bíblicas contra evidencia_citacoes_regex e texto_original. A regex é candidata, não autoridade; capítulo/versículo sem suporte deve ser deletado ou corrigido.
- Considere texto principal, notas e aparato crítico. Se houver fac-símile anexado, use-o apenas para resolver OCR/pontuação ambíguos.
- Se houver duplicatas semânticas, faça merge no termo mais técnico e estável.
- Preserve termos técnicos patrísticos consagrados em latim/grego transliterado.
- Delete ruído editorial e marcadores de edição: Migne, Patrologia Latina, Patrologia Graeca, PL, PG, série, tomo, volume, coluna, caput etc.
- Corrija OCR leve quando necessário.
- Corrija parênteses desbalanceados se necessário.

Retorne JSON puro:
{
  "terms": [
    {
      "original": "<escreva como está no json para automação localizar>",
      "nota": "",
      "decision": "keep | change | delete | merge",
      "change_to": "",
      "is_canon_name": true/false/null
    }
  ]
}
""".strip()


def replace_linebreak(text: str) -> str:
    # Captura hífen padrão, meia-risca (–) ou travessão (—)
    # seguido de espaços/quebras e remove também espaços no início da próxima linha
    return re.sub(r"[-–—]\s*[\r\n]+\s*", "", text)


def build_user_review_prompt(
    row: dict,
    keywords_originais: str,
    doc_name: str = "",
    scripture_evidence: dict[str, Any] | None = None,
) -> str:
    """
    Monta o prompt para o Revisor Crítico.
    Passa o texto limpo, o resumo (para contexto de temas) e as keywords prévias.
    """

    parts: List[str] = []

    # 1. Identificação do Contexto (Ajuda a identificar ruídos de coleção como Migne)
    if doc_name:
        parts.append(f"--- CONTEXTO: {doc_name} ---\n")

    evidence = scripture_evidence or collect_page_scripture_evidence(row)
    parts.append(_scripture_evidence_prompt_block(evidence))

    # 2. Texto Original (A âncora)
    parts.append("<texto_original>")
    parts.append(
        clean_ocr_text_optimized(replace_linebreak(row.get("pagina_texto") or ""))[0]
    )
    parts.append("</texto_original>\n")

    # 3. Resumo da Página (Essencial para validar a 'Exceção' de Temas Teológicos)
    parts.append("<resumo_volume>")
    resumo_global_limpo = clean_ocr_text_optimized(row.get("resumo_global") or "")[0]

    # Adiciona ao contexto autor e obra detectada se não estiver no resumo_global_limpo já
    if (
        "author_detected" in row
        and not row["author_detected"].lower() in resumo_global_limpo.lower()
    ):
        parts.append(f"Autor Detectado: {row['author_detected']}\n")
    if (
        "work_detected" in row
        and not row["work_detected"].lower() in resumo_global_limpo.lower()
    ):
        parts.append(f"Obra Detectada: {row['work_detected']}\n")

    parts.append(resumo_global_limpo)
    parts.append("</resumo_volume>\n")

    parts.append("<resumo_contextual>")
    parts.append(clean_ocr_text_optimized(row.get("resumo_pagina") or "")[0])
    parts.append("</resumo_contextual>\n")

    # 4. As keywords que precisam de auditoria
    parts.append("<keywords_previa_json>")
    parts.append(keywords_originais)
    parts.append("</keywords_previa_json>\n")

    parts.append(
        "TAREFA: Compare as keywords_previa_json com o texto_original. "
        "Use o resumo_contextual apenas para validar temas teológicos conceituais. "
        "Gere o JSON revisado respeitando a ordem de importância e as regras."
    )

    return "\n".join(parts)


def _merge_unbalanced_parentheses_in_list(items: list) -> list:
    """Une itens adjacentes apenas quando a concatenação resolve parênteses desequilibrados.

    Conservador: só opera em listas de strings e limita o número de concatenações
    consecutivas para evitar merges excessivos.
    """
    if not items:
        return items
    out: list = []

    def balanced(s: str) -> bool:
        bal = 0
        for ch in s:
            if ch == "(":
                bal += 1
            elif ch == ")":
                bal -= 1
                if bal < 0:
                    return False
        return bal == 0

    buf = None
    merges_limit = 3
    merges = 0

    for it in items:
        if not isinstance(it, str):
            # if non-string, flush buffer and append raw
            if buf is not None:
                out.append(buf)
                buf = None
                merges = 0
            out.append(it)
            continue

        s = it.strip()
        if buf is None:
            if "(" in s and not balanced(s):
                buf = s
                merges = 0
                continue
            else:
                out.append(s)
                continue

        # have buffer unbalanced
        candidate = (buf + " " + s).strip()
        merges += 1
        if balanced(candidate) or merges >= merges_limit:
            out.append(candidate)
            buf = None
            merges = 0
        else:
            buf = candidate

    if buf is not None:
        out.append(buf)

    return out


def _merge_unbalanced_parentheses_in_payload(obj: object) -> object:
    """Recursively aplica merge apenas em listas de strings dentro do payload JSON."""
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if isinstance(v, list) and all(isinstance(i, str) for i in v):
                new[k] = _merge_unbalanced_parentheses_in_list(v)
            else:
                new[k] = _merge_unbalanced_parentheses_in_payload(v)
        return new
    if isinstance(obj, list):
        if all(isinstance(i, str) for i in obj):
            return _merge_unbalanced_parentheses_in_list(obj)
        return [_merge_unbalanced_parentheses_in_payload(i) for i in obj]
    return obj


def _pretty_keywords_json(raw: str) -> str:
    """Deixa o JSON legível para o prompt; cai para string crua se não parsear."""
    if not raw or not raw.strip():
        return "[]"
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except Exception:
        return raw.strip()


def build_user_prompt(
    row: dict,
    source: str,
    doc_name: str,
    scripture_evidence: dict[str, Any] | None = None,
) -> str:
    """Monta o prompt de usuário com o conteúdo conforme --source."""

    # Cabeçalho técnico para o modelo se localizar
    # Mapeamento de siglas para nomes extensos
    MAPA_SERIES = {
        "PG": "Patrologia Graeca (Migne)",
        "PL": "Patrologia Latina (Migne)",
        "PO": "Patrologia Orientalis (Graffin/Nau)",
        "ACO": "Acta Conciliorum Oecumenicorum",  # Caso decidas expandir no futuro
    }

    # No build_user_prompt, extraímos o prefixo (ex: 'PG' de 'PG005')
    prefixo = "".join(re.findall(r"[A-Za-z]+", doc_name))
    serie_nome = MAPA_SERIES.get(prefixo, "Coleção Patrística")

    parts: List[str] = []
    parts.append(
        f"Documento: {doc_name} | Coleção: {serie_nome} | Página: {row['pagina_num']}\n"
    )
    evidence = scripture_evidence or collect_page_scripture_evidence(row)
    parts.append(_scripture_evidence_prompt_block(evidence))

    if source == "resumo_pagina":
        parts.append("<conteúdo>")
        parts.append(row["resumo_pagina"])
        parts.append("</conteúdo>\n")

    elif source == "resumo_global":
        parts.append("<conteúdo>")
        parts.append(row["resumo_global"])
        parts.append("</conteúdo>\n")

    elif source == "pagina_texto":
        parts.append("<conteúdo>")
        parts.append(replace_linebreak(row["pagina_texto"]))
        parts.append("</conteúdo>\n")

    elif source == "resumo+pagina":
        parts.append("<texto_original>")
        parts.append(replace_linebreak(row["pagina_texto"]))
        parts.append("</texto_original>\n")

        parts.append("<resumo_da_pagina>")
        parts.append(row["resumo_pagina"])
        parts.append("</resumo_da_pagina>\n")

    elif source == "tudo":
        parts.append("<resumo_global>")
        parts.append(row["resumo_global"])
        parts.append("</resumo_global>\n")

        parts.append("<resumo_da_pagina>")
        parts.append(row["resumo_pagina"])
        parts.append("</resumo_da_pagina>\n")

        parts.append("<texto_original>")
        parts.append(replace_linebreak(row["pagina_texto"]))
        parts.append("</texto_original>\n")

    parts.append(
        "Extraia os descriptors teológicos/filosóficos/históricos ordenados por importância argumentativa. Critério de inclusão: literary warrant estrito — o termo deve ter suporte explícito em texto_original. Prefira especificidade a exaustividade em casos ambíguos."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing da resposta
# ---------------------------------------------------------------------------

# Tipo estruturado retornado pelo parser
KeywordsResult = dict  # {"keywords": List[str], "categorias": Dict[str, List[str]]}


def _parse_numbered_keywords(raw: str) -> List[str]:
    """Extrai keywords de linhas numeradas: '1. keyword', '2. keyword', etc."""
    keywords: List[str] = []
    for m in re.finditer(r"^\s*\d+\.\s*(.+)$", raw, re.MULTILINE):
        kw = m.group(1).strip().rstrip(".")
        # Limpa parênteses explicativos do modelo: "Barnabas (Barnabé in Portuguese)"
        kw = re.sub(
            r"\s*\((?:since|the |Latin|Portuguese|but |most |key |mentioned).*\)\s*$",
            "",
            kw,
            flags=re.IGNORECASE,
        ).strip()
        if kw and len(kw) < 200:
            keywords.append(kw)
    return keywords


def _parse_numbered_keywords_fallback(raw: str) -> List[str]:
    """
    Fallback para respostas compactadas em uma única linha, ex.:
    "Keywords: 1.Atanásio;2.Epístola ...;3.Ariano ...4.Meleciano..."
    Captura blocos numerados mesmo sem novas linhas ou com separadores ';'.
    """
    raw_main = raw.split("Categorias", 1)[0]
    pattern = re.compile(
        r"\b\d+[.)]?\s*(.+?)(?=(?:\s*\d+[.)]|$))",
        re.DOTALL,
    )
    keywords: List[str] = []
    for m in pattern.finditer(raw_main):
        kw = m.group(1).strip().strip(";,:.-")
        kw = re.sub(r"\s+\(.{0,80}\)$", "", kw).strip()
        if kw and len(kw) < 200:
            keywords.append(kw)
    return keywords


def _parse_categories(raw: str) -> dict:
    """
    Extrai categorias da resposta. Resiliente a variações de formato:
      - Pessoas: X, Y, Z
      - **Pessoas**: X, Y, Z
      - Pessoas — X, Y, Z
      - Pessoas: X; Y; Z
    """
    categorias: dict = {}

    # Padrão flexível: "- LABEL:" ou "- **LABEL**:" seguido de itens
    cat_pattern = re.compile(
        r"^\s*[-•*]\s*\**\s*"  # bullet: -, •, *
        r"([\w\s\u00C0-\u024F]+?)"  # nome da categoria (unicode para acentos)
        r"\s*\**\s*[:—–\-]\s*"  # separador: :, —, –, -
        r"(.+)$",  # conteúdo
        re.MULTILINE,
    )

    for m in cat_pattern.finditer(raw):
        cat_name = m.group(1).strip().rstrip("*").strip()
        cat_content = m.group(2).strip()

        # Normaliza nome da categoria
        cat_name = cat_name.capitalize()

        # Ignora se parece ser uma keyword numerada acidentalmente capturada
        if re.match(r"^\d+\.", cat_name):
            continue

        # Vírgula entre algarismos pertence à citação ("Lucas 24,39").
        items = re.split(r"\s*;\s*|\s*,\s*(?!\d)", cat_content)
        items = [it.strip().strip("*").strip() for it in items if it.strip()]
        # Remove itens vazios ou muito longos (noise)
        items = [it for it in items if 1 < len(it) < 200]

        if items:
            categorias[cat_name] = items

    return categorias


def _strip_think_block(raw: str) -> str:
    """Remove blocos <think>...</think> da resposta (chain-of-thought vazado)."""
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


def _strip_json_code_fence(raw: str) -> str:
    """Remove um fence Markdown externo sem alterar JSON interno."""
    match = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*",
        raw or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else (raw or "").strip()


def _is_admin_or_blank_page(row: dict) -> bool:
    """
    Heurística leve para detectar páginas administrativas/lixo e evitar retries inúteis.
    Considera como admin se resumo_pagina ou resumo_global contiver "conteúdo administrativo"
    (case-insensitive) ou se todos os campos de texto estiverem vazios.
    """
    resumo_p = (row.get("resumo_pagina") or "").strip().lower()
    resumo_g = (row.get("resumo_global") or "").strip().lower()
    texto = (row.get("pagina_texto") or "").strip()

    if "conteúdo administrativo" in resumo_p or "conteúdo administrativo" in resumo_g:
        return True

    if not resumo_p and not resumo_g and not texto:
        return True

    return False


def parse_keywords_response(raw: str) -> Tuple[KeywordsResult, str]:
    """
    Extrai keywords e categorias da resposta do LLM.
    Retorna (resultado_estruturado, raw_completo).

    resultado_estruturado = {
        "keywords": ["kw1", "kw2", ...],
        "categorias": {
            "Pessoas": ["X", "Y"],
            "Obras": ["A", "B"],
            "Temas": ["T1", "T2"],
            "Termos técnicos": ["TT1", "TT2"],
        }
    }
    """
    # Remove blocos de raciocínio que o modelo pode vazar mesmo com think=false
    clean = _strip_json_code_fence(_strip_think_block(raw))

    # Tenta parsing direto de JSON no formato esperado do system prompt
    try:
        data = json.loads(clean)
    except Exception:
        data = None

    if isinstance(data, dict):
        kws_from_json: List[str] = []
        if isinstance(data.get("keywords_ranking"), list) and data.get(
            "keywords_ranking"
        ):
            kws_from_json = [
                kw for kw in data["keywords_ranking"] if isinstance(kw, str)
            ]
        elif isinstance(data.get("keywords"), list):
            kws_from_json = [kw for kw in data["keywords"] if isinstance(kw, str)]

        categorias = data.get("categorias")
        if isinstance(categorias, dict):
            categorias = {k: v for k, v in categorias.items() if isinstance(v, list)}
        else:
            categorias = {}

        result: KeywordsResult = {"keywords": kws_from_json}
        if kws_from_json:
            # preserva campo novo para quem consome o JSON cru
            result["keywords_ranking"] = kws_from_json
        if categorias:
            result["categorias"] = categorias

        if data.get("notas"):
            result["notas"] = data["notas"]
        return result, clean

    if isinstance(data, list):
        kws_from_json = [kw for kw in data if isinstance(kw, str)]
        return {"keywords": kws_from_json}, clean

    keywords = _parse_numbered_keywords(clean)
    if not keywords:
        keywords = _parse_numbered_keywords_fallback(clean)
    else:
        extra = _parse_numbered_keywords_fallback(clean)
        for kw in extra:
            if kw not in keywords:
                keywords.append(kw)
    categorias = _parse_categories(clean)

    result: KeywordsResult = {
        "keywords": keywords,
    }
    if categorias:
        result["categorias"] = categorias

    return result, raw


# ---------------------------------------------------------------------------
# Judge parser (SYSTEM_PROMPT_VERIFICACAO)
# ---------------------------------------------------------------------------


def parse_judge_response(raw: str) -> Tuple[List[dict], List[str]]:
    """
    Extrai decisões do LLM-judge.
    Retorna (decisions, issues). Mantém ordem recebida.
    decisions[i] = {
        "original": str,
        "decision": keep|change|delete|merge,
        "change_to": str|None,
        "nota": str|None,
        "is_canon_name": bool|None,
    }
    """
    issues: List[str] = []
    if not raw or not raw.strip():
        return [], ["empty_response"]

    cleaned = _strip_json_code_fence(_strip_think_block(raw))

    # Rejeita respostas com caracteres de controle (ex.: \u0000, \u007f) para forçar retry
    if re.search(
        r"\\u00(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7f)", cleaned, flags=re.IGNORECASE
    ) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", cleaned):
        return [], ["invalid_control_chars"]

    try:
        data = json.loads(cleaned)
    except Exception:
        return [], ["invalid_json"]

    # Normaliza estruturas possíveis
    terms: List[dict] = []
    if isinstance(data, dict):
        if "terms" in data and isinstance(data["terms"], list):
            terms = data["terms"]
        else:
            # formato {"Termo": {...}}
            for key, val in data.items():
                if isinstance(val, dict):
                    term = {"original": key}
                    term.update(val)
                    terms.append(term)
    elif isinstance(data, list):
        terms = data
    else:
        return [], ["unsupported_root_type"]

    if not isinstance(terms, list):
        return [], ["terms_not_list"]

    normalized: List[dict] = []
    seen_originals: set[str] = set()
    allowed = {"keep", "change", "delete", "merge"}

    for idx, item in enumerate(terms):
        if not isinstance(item, dict):
            issues.append(f"item_not_object[{idx}]")
            continue

        original = item.get("original") or item.get("term") or item.get("keyword")
        if not original or not isinstance(original, str):
            issues.append(f"missing_original[{idx}]")
            continue

        decision_raw = item.get("decision") or ""
        decision = str(decision_raw).strip().lower()
        if decision not in allowed:
            issues.append(f"decision_invalid[{original}]")
            decision = "keep"  # fallback seguro

        change_to = item.get("change_to")
        if change_to is not None and not isinstance(change_to, str):
            change_to = str(change_to)

        nota = item.get("nota")
        if nota is not None and not isinstance(nota, str):
            nota = str(nota)

        is_canon = item.get("is_canon_name")
        if is_canon is not None:
            is_canon = bool(is_canon)

        if original in seen_originals:
            issues.append(f"duplicate_original[{original}]")
            # mantém apenas a primeira ocorrência
            continue
        seen_originals.add(original)

        normalized.append(
            {
                "original": _deep_clean_keyword(original),
                "decision": decision,
                "change_to": _deep_clean_keyword(change_to) if change_to else None,
                "nota": nota,
                "is_canon_name": is_canon,
            }
        )

        if decision in {"change", "merge"} and not change_to:
            issues.append(f"missing_change_to[{original}]")

    return normalized, sorted(set(issues))


# ---------------------------------------------------------------------------
# Validação e limpeza de keywords existentes
# ---------------------------------------------------------------------------


def normalize_keywords_payload(raw: str) -> Tuple[KeywordsResult, Optional[str]]:
    """
    Converte keywords_json em estrutura padrão {"keywords": [...], "categorias": {...}}
    e sinaliza problemas de parsing.
    """
    if not raw or not raw.strip():
        return {"keywords": []}, "missing_keywords_json"

    if _contains_control_chars(raw):
        return {"keywords": []}, "invalid_control_chars_existing"

    try:
        data = json.loads(raw)
    except Exception:
        return {"keywords": []}, "invalid_json"

    result: KeywordsResult = {}
    parse_issue: Optional[str] = None

    if isinstance(data, list):
        result["keywords"] = [
            _deep_clean_keyword(kw) for kw in data if isinstance(kw, str)
        ]
        result["keywords"] = [kw for kw in result["keywords"] if kw]
        parse_issue = "legacy_list_format"
        return result, parse_issue

    if isinstance(data, dict):
        if "keywords" in data:
            kws = data.get("keywords")
        else:
            kws = data.get("keywords_ranking")
        if isinstance(kws, list):
            clean_kws = [_deep_clean_keyword(kw) for kw in kws if isinstance(kw, str)]
            clean_kws = [kw for kw in clean_kws if kw]
            result["keywords"] = clean_kws
            # preserva campo novo para quem ainda lê keywords_ranking
            if data.get("keywords_ranking"):
                result["keywords_ranking"] = clean_kws
        else:
            result["keywords"] = []
            parse_issue = "missing_keywords_field"

        cats = data.get("categorias") or data.get("categories")
        if isinstance(cats, dict):
            clean_cats = {}
            for name, items in cats.items():
                if not isinstance(items, list):
                    continue
                clean_items = [
                    _deep_clean_keyword(it) for it in items if isinstance(it, str)
                ]
                clean_items = [it for it in clean_items if it]
                if clean_items:
                    clean_cats[name] = clean_items
            if clean_cats:
                result["categorias"] = clean_cats

        notes = _clean_keyword_notes(data.get("notas") or data.get("notes"))
        if notes:
            result["notas"] = notes

        return result, parse_issue

    return {"keywords": []}, "unexpected_structure"


def _strip_markdown_wrappers(text: str) -> str:
    """Remove bullets/ênfase/code simples que podem envolver a keyword."""
    return strip_markdown_wrappers(text)


def _deep_clean_keyword(text: str) -> str:
    """Normalização agressiva para keywords vindas do LLM (quotes/ênfase/whitespace)."""
    cleaned, _issues = clean_keyword_text(text)
    return cleaned


def _clean_keyword_notes(raw_notes: Any) -> dict[str, str]:
    """Preserva notas como metadado não pesquisável, sem caracteres invisíveis."""
    if not isinstance(raw_notes, dict):
        return {}
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in raw_notes.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            continue
        key = _deep_clean_keyword(raw_key)
        value = unicodedata.normalize("NFKC", raw_value)
        value, _removed_format = remove_unicode_format_chars(value)
        value = re.sub(r"\s+", " ", value).strip()
        if key and value:
            cleaned[key] = value
    return cleaned


def _contains_control_chars(text: str) -> bool:
    """Detecta controles ASCII (0x00-0x1F e 0x7F) ou escapes \\u00xx."""
    if not text:
        return False
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        return True
    if re.search(r"\\u00(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7f)", text, flags=re.IGNORECASE):
        return True
    return False


def _split_conjoined_keywords(
    raw_text: str, cleaned_text: str
) -> Tuple[List[str], bool]:
    """
    Split desativado para evitar falsos positivos em nomes compostos.
    Mantém assinatura para compatibilidade, mas devolve item único.
    """
    return [cleaned_text], False


def _clean_list(items: List[str]) -> Tuple[List[str], List[str]]:
    """Normaliza lista de strings removendo duplicatas, espaços e itens inválidos."""
    issues: set = set()
    cleaned: List[str] = []
    seen = set()

    items = _merge_unbalanced_parentheses_in_payload(items)

    for raw in items:
        if not isinstance(raw, str):
            issues.add("removed_non_string")
            continue

        sanitized = sanitize_keyword_item(raw)
        issues.update(sanitized.issues)
        kw = sanitized.value
        if not kw:
            continue

        parts, did_split = _split_conjoined_keywords(raw, kw)
        if did_split:
            issues.add("split_conjoined_keywords")

        for part in parts:
            if len(part) > MAX_KEYWORDS_LENGTH:
                issues.add("removed_too_long")
                continue

            # descarta tokens sem nenhuma letra (evita ".1,5.", "123", "....")
            if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿΑ-ωα-ω]", part):
                issues.add("removed_non_alpha")
                continue

            key = keyword_normalization_key(part)
            if key in seen:
                issues.add("removed_duplicate")
                continue
            seen.add(key)
            cleaned.append(part)

    return cleaned, sorted(issues)


def has_mixed_scripts(text: str) -> bool:
    # Detecta mistura de letras latinas e gregas na mesma palavra
    pattern = r"\b(?=[^\s]*[a-zA-Z])(?=[^\s]*[\u0370-\u03FF])[^\s]+\b"
    matches = re.findall(pattern, text)
    if matches:
        print(f"Alerta: Palavras corrompidas detectadas: {matches}")
        return True
    return False


def has_unbalanced_parentheses(text: str) -> bool:
    balance = 0
    for ch in text:
        if ch == "(":
            balance += 1
        elif ch == ")":
            balance -= 1
            if balance < 0:
                return True
    return balance != 0


def looks_like_reference_keyword(text: str) -> bool:
    """
    Heurística para referências abreviadas (ps. l, 7 / xxvii, 9 / fol. 55, r a).
    Considera ruim strings curtas compostas só de abreviações, algarismos romanos
    e números. Citações bíblicas reconhecidas pelo normalizer não entram aqui.
    """
    if not text:
        return False

    if extract_citations_from_value_cached(
        text,
        source_kind="keywords",
        source_path="heuristic",
        support_mode=False,
    ):
        # use cached wrapper to avoid repeated heavy parsing
        return False

    if _normalize_ambiguous_bible_reference(text):
        return False

    raw = text.lower()
    raw_abbrev_dot = bool(re.search(r"\b[a-z]{1,6}\.", raw))

    norm = raw.strip()
    norm = re.sub(r"[.,;:]+", " ", norm)
    tokens = [t for t in re.split(r"\s+", norm) if t]

    if not tokens or len(tokens) > 7:
        return False

    abbr = roman = numeric = singles = 0
    for tok in tokens:
        if re.fullmatch(r"[a-z]{1,4}", tok):
            abbr += 1
            continue
        if re.fullmatch(r"[ivxlcdm]+", tok):
            roman += 1
            continue
        if re.fullmatch(r"\d{1,3}(?:-\d{1,3})?", tok):
            numeric += 1
            continue
        if re.fullmatch(r"[a-z]", tok):
            singles += 1
            continue
        return False

    if raw_abbrev_dot and len(text) <= 40:
        print(f"Alerta: referência abreviada detectada: {text}")
        return True

    result = (roman >= 1) or (
        abbr + numeric + singles >= 2 and roman + numeric + abbr >= 1
    )

    if result:
        print(f"Alerta: possível referência abreviada detectada: {text}")

    return result


# Padrões proibidos explícitos
FORBIDDEN_KEYWORD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^caput\s+(?:[ivxlcdm]+|\d{1,3})$", flags=re.IGNORECASE),
        "keyword_forbidden_caput_number",
    ),
]


def clean_keywords_structure(
    result: KeywordsResult,
) -> Tuple[KeywordsResult, List[str]]:
    """Aplica limpeza leve nas keywords/categorias e devolve issues encontradas."""
    issues: List[str] = []

    kws = result.get("keywords") or []
    cleaned_kws, kw_issues = _clean_list(kws)
    issues.extend(kw_issues)

    cleaned: KeywordsResult = {"keywords": cleaned_kws}

    cats = result.get("categorias") or {}
    if isinstance(cats, dict):
        clean_cats = {}
        for name, items in cats.items():
            if not isinstance(items, list):
                issues.append("category_not_list")
                continue
            cleaned_items, cat_issues = _clean_list(items)
            issues.extend(cat_issues)
            if cleaned_items:
                clean_cats[name] = cleaned_items
        if clean_cats:
            cleaned["categorias"] = clean_cats

    notes = _clean_keyword_notes(result.get("notas"))
    if notes:
        cleaned["notas"] = notes

    cleaned, citation_issues = extract_citations(cleaned)
    issues.extend(citation_issues)

    if notes:
        cleaned["notas"] = notes

    # Normaliza duplicatas de issues
    issues = sorted(set(issues))
    return cleaned, issues


GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
LATIN_RE = re.compile(r"[A-Za-z]")


def _doc_language_hint(doc_name: Optional[str], keywords: List[str]) -> Optional[str]:
    """
    Heurística: prioriza detecção por caracteres nas próprias keywords.
    - Se houver qualquer caractere grego, assume grego.
    - Se houver grego E latim, sinaliza multi (sem viés; decide por token).
    - Se houver apenas letras latinas, força latim apenas para docs PL; caso contrário deixa o detector por token decidir.
    - Caso contrário, cai para heurística por prefixo do documento.
    """
    concat = " ".join(keywords)
    has_greek_chars = bool(GREEK_RE.search(concat))
    has_latin_chars = bool(LATIN_RE.search(concat))

    if has_greek_chars and has_latin_chars:
        return "multi"
    if has_greek_chars:
        return "grc"
    if has_latin_chars and not has_greek_chars:
        if doc_name and str(doc_name).upper().startswith("PL"):
            return "lat"
        return None

    if doc_name:
        m = re.match(r"[A-Za-z]+", str(doc_name))
        prefix = m.group(0).upper() if m else ""
        if prefix.startswith("PG"):
            return "grc"
        if prefix.startswith("PL"):
            return "lat"
        if prefix.startswith("PO"):
            return "multi"  # multilingue, deixa detector decidir
    return None


def _integrity_http_batch(
    keywords: List[str], language_hint: Optional[str]
) -> List[ValidationEvidence]:
    """Consulta o serviço HTTP; em falha propaga exceção para ser tratada acima."""
    import urllib.request
    import urllib.error

    if not INTEGRITY_HTTP_URL:
        return []

    payload = {
        "terms": keywords,
        "language_hint": language_hint,
        "is_canon_flags": None,
    }
    req = urllib.request.Request(
        INTEGRITY_HTTP_URL.rstrip("/") + "/batch",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=INTEGRITY_HTTP_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"integrity_http_status_{resp.status}")
        data = json.loads(resp.read().decode("utf-8"))

    evidences: List[ValidationEvidence] = []
    for ev in data:
        status_str = ev.get("status", "UNKNOWN")
        try:
            status = IntegrityStatus(status_str)
        except Exception:
            status = IntegrityStatus.UNKNOWN
        evidences.append(
            ValidationEvidence(
                raw_term=ev.get("raw_term", ""),
                normalized_term=ev.get("normalized_term", ev.get("raw_term", "")),
                status=status,
                reasons=ev.get("reasons", []) or [],
                lemma=ev.get("lemma"),
                matched_vocab=ev.get("matched_vocab", False),
                matched_stopword=ev.get("matched_stopword", False),
                control_chars_found=ev.get("control_chars_found", False),
                script_flags=ev.get("script_flags", {}) or {},
                confidence=ev.get("confidence", 0.0),
                language=ev.get("language", "unknown"),
            )
        )
    return evidences


def recursive_unbalanced_parentheses_check(cleaned: dict) -> bool:
    """
    Verifica recursivamente se há parênteses desequilibrados.
    """
    for key in cleaned.keys():
        value = cleaned.get(key, [])

        if isinstance(value, list) and any(
            has_unbalanced_parentheses(kw) for kw in value
        ):
            return True
        elif isinstance(value, str) and has_unbalanced_parentheses(value):
            return True
        elif isinstance(value, dict):
            if recursive_unbalanced_parentheses_check(value):
                return True

    return False


def dict_recursive_callback(
    cleaned: list | dict | str, funcb: callable, ref: str = ""
) -> None:
    if isinstance(cleaned, dict):
        for key, value in cleaned.items():
            if isinstance(value, list):
                for item in value:
                    dict_recursive_callback(
                        item, funcb, ref=f"{ref}.{key}" if ref else key
                    )
            elif isinstance(value, dict):
                dict_recursive_callback(
                    value, funcb, ref=f"{ref}.{key}" if ref else key
                )
            else:
                if not funcb(key, value):
                    return
    elif isinstance(cleaned, list):
        for item in cleaned:
            dict_recursive_callback(item, funcb, ref=ref)
    else:
        if not funcb(ref, cleaned):
            return


def validate_keywords(
    cleaned: KeywordsResult, parse_issue: Optional[str], doc_name: Optional[str] = None
) -> List[str]:
    """Valida lista de keywords já limpa; retorna lista de issues."""
    issues: List[str] = []
    kw_count = len(cleaned.get("keywords", []))

    if parse_issue:
        issues.append(parse_issue)

    if kw_count == 0:
        issues.append("empty_keywords")
    elif kw_count < RECOMMENDED_MIN_KEYWORDS:
        issues.append(f"few_keywords({kw_count})")
    elif kw_count > RECOMMENDED_MAX_KEYWORDS + 7:  # tolera até +7 como outlier extremo
        issues.append(f"many_keywords({kw_count})")

    # if any(len((kw or "").split()) >= 9 for kw in cleaned.get("keywords", [])):
    #     issues.append("keyword_looks_sentence")

    if any("a lista " in (kw or "") for kw in cleaned.get("keywords", [])):
        issues.append("keyword_contains_lista")

    if any("palavras-chave" in (kw or "") for kw in cleaned.get("keywords", [])):
        issues.append("keyword_contains_palavras-chave")

    if any((len(kw) > 80) for kw in cleaned.get("keywords", [])):
        issues.append("keyword_long")

    def keyword_long_cb(k, v):
        if len(v) > 70:
            issues.append("keyword_long")
            # Interrompe o loop recursivo
            return False

        return True

    # Recursive keyword_long check
    dict_recursive_callback(cleaned.get("keywords", []), keyword_long_cb)

    # Verifica por misturas de latim e grego: has_mixed_scripts
    if any(has_mixed_scripts(kw) for kw in cleaned.get("keywords", [])):
        issues.append("keyword_mixed_scripts")

    if recursive_unbalanced_parentheses_check(cleaned):
        issues.append("keyword_unbalanced_parentheses")

    # if any(looks_like_reference_keyword(kw) for kw in cleaned.get("keywords", [])):
    #    issues.append("keyword_probable_reference")

    for kw in cleaned.get("keywords", []):
        for pattern, code in FORBIDDEN_KEYWORD_PATTERNS:
            if pattern.match(kw or ""):
                issues.append(code)
                break

    # Flag números isolados virando keywords (ex.: "4")
    if any(
        re.fullmatch(r"\d+[.,]?", (kw or "").strip())
        for kw in cleaned.get("keywords", [])
    ):
        issues.append("keyword_isolated_number")

    # Linguistic integrity (HTTP service preferido; fallback local). Fail-open on errors.
    if INTEGRITY_ENABLED:
        evidences: List[ValidationEvidence] = []
        kws = cleaned.get("keywords", [])
        try:
            doc_hint = _doc_language_hint(doc_name, kws)
            if INTEGRITY_HTTP_URL:
                evidences = _integrity_http_batch(kws, doc_hint)
            else:
                log.warning("Integrity skipped: KW_INTEGRITY_HTTP_URL not set")

            suspect_langs: set[str] = set()
            stopword_langs: set[str] = set()
            for ev in evidences:
                lang = ev.language or "unknown"
                if ev.status in {IntegrityStatus.SUSPECT, IntegrityStatus.NOISE}:
                    suspect_langs.add(lang)
                    log.debug(
                        "Integrity suspect/noise (doc %s) term found: %s", doc_name, ev
                    )
                if ev.status == IntegrityStatus.STOPWORD:
                    stopword_langs.add(lang)

            if suspect_langs:
                issues.append(
                    f"integrity_suspect_terms({'+'.join(sorted(suspect_langs))})"
                )
            if stopword_langs:
                issues.append(
                    f"integrity_contains_stopwords({'+'.join(sorted(stopword_langs))})"
                )

            if INTEGRITY_DEBUG and evidences:
                log.debug("Integrity evidences: %s", evidences)
        except Exception as exc:  # pragma: no cover — defensive
            log.error(f"Integrity checker failed (fail-open): {exc}")
            # fail-open: não adiciona issue de erro

    return issues


# ---------------------------------------------------------------------------
# Bíblia — normalização de citações dentro do próprio keywords_json
# ---------------------------------------------------------------------------


def _normalized_category_key(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text or ""))
    ascii_approx = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", "_", ascii_approx).strip().casefold()


def _find_bible_works_category_key(categorias: dict) -> str | None:
    """Retorna a primeira chave de categoria que pode conter citações bíblicas."""
    keys = _find_all_bible_category_keys(categorias)
    return keys[0] if keys else None


def _find_all_bible_category_keys(categorias: dict) -> list[str]:
    """Retorna *todas* as chaves de categorias que podem conter citações bíblicas."""
    if not isinstance(categorias, dict):
        return []
    target_keys = {
        "obras_citadas",
        "obras",
        "works",
        "works_cited",
        "termos_tecnicos",
        "technical_terms",
        "termos_tecnicos_lat_gr",
    }
    result: list[str] = []
    for key, value in categorias.items():
        if not isinstance(value, list):
            continue
        if _normalized_category_key(key) in target_keys:
            result.append(key)
    return result


_AMBIGUOUS_BIBLE_REF_RE = re.compile(
    r"^\s*(?P<book>(?:[1-3IVXivx]{1,3}\s+)?(?:cor|tm|ts|pe))\s+"
    r"(?P<chapter>\d{1,3})\s*[:.]\s*(?P<verse>\d{1,3}(?:-\d{1,3})?)\s*$",
    re.IGNORECASE,
)


def _normalize_ambiguous_bible_reference(text: str) -> str | None:
    match = _AMBIGUOUS_BIBLE_REF_RE.fullmatch(text or "")
    if not match:
        return None
    raw_book = re.sub(r"\s+", " ", match.group("book")).strip()
    tokens = raw_book.split()
    pretty_tokens: list[str] = []
    for token in tokens:
        if token.isdigit():
            pretty_tokens.append(token)
            continue
        if re.fullmatch(r"[ivx]+", token, re.IGNORECASE):
            pretty_tokens.append(token.upper())
            continue
        pretty_tokens.append(token[:1].upper() + token[1:].lower())
    book = " ".join(pretty_tokens)
    return f"{book} {match.group('chapter')},{match.group('verse')}"


def _comparison_keys(rec: dict) -> set[tuple[int, int | None, str | None]]:
    keys = {(rec.get("book_idx"), rec.get("number"), rec.get("verse"))}
    if rec.get("verse") is None and rec.get("alt_number") is not None:
        keys.add((rec.get("book_idx"), rec.get("alt_number"), None))
    return keys


def _citation_covers_whole_item(item: str, rec: dict) -> bool:
    raw = _deep_clean_keyword(item)
    frag = _deep_clean_keyword(rec.get("raw", ""))
    return bool(raw and frag and raw.casefold() == frag.casefold())


def _record_matches_anchor(anchor: dict, candidate: dict) -> bool:
    if _comparison_keys(anchor) & _comparison_keys(candidate):
        return True
    # original behaviour: if anchor has no number, allow candidate with same book_idx
    return anchor.get("number") is None and candidate.get("book_idx") == anchor.get(
        "book_idx"
    )


def _full_item_citation_record(item: str, records: List[dict]) -> dict | None:
    for rec in records:
        if _citation_covers_whole_item(item, rec):
            return rec
    return None


def _dedupe_after_citation_normalization(items: List[str]) -> tuple[List[str], bool]:
    deduped = _dedupe_preserve_order(items)
    return deduped, len(deduped) != len(items)


def extract_citations(payload: dict) -> tuple[KeywordsResult, list[str]]:
    """Normaliza citações bíblicas em todas as categorias bíblicas e keywords com âncoras seguras."""
    if not isinstance(payload, dict):
        return {"keywords": []}, []

    categorias = payload.get("categorias") if isinstance(payload, dict) else {}
    bible_keys = (
        _find_all_bible_category_keys(categorias)
        if isinstance(categorias, dict)
        else []
    )
    keywords_list = payload.get("keywords") if isinstance(payload, dict) else []
    if not isinstance(keywords_list, list):
        keywords_list = []

    issues: list[str] = []
    anchors: list[dict] = []
    seen_anchor: set[tuple[int, int | None, str | None]] = set()

    # Processa todas as categorias que podem conter citações bíblicas
    normalized_cats: dict[str, list[str]] = {}
    for cat_key in bible_keys:
        cat_items = categorias.get(cat_key) if isinstance(categorias, dict) else []
        if not isinstance(cat_items, list):
            continue
        cat_kind = _normalized_category_key(cat_key)
        normalized_items: list[str] = []
        for idx, raw in enumerate(cat_items):
            if not isinstance(raw, str):
                continue
            records = extract_citations_from_value_cached(
                raw,
                source_kind=cat_kind,
                source_path=f"categorias.{cat_kind}[{idx}]",
                support_mode=False,
            )
            normalized_value = raw
            full_item_record = _full_item_citation_record(raw, records)
            if full_item_record:
                normalized_value = full_item_record["normalized"]
            else:
                ambiguous_normalized = _normalize_ambiguous_bible_reference(raw)
                if ambiguous_normalized:
                    normalized_value = ambiguous_normalized
            normalized_items.append(normalized_value)

            for rec in records:
                key = (rec["book_idx"], rec.get("number"), rec.get("verse"))
                if key in seen_anchor:
                    continue
                seen_anchor.add(key)
                anchors.append(rec)

        if normalized_items != cat_items:
            issues.append("normalized_bible_citation")
        normalized_items, removed_dup = _dedupe_after_citation_normalization(
            normalized_items
        )
        if removed_dup:
            issues.append("removed_duplicate")
        normalized_cats[cat_key] = normalized_items

    normalized_keywords: list[str] = []
    for idx, raw in enumerate(keywords_list):
        if not isinstance(raw, str):
            continue
        records = extract_citations_from_value_cached(
            raw,
            source_kind="keywords",
            source_path=f"keywords[{idx}]",
            support_mode=True,
        )
        normalized_value = raw
        full_item_record = _full_item_citation_record(raw, records)
        if full_item_record:
            rec = full_item_record
            # Only apply normalization from anchors when anchor and candidate
            # share explicit comparison keys (book + chapter/verse). This
            # prevents expanding short abbreviations in keywords when the
            # categorias only contain book-level anchors (e.g. 'Gênesis').
            from scripture_ref_normalizer import is_book_abbreviation_token

            matched_anchors = [a for a in anchors if _record_matches_anchor(a, rec)]
            if matched_anchors:
                # Apply normalization from anchors: expand abbreviations to
                # canonical book names (this covers short forms, Latin and
                # Greek variants that are present in the index).
                normalized_value = rec["normalized"]
        else:
            ambiguous_normalized = _normalize_ambiguous_bible_reference(raw)
            if ambiguous_normalized:
                normalized_value = ambiguous_normalized
        normalized_keywords.append(normalized_value)

    if normalized_keywords != keywords_list:
        issues.append("normalized_bible_citation")
    normalized_keywords, removed_kw_dup = _dedupe_after_citation_normalization(
        normalized_keywords
    )
    if removed_kw_dup:
        issues.append("removed_duplicate")

    cleaned: KeywordsResult = {"keywords": normalized_keywords}
    clean_cats: dict[str, list[str]] = {}
    bible_keys_set = set(bible_keys)
    if isinstance(categorias, dict):
        for name, items in categorias.items():
            if name in bible_keys_set:
                norm = normalized_cats.get(name, [])
                if norm:
                    clean_cats[name] = norm
                continue
            if isinstance(items, list) and items:
                clean_cats[name] = items
    if clean_cats:
        cleaned["categorias"] = clean_cats
    return cleaned, sorted(set(issues))


def _apply_decision_to_item(
    item: str, decision: dict, issues: List[str]
) -> Optional[str]:
    """Aplica decisão a um item único; retorna novo valor ou None (delete)."""
    dec = decision.get("decision")
    change_to = decision.get("change_to")
    original = decision.get("original", "")

    if dec == "delete":
        return None
    if dec == "change":
        if not change_to:
            issues.append(f"change_without_target[{original}]")
            return item
        if _deep_clean_keyword(change_to) == _deep_clean_keyword(item):
            # keep silencioso: trata como keep, não registra issue
            return item
        return change_to
    if dec == "merge":
        if not change_to:
            issues.append(f"merge_without_target[{original}]")
            return item
        if _deep_clean_keyword(change_to) == _deep_clean_keyword(item):
            # keep silencioso: trata como keep, não registra issue
            return item
        return change_to
    # keep ou fallback
    return item


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for it in items:
        key = it.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def apply_judge_decisions(
    existing_json_str: str,
    decisions: List[dict],
    *,
    order_from_judge: bool = True,
    include_categories: bool = True,
) -> Tuple[KeywordsResult, dict]:
    """
    Aplica decisões do judge sobre keywords_json existente.
    Retorna (novo_struct, report).
    report: {changes, removed, merged, issues: [...]}.
    """
    base, parse_issue = normalize_keywords_payload(existing_json_str)
    report: dict = {
        "changes": 0,
        "removed": 0,
        "merged": 0,
        "issues": [],
    }
    if parse_issue:
        report["issues"].append(parse_issue)

    if not base.get("keywords"):
        report["issues"].append("no_keywords_in_existing")
        return base, report

    decision_map: dict[str, dict] = {}
    for d in decisions:
        orig = d.get("original")
        if not orig:
            continue
        if orig in decision_map:
            report["issues"].append(f"duplicate_original_decision[{orig}]")
            continue
        decision_map[orig] = d

    orig_keywords: List[str] = base.get("keywords", [])
    touched: set[str] = set()

    new_keywords: List[str] = []

    if order_from_judge:
        for d in decisions:
            orig = d.get("original")
            if orig not in orig_keywords:
                continue
            touched.add(orig)
            new_val = _apply_decision_to_item(orig, d, report["issues"])
            if new_val is None:
                report["removed"] += 1
                continue
            if d.get("decision") == "merge":
                report["merged"] += 1
            if new_val != orig:
                report["changes"] += 1
            if new_val in new_keywords:
                # auto-merge: mantém ordem do primeiro, não duplica nem loga issue
                if d.get("decision") == "merge" or d.get("decision") == "change":
                    report["merged"] += 1
                    report["removed"] += 1
                continue
            new_keywords.append(new_val)

        # adiciona keywords não tocadas mantendo ordem original
        for kw in orig_keywords:
            if kw in touched:
                continue
            if kw in new_keywords:
                # já presente; mantém ordem do primeiro
                continue
            new_keywords.append(kw)
    else:
        for kw in orig_keywords:
            d = decision_map.get(kw)
            if d:
                touched.add(kw)
                new_val = _apply_decision_to_item(kw, d, report["issues"])
                if new_val is None:
                    report["removed"] += 1
                    continue
                if d.get("decision") == "merge":
                    report["merged"] += 1
                if new_val != kw:
                    report["changes"] += 1
                if new_val in new_keywords:
                    if d.get("decision") == "merge" or d.get("decision") == "change":
                        report["merged"] += 1
                        report["removed"] += 1
                    continue
                new_keywords.append(new_val)
            else:
                new_keywords.append(kw)

    new_keywords = _dedupe_preserve_order(new_keywords)

    # Aplica a categorias
    new_categories: dict = {}
    cats = base.get("categorias") if include_categories else {}
    if isinstance(cats, dict):
        for cat, items in cats.items():
            if not isinstance(items, list):
                continue
            updated_items: List[str] = []
            for it in items:
                d = decision_map.get(it)
                if d:
                    new_val = _apply_decision_to_item(it, d, report["issues"])
                    if new_val is None:
                        report["removed"] += 1
                        continue
                    if d.get("decision") == "merge":
                        report["merged"] += 1
                    if new_val != it:
                        report["changes"] += 1
                    updated_items.append(new_val)
                else:
                    updated_items.append(it)
            updated_items = _dedupe_preserve_order(updated_items)
            if updated_items:
                new_categories[cat] = updated_items

    cleaned, clean_issues = clean_keywords_structure(
        {
            "keywords": new_keywords,
            **({"categorias": new_categories} if new_categories else {}),
        }
    )
    report["issues"].extend(clean_issues)
    report["issues"] = sorted(set(report["issues"]))

    # copia keywords_ranking para manter contrato
    cleaned["keywords_ranking"] = cleaned.get("keywords", [])

    return cleaned, report


def verify_documents(
    con: sqlite3.Connection,
    docs: List[str],
    *,
    apply_fix: bool = False,
    rerun_bad: bool = False,
    process_params: Optional[dict] = None,
    page: Optional[int] = None,
    limit: Optional[int] = None,
    provider: str | None = None,
    model: str | None = None,
    report_output: TextIO | None = None,
    check_scripture_warrant: bool = False,
) -> dict[str, int]:
    """Valida keywords já gravadas e, opcionalmente, aplica correções leves."""

    total_pages = 0
    total_issue_pages = 0
    total_fixes = 0
    total_reruns = 0

    for doc in docs:
        rows = fetch_pages(con, doc, page=page, limit=limit)
        if not rows:
            log.warning("[%s] Nenhuma página encontrada para validar", doc)
            continue

        doc_counts: List[int] = []
        page_reports = []

        for row in rows:
            pnum = row["pagina_num"]
            raw_kw = row.get("keywords_json", "")

            parsed, parse_issue = normalize_keywords_payload(raw_kw)
            cleaned, clean_issues = clean_keywords_structure(parsed)
            issues = clean_issues + validate_keywords(
                cleaned, parse_issue, doc_name=doc
            )
            if check_scripture_warrant:
                scripture_evidence = collect_page_scripture_evidence(row)
                issues.extend(validate_scripture_warrant(cleaned, scripture_evidence))

            page_reports.append(
                {
                    "pagina": pnum,
                    "count": len(cleaned.get("keywords", [])),
                    "issues": set(issues),
                    "cleaned": cleaned,
                    "parsed": parsed,
                    "parse_issue": parse_issue,
                    "source": row.get("keywords_source", ""),
                    "modelo": row.get("keywords_modelo", ""),
                    "row": row,
                }
            )
            doc_counts.append(len(cleaned.get("keywords", [])))

        doc_median = median(doc_counts) if doc_counts else 0
        doc_avg = mean(doc_counts) if doc_counts else 0

        # Marca outliers por doc
        if doc_median:
            low_threshold = max(2, int(doc_median * OUTLIER_LOW_RATIO))
            high_threshold = max(
                RECOMMENDED_MAX_KEYWORDS + 7, int(doc_median * OUTLIER_HIGH_RATIO)
            )
            for rep in page_reports:
                if rep["count"] < low_threshold:
                    rep["issues"].add(f"outlier_low_vs_doc(med={doc_median:.1f})")
                if rep["count"] > high_threshold:
                    rep["issues"].add(f"outlier_high_vs_doc(med={doc_median:.1f})")

        # Log e aplica fixes
        issue_pages_doc = 0
        for rep in page_reports:
            if rep["issues"]:
                issue_pages_doc += 1
                total_issue_pages += 1
                if report_output is not None:
                    report_output.write(
                        json.dumps(
                            {
                                "documento": doc,
                                "pagina_num": rep["pagina"],
                                "keyword_count": rep["count"],
                                "issues": sorted(rep["issues"]),
                                "source": rep["source"],
                                "modelo": rep["modelo"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                log.warning(
                    "[%s] p%d  %d keywords  issues: %s",
                    doc,
                    rep["pagina"],
                    rep["count"],
                    ", ".join(sorted(rep["issues"])),
                )

            if (
                apply_fix
                and not rep.get("parse_issue")
                and rep["cleaned"] != rep["parsed"]
            ):
                kw_json = json.dumps(rep["cleaned"], ensure_ascii=False)
                save_keywords_to_resumos(
                    con=con,
                    documento=doc,
                    pagina_num=rep["pagina"],
                    keywords_json=kw_json,
                    source=rep["source"],
                    modelo=rep["modelo"],
                )
                total_fixes += 1
                log.info(
                    "[%s] p%d auto-fix aplicado (dedupe/limpeza)", doc, rep["pagina"]
                )

        # Reprocessa via LLM páginas ainda com issues
        if rerun_bad and process_params:
            for rep in page_reports:
                if not rep["issues"]:
                    continue

                row = rep["row"]
                src = rep.get("source") or process_params.get("source", "resumo_pagina")

                if not model:
                    model = process_params.get(
                        "model", rep.get("modelo") or DEFAULT_MODEL
                    )

                if not provider:
                    provider = process_params.get(
                        "provider", rep.get("provider") or "ollama"
                    )

                raw_kw = row.get("keywords_json") or ""
                has_outlier_low = any(
                    "outlier_low_vs_doc" in issue for issue in rep["issues"]
                ) or any("few_keywords" in issue for issue in rep["issues"])

                if (
                    len(
                        clean_ocr_text_optimized(
                            replace_linebreak(row.get("pagina_texto") or "")
                        )[0].strip()
                    )
                    < 400
                ):
                    continue

                if (
                    (not has_outlier_low)
                    and (raw_kw.strip() or rep["count"] > RECOMMENDED_MIN_KEYWORDS)
                    
                ):
                    judge_args = argparse.Namespace(
                        provider=provider,
                        model=model,
                        timeout=process_params["timeout"],
                        num_ctx=process_params["num_ctx"],
                        reasoning_effort=process_params["reasoning_effort"],
                        api_key_env=process_params["api_key_env"],
                        retries=process_params["retries"],
                        think=process_params["think"],
                        source=src,
                        verbose=False,
                        facsimile=bool(process_params.get("facsimile", False)),
                    )
                    judge_llm_with_retry_and_save(
                        args=judge_args,
                        row=row,
                        base_url=process_params["base_url"],
                        doc=doc,
                        pnum=rep["pagina"],
                        resumos_con=con,
                        raw_kw=raw_kw,
                    )
                    continue

                try:
                    result, raw = process_page(
                        row,
                        source=src,
                        provider=provider,
                        model=model,
                        base_url=process_params["base_url"],
                        timeout=process_params["timeout"],
                        num_ctx=process_params["num_ctx"],
                        reasoning_effort=process_params["reasoning_effort"],
                        api_key_env=process_params["api_key_env"],
                        retries=process_params["retries"],
                        think=process_params["think"],
                        include_facsimile=bool(process_params.get("facsimile", False)),
                    )
                except Exception as exc:
                    log.error("[%s] p%d rerun falhou: %s", doc, rep["pagina"], exc)
                    continue

                kw_json = json.dumps(result, ensure_ascii=False)
                save_keywords_to_resumos(
                    con=con,
                    documento=doc,
                    pagina_num=rep["pagina"],
                    keywords_json=kw_json,
                    source=src,
                    modelo=model,
                )
                total_reruns += 1
                log.info(
                    "[%s] p%d rerun LLM concluído (%d keywords)",
                    doc,
                    rep["pagina"],
                    len(result.get("keywords", [])),
                )

        total_pages += len(rows)
        log.info(
            "[%s] %d páginas | média %.1f | mediana %.1f | páginas com issues: %d%s",
            doc,
            len(rows),
            doc_avg,
            doc_median,
            issue_pages_doc,
            (
                " | FORA DO RANGE"
                if doc_avg
                and (
                    doc_avg < RECOMMENDED_MIN_KEYWORDS
                    or doc_avg > RECOMMENDED_MAX_KEYWORDS
                )
                else ""
            ),
        )

    log.info("─" * 60)
    log.info(
        "Validação concluída: %d páginas verificadas, %d páginas com issues",
        total_pages,
        total_issue_pages,
    )
    if apply_fix:
        log.info("Auto-fixes aplicados: %d páginas", total_fixes)
    if rerun_bad:
        log.info("Reruns via LLM: %d páginas", total_reruns)
    log.info("─" * 60)
    return {
        "pages": total_pages,
        "issue_pages": total_issue_pages,
        "fixes": total_fixes,
        "reruns": total_reruns,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def process_page(
    row: dict,
    source: str,
    *,
    provider: str,
    model: str,
    base_url: str,
    timeout: int,
    num_ctx: int,
    reasoning_effort: str,
    api_key_env: str,
    retries: int,
    think: bool = False,
    include_facsimile: bool = False,
) -> Tuple[KeywordsResult, str]:
    """Processa uma página e retorna (resultado_estruturado, raw_response)."""
    doc_name = row["documento"]
    scripture_evidence = collect_page_scripture_evidence(row)
    user_prompt = build_user_prompt(
        row,
        source,
        doc_name,
        scripture_evidence=scripture_evidence,
    )
    image_path = (
        Path(scripture_evidence["image_file"])
        if include_facsimile and scripture_evidence.get("image_file")
        else None
    )
    if include_facsimile and image_path is None:
        log.info("[%s] p%d fac-símile não encontrado; seguindo só com OCR", doc_name, row["pagina_num"])

    # Trunca se necessário (Ollama)
    ctx_window = num_ctx if provider == "ollama" else 128000
    budget = ctx_window - TOKEN_RESERVE_OUTPUT - TOKEN_RESERVE_SYSTEM
    if budget > 0:
        user_prompt = truncate_to_budget(
            user_prompt,
            budget,
            label="user_prompt",
        )

    MIN_KEYWORDS = 3  # mínimo para considerar resposta parseável

    prompt_tokens_est = estimate_tokens(user_prompt)
    log.info(
        "[%s] p%d  chamando LLM (%s)  ~%d tokens prompt  source=%s",
        doc_name,
        row["pagina_num"],
        model,
        prompt_tokens_est,
        source,
    )

    raw_response = ""
    result: KeywordsResult = {"keywords": []}
    for attempt in range(1, retries + 1):
        try:
            raw_response = llm_chat(
                prompt_system=SYSTEM_PROMPT,
                prompt_user=user_prompt,
                provider=provider,
                model=model,
                base_url=base_url,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
                api_key_env=api_key_env,
                num_ctx=num_ctx,
                think=think,
                image_path=image_path,
            )
        except Exception as exc:
            log.warning(
                "[%s] p%d tentativa %d/%d – erro LLM: %s resposta: %s",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
                exc,
                raw_response,
            )
            if attempt < retries:
                time.sleep(3 * attempt)
            continue

        # Valida se a resposta é parseável
        if not raw_response or not raw_response.strip():
            log.warning(
                "[%s] p%d tentativa %d/%d – resposta vazia: %s",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
                raw_response,
            )
            if attempt < retries:
                time.sleep(2 * attempt)
            continue

        log.debug(
            "[%s] p%d tentativa %d/%d – resposta: %s",
            doc_name,
            row["pagina_num"],
            attempt,
            retries,
            raw_response,
        )

        parsed_result, _ = parse_keywords_response(raw_response)
        cleaned_result, clean_issues = clean_keywords_structure(parsed_result)
        if clean_issues:
            log.debug(
                "[%s] p%d parsing issues: %s",
                doc_name,
                row["pagina_num"],
                ",".join(clean_issues),
            )
        hard_sanitization_issues = sorted(
            set(clean_issues).intersection(HARD_SANITIZATION_ISSUES)
        )
        if hard_sanitization_issues and attempt < retries:
            log.warning(
                "[%s] p%d tentativa %d/%d – resposta rejeitada pela sanitização: %s",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
                ",".join(hard_sanitization_issues),
            )
            time.sleep(2 * attempt)
            continue
        result = cleaned_result
        warrant_issues = validate_scripture_warrant(result, scripture_evidence)
        if warrant_issues:
            log.warning(
                "[%s] p%d citações sem suporte no detector OCR: %s",
                doc_name,
                row["pagina_num"],
                ", ".join(warrant_issues),
            )
        if len(result["keywords"]) >= MIN_KEYWORDS:
            break  # sucesso: resposta parseável com keywords suficientes

        # Checa se página é administrativa/lixo para não insistir
        if _is_admin_or_blank_page(row):
            log.warning(
                "[%s] p%d tentativa %d/%d – página administrativa/lixo detectada; "
                "mantendo %d keywords e encerrando retries",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
                len(result["keywords"]),
            )
            break

        log.warning(
            "[%s] p%d tentativa %d/%d – parsing retornou apenas %d keywords "
            "(mín: %d). Resposta: %s",
            doc_name,
            row["pagina_num"],
            attempt,
            retries,
            len(result["keywords"]),
            MIN_KEYWORDS,
            raw_response.replace("\n", " "),
        )
        if attempt < retries:
            time.sleep(2 * attempt)

    if not raw_response:
        log.error(
            "[%s] p%d esgotou tentativas – nenhuma resposta",
            doc_name,
            row["pagina_num"],
        )
        return {"keywords": []}, ""

    if len(result["keywords"]) < MIN_KEYWORDS:
        log.error(
            "[%s] p%d esgotou tentativas – parsing falhou (%d keywords)",
            doc_name,
            row["pagina_num"],
            len(result["keywords"]),
        )
        return result, raw_response

    return result, raw_response


def preview_llm_judge(
    row: dict,
    *,
    provider: str,
    model: str,
    base_url: str,
    timeout: int,
    num_ctx: int,
    reasoning_effort: str,
    api_key_env: str,
    think: bool = False,
    retry: int = 0,
    include_facsimile: bool = False,
) -> Tuple[str, str]:
    """Constrói o prompt de verificação e chama o LLM apenas para preview."""
    doc_name = row.get("documento", "")
    keywords_originais = _pretty_keywords_json(row.get("keywords_json", ""))
    scripture_evidence = collect_page_scripture_evidence(row)
    user_prompt = build_user_review_prompt(
        row,
        keywords_originais,
        doc_name=doc_name,
        scripture_evidence=scripture_evidence,
    )
    image_path = (
        Path(scripture_evidence["image_file"])
        if include_facsimile and scripture_evidence.get("image_file")
        else None
    )

    log.debug("[%s] p%d  prompt: %s", doc_name, row.get("pagina_num"), user_prompt)

    ctx_window = num_ctx if provider == "ollama" else 128000
    budget = ctx_window - TOKEN_RESERVE_OUTPUT - TOKEN_RESERVE_SYSTEM
    if budget > 0:
        user_prompt = truncate_to_budget(
            user_prompt,
            budget,
            label="review_prompt",
        )

    prompt_tokens_est = estimate_tokens(user_prompt)
    log.info(
        "[%s] p%d  chamando LLM-judge (%s)  ~%d tokens prompt",
        doc_name,
        row.get("pagina_num"),
        model,
        prompt_tokens_est,
    )

    # Precisa combinar com o SYSTEM_PROMPT_VERIFICACAO
    json_schema = {
        "name": "term_analysis_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "terms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "original": {
                                "type": "string",
                                "description": "O termo original que está sendo analisado",
                            },
                            "nota": {
                                "type": "string",
                                "description": "Breve justificativa técnica (máx 10 palavras)",
                            },
                            "decision": {
                                "type": "string",
                                "enum": ["keep", "change", "delete", "merge"],
                                "description": "Ação a ser tomada com o termo",
                            },
                            "change_to": {
                                "type": ["string", "null"],
                                "description": "Novo Termo ou Nome do Termo que o absorveu (null se não houver)",
                            },
                            "is_canon_name": {
                                "type": ["boolean", "null"],
                                "description": "Indica se este é o nome canônico/oficial",
                            },
                        },
                        "required": [
                            "original",
                            "nota",
                            "decision",
                            "change_to",
                            "is_canon_name",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["terms"],
            "additionalProperties": False,
        },
    }

    top_p = TOP_P_DEFAULT
    temperature = TEMPERATURE_DEFAULT

    if retry > 1:
        temperature = 0.5
        top_p = 0.9

    if retry > 3:
        json_schema = None  # desativa schema após muitas tentativas para evitar falhas por parsing de schema

    response = llm_chat(
        prompt_system=SYSTEM_PROMPT_VERIFICACAO,
        prompt_user=user_prompt,
        provider=provider,
        model=model,
        base_url=base_url,
        timeout=timeout,
        reasoning_effort=reasoning_effort,
        api_key_env=api_key_env,
        num_ctx=num_ctx,
        think=think,
        json_schema=json_schema,
        top_p=top_p,
        temperature=temperature,
        image_path=image_path,
    )
    return user_prompt, response


def judge_llm_with_retry_and_save(
    args: argparse.Namespace,
    row: dict,
    base_url: str,
    doc: str,
    pnum: int,
    resumos_con: sqlite3.Connection,
    raw_kw: str,
) -> bool:
    prompt_user = ""
    response = ""
    decisions: List[dict] = []
    parse_issues: List[str] = []
    for attempt in range(1, args.retries + 1):
        try:
            prompt_user, response = preview_llm_judge(
                row,
                provider=args.provider,
                model=args.model,
                base_url=base_url,
                timeout=args.timeout,
                num_ctx=args.num_ctx,
                reasoning_effort=args.reasoning_effort,
                api_key_env=args.api_key_env,
                think=args.think,
                retry=attempt,
                include_facsimile=args.facsimile,
            )
            decisions, parse_issues = parse_judge_response(response)
            if decisions:
                break
            log.warning(
                "[%s] p%d LLM-judge tentativa %d/%d sem decisões: %s",
                doc,
                pnum,
                attempt,
                args.retries,
                ",".join(parse_issues) if parse_issues else "unknown",
            )
        except Exception as exc:
            traceback.print_exc()
            log.warning(
                "[%s] p%d LLM-judge tentativa %d/%d falhou: %s",
                doc,
                pnum,
                attempt,
                args.retries,
                exc,
            )
        if attempt < args.retries:
            backoff = 1.5 ** (attempt - 1)
            jitter = random.uniform(0.5, 1.5)
            time.sleep(backoff * jitter)

    if not decisions:
        log.error(
            "[%s] p%d LLM-judge esgotou tentativas; parse_issues=%s",
            doc,
            pnum,
            ",".join(parse_issues) if parse_issues else "none",
        )
        return False

    new_struct, report = apply_judge_decisions(
        raw_kw,
        decisions,
        order_from_judge=True,
        include_categories=True,
    )

    if not new_struct.get("keywords"):
        log.warning(
            "[%s] p%d resultado vazio; issues=%s", doc, pnum, report.get("issues")
        )
        return False

    kw_json = json.dumps(new_struct, ensure_ascii=False)
    save_keywords_to_resumos(
        con=resumos_con,
        documento=doc,
        pagina_num=pnum,
        keywords_json=kw_json,
        source=row.get("keywords_source", args.source),
        modelo=row.get("keywords_modelo", args.model),
    )

    log.info(
        "[%s] p%d judge aplicado: %d keywords, changes=%d removed=%d issues=%s",
        doc,
        pnum,
        len(new_struct.get("keywords", [])),
        report.get("changes"),
        report.get("removed"),
        ",".join(report.get("issues", [])) or "none",
    )

    if args.verbose:
        print("─" * 60)
        print(f"  {doc}  página {pnum}  (LLM-judge)")
        print("─" * 60)
        print("PROMPT USUÁRIO:\n")
        print(prompt_user)
        print("\nRESPOSTA LLM:\n")
        print(response)
        print("\nJSON ANTIGO:\n")
        print(_pretty_keywords_json(raw_kw))
        print("\nJSON NOVO:\n")
        print(json.dumps(new_struct, ensure_ascii=False, indent=2))
        print("")


# ---------------------------------------------------------------------------
# Formatação dry-run
# ---------------------------------------------------------------------------


def format_dry_output(
    doc: str,
    pnum: int,
    source: str,
    result: KeywordsResult,
    raw: str,
    verbose: bool = False,
) -> str:
    lines: List[str] = []
    keywords = result.get("keywords", [])
    categorias = result.get("categorias", {})

    lines.append(f"{'─' * 60}")
    lines.append(f"  {doc}  página {pnum}  (source: {source})")
    lines.append(f"{'─' * 60}")

    if keywords:
        lines.append(f"  Keywords ({len(keywords)}):")
        for i, kw in enumerate(keywords, 1):
            lines.append(f"    {i:2d}. {kw}")
    else:
        lines.append("  ⚠ Nenhuma keyword extraída")

    if categorias:
        lines.append("")
        lines.append("  Categorias:")
        for cat, items in categorias.items():
            lines.append(f"    {cat}: {', '.join(items)}")

    if verbose and raw:
        lines.append("")
        lines.append("  ── Resposta completa ──")
        for l in raw.splitlines():
            lines.append(f"  │ {l}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extração de keywords de páginas da Patrística via LLM. "
        "Dry run por padrão (stdout).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Exemplos:
  # Testar com 3 páginas do PG001, usando resumo da página (dry run)
  python keywords_serial.py --doc PG001 --limit 3

  # Testar uma página específica com texto completo
  python keywords_serial.py --doc PG001 --page 4 --source pagina_texto

  # Testar com OpenAI
  python keywords_serial.py --doc PG001 --page 4 --provider openai

  # Ver resposta completa do modelo (verbose)
  python keywords_serial.py --doc PG001 --page 4 -v

  # Gravar keywords na tabela resumos (padrão)
  python keywords_serial.py --doc PG001 --write

  # Gravar em DB separado (patristica_keywords.db)
  python keywords_serial.py --doc PG001 --write --separate-db

  # Processar todos os documentos
  python keywords_serial.py --all --write --skip-done
""",
    )

    # Seleção de documentos
    g = p.add_mutually_exclusive_group()
    g.add_argument("--doc", help="Documento específico (ex: PG001).")
    g.add_argument("--all", action="store_true", help="Processar todos os documentos.")

    p.add_argument(
        "--page",
        type=int,
        default=None,
        help="Página específica (para testes rápidos).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limitar quantidade de páginas a processar por documento.",
    )

    # Fonte de input
    p.add_argument(
        "--source",
        choices=VALID_SOURCES,
        default="resumo_pagina",
        help="Fonte de conteúdo para extração (default: resumo_pagina).",
    )

    # Output
    p.add_argument(
        "--write",
        action="store_true",
        help="Gravar keywords (sem isso, dry run no stdout). "
        "Por padrão grava na tabela resumos; use --separate-db para DB à parte.",
    )
    p.add_argument(
        "--separate-db",
        action="store_true",
        help="Gravar num DB separado (patristica_keywords.db) em vez da tabela resumos.",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="Somente valida keywords já gravadas (não chama LLM).",
    )
    p.add_argument(
        "--verify-fix",
        action="store_true",
        help="Valida e aplica auto-fix leve (dedupe/limpeza) nas keywords já gravadas.",
    )
    p.add_argument(
        "--verify-report",
        type=Path,
        help="Grava JSONL das páginas problemáticas; requer --verify/--verify-fix.",
    )
    p.add_argument(
        "--verify-scripture-warrant",
        action="store_true",
        help=(
            "Com --verify/--verify-fix, compara citações explícitas das keywords "
            "com o detector conservador aplicado ao OCR da página. Apenas reporta; "
            "não apaga citações automaticamente."
        ),
    )
    p.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Retorna exit code 1 se a verificação encontrar problemas.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Mostra resposta completa do modelo no dry run.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output em JSON Lines (uma linha por página) no dry run.",
    )
    p.add_argument(
        "--no-skip-noise",
        dest="skip_noise",
        action="store_false",
        help="Processa também páginas classificadas como ruído/boilerplate.",
    )
    p.set_defaults(skip_noise=True)
    p.add_argument(
        "--rerun-bad",
        action="store_true",
        help="Após verify/verify-fix, reprocessa páginas ainda com issues chamando o LLM.",
    )
    p.add_argument(
        "--preview-llm-judge",
        action="store_true",
        help=(
            "Monta SYSTEM_PROMPT_VERIFICACAO + build_user_review_prompt e chama o LLM, "
            "apenas para preview (stdout); não grava nem valida."
        ),
    )
    p.add_argument(
        "--llm-judge",
        action="store_true",
        help=(
            "Aplica o LLM-judge às keywords existentes, atualizando keywords_json em resumos."
        ),
    )

    # Databases
    p.add_argument(
        "--resumos-db",
        type=Path,
        default=DEFAULT_RESUMOS_DB,
        help=f"DB de resumos (input, default: {DEFAULT_RESUMOS_DB}).",
    )
    p.add_argument(
        "--keywords-db",
        type=Path,
        default=DEFAULT_KEYWORDS_DB,
        help=f"DB de keywords (output, default: {DEFAULT_KEYWORDS_DB}).",
    )

    # LLM
    p.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    p.add_argument(
        "--model", default=None, help=f"Modelo (default: {DEFAULT_MODEL} / gpt-5-mini)."
    )
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--openai-url", default="https://api.openai.com/v1")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    p.add_argument(
        "--reasoning-effort",
        choices=["", "minimal", "low", "medium", "high"],
        default="high",
    )
    p.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    p.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout em segundos (default: 120 ollama, 300 openai).",
    )
    p.add_argument(
        "--retries", type=int, default=5, help="Número de tentativas em caso de falha."
    )
    p.add_argument(
        "--think",
        action="store_true",
        help="Habilita thinking (chain-of-thought) em modelos qwen3/deepseek-r1. "
        "Desligado por padrão pois keywords não precisam de raciocínio profundo.",
    )
    p.add_argument(
        "--facsimile",
        action="store_true",
        help=(
            "Anexa ao modelo a imagem pareada da página quando existir (OpenAI ou "
            "Ollama multimodal), convertida para JPEG em memória. O pareamento usa "
            "o artefato físico, não a página editorial."
        ),
    )
    p.add_argument(
        "--no-integrity-check",
        action="store_true",
        help="Desliga a checagem linguística (NLTK/CLTK) das keywords.",
    )
    p.add_argument(
        "--integrity-debug",
        action="store_true",
        help="Loga evidências detalhadas do verificador de integridade.",
    )

    # Controle
    p.add_argument(
        "--skip-done",
        action="store_true",
        help="Pula páginas que já têm keywords (com --write).",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()

    preview_mode = args.preview_llm_judge
    llm_judge_mode = args.llm_judge

    # Resolve modelo
    if args.model is None:
        args.model = "gpt-5-mini" if args.provider == "openai" else DEFAULT_MODEL

    # Resolve timeout por provider (se não especificado)
    if args.timeout is None:
        args.timeout = (
            DEFAULT_TIMEOUT_OPENAI
            if args.provider == "openai"
            else DEFAULT_TIMEOUT_OLLAMA
        )

    # Resolve base_url
    base_url = args.openai_url if args.provider == "openai" else args.ollama_url

    # Modo de gravação/validação
    verify_mode = args.verify or args.verify_fix
    apply_fix = args.verify_fix
    rerun_bad = args.rerun_bad

    # Ajusta flags do verificador de integridade
    set_integrity_flags(enabled=not args.no_integrity_check, debug=args.integrity_debug)

    if (llm_judge_mode and preview_mode) or (
        llm_judge_mode and (verify_mode or args.write or args.separate_db or rerun_bad)
    ):
        log.error(
            "--llm-judge é exclusivo; não combine com preview/verify/write/separate-db/rerun-bad."
        )
        sys.exit(1)

    if preview_mode and (args.write or verify_mode or args.separate_db or rerun_bad):
        log.error(
            "--preview-llm-judge é apenas para inspeção; não combine com "
            "--write/--verify/--verify-fix/--separate-db/--rerun-bad."
        )
        sys.exit(1)

    if (
        args.verify_report or args.fail_on_issues or args.verify_scripture_warrant
    ) and not verify_mode:
        log.error(
            "--verify-report/--fail-on-issues/--verify-scripture-warrant "
            "requer --verify ou --verify-fix."
        )
        sys.exit(1)

    # Conexões
    if preview_mode:
        resumos_con = connect_readonly(args.resumos_db)
        mode_label = "PREVIEW LLM-JUDGE (stdout)"
        kw_con = None
        dry_run = True
        use_resumos_inline = False
        use_separate_db = False
    elif llm_judge_mode:
        resumos_con = connect_readwrite(args.resumos_db)
        ensure_keywords_columns(resumos_con)
        mode_label = "LLM-JUDGE (write keywords_json)"
        kw_con = None
        dry_run = False
        use_resumos_inline = True
        use_separate_db = False
    elif verify_mode:
        needs_write = apply_fix or rerun_bad
        resumos_con = (
            connect_readwrite(args.resumos_db)
            if needs_write
            else connect_readonly(args.resumos_db)
        )
        mode_label = "VERIFICAR keywords"
        if apply_fix:
            mode_label += " (auto-fix ON)"
        if rerun_bad:
            mode_label += " + RERUN LLM"
        if needs_write:
            ensure_keywords_columns(resumos_con)
    else:
        use_separate_db = args.separate_db and args.write
        use_resumos_inline = args.write and not args.separate_db
        dry_run = not args.write

        if use_resumos_inline:
            resumos_con = connect_readwrite(args.resumos_db)
            ensure_keywords_columns(resumos_con)
            mode_label = f"GRAVANDO na tabela resumos → {args.resumos_db}"
        else:
            resumos_con = connect_readonly(args.resumos_db)

        kw_con: Optional[sqlite3.Connection] = None
        if use_separate_db:
            kw_con = connect_keywords_db(args.keywords_db)
            init_keywords_schema(kw_con)
            mode_label = f"GRAVANDO → {args.keywords_db}"

        if dry_run:
            mode_label = "DRY RUN (stdout)"

    log.info("═" * 60)
    log.info("  Keywords Serial – Patrística")
    log.info("═" * 60)
    log.info("  Modo:     %s", mode_label)
    if not verify_mode or rerun_bad:
        log.info("  Source:   %s", args.source)
        log.info("  Provider: %s  |  Modelo: %s", args.provider, args.model)
        if args.provider == "ollama":
            log.info(
                "  num_ctx:  %d  |  think: %s",
                args.num_ctx,
                "ON" if args.think else "OFF",
            )
    log.info("═" * 60)

    # Descobre documentos
    if args.doc:
        docs = [args.doc]
    elif args.all:
        docs = list_documents(resumos_con)
    else:
        log.error("Especifique --doc NOME ou --all")
        sys.exit(1)

    if not docs:
        log.warning("Nenhum documento encontrado no DB de resumos.")
        sys.exit(1)

    log.info("Documentos: %d", len(docs))

    if preview_mode:
        for doc in docs:
            rows = fetch_pages(resumos_con, doc, page=args.page, limit=args.limit)
            if not rows:
                log.warning("[%s] Nenhuma página encontrada para preview", doc)
                continue

            log.info("[%s] %d páginas para preview LLM-judge", doc, len(rows))
            for row in rows:
                pnum = row["pagina_num"]

                # Opcionalmente pula páginas lixo/boilerplate se o usuário não desligou
                if args.skip_noise:
                    texto_original = row.get("pagina_texto") or ""
                    texto_limpo, meta = clean_ocr_text_optimized(texto_original)
                    quality = classify_page_noise(
                        texto_original, texto_limpo, meta=meta
                    )
                    if quality.get("drop_original_embedding"):
                        log.info(
                            "[%s] p%d pulada (noise): %s",
                            doc,
                            pnum,
                            quality.get("reason", "noise"),
                        )
                        continue

                try:
                    prompt_user, response = preview_llm_judge(
                        row,
                        provider=args.provider,
                        model=args.model,
                        base_url=base_url,
                        timeout=args.timeout,
                        num_ctx=args.num_ctx,
                        reasoning_effort=args.reasoning_effort,
                        api_key_env=args.api_key_env,
                        think=args.think,
                        include_facsimile=args.facsimile,
                    )
                except Exception as exc:
                    log.error("[%s] p%d preview LLM-judge falhou: %s", doc, pnum, exc)
                    continue

                print("─" * 60)
                print(f"  {doc}  página {pnum}  (preview judge)")
                print("─" * 60)
                print("PROMPT USUÁRIO:\n")
                print(prompt_user)
                print("\nRESPOSTA LLM:\n")
                print(response)
                print("")

        resumos_con.close()
        return
    elif llm_judge_mode:
        for doc in docs:
            rows = fetch_pages(resumos_con, doc, page=args.page, limit=args.limit)
            if not rows:
                log.warning("[%s] Nenhuma página encontrada para judge", doc)
                continue

            log.info("[%s] %d páginas para LLM-judge", doc, len(rows))
            for row in rows:
                pnum = row["pagina_num"]

                if args.skip_noise:
                    texto_original = row.get("pagina_texto") or ""
                    texto_limpo, meta = clean_ocr_text_optimized(texto_original)
                    quality = classify_page_noise(
                        texto_original, texto_limpo, meta=meta
                    )
                    if quality.get("drop_original_embedding"):
                        log.info(
                            "[%s] p%d pulada (noise): %s",
                            doc,
                            pnum,
                            quality.get("reason", "noise"),
                        )
                        continue

                raw_kw = row.get("keywords_json") or ""
                if not raw_kw.strip():
                    log.warning("[%s] p%d sem keywords_json; pulando", doc, pnum)
                    continue

                judge_llm_with_retry_and_save(
                    args=args,
                    row=row,
                    base_url=base_url,
                    doc=doc,
                    pnum=pnum,
                    resumos_con=resumos_con,
                    raw_kw=raw_kw,
                )
        resumos_con.close()
        return

    # Apenas validação
    if verify_mode:
        process_params = {
            "provider": args.provider,
            "model": args.model,
            "base_url": base_url,
            "timeout": args.timeout,
            "num_ctx": args.num_ctx,
            "reasoning_effort": args.reasoning_effort,
            "api_key_env": args.api_key_env,
            "retries": args.retries,
            "think": args.think,
            "source": args.source,
            "facsimile": args.facsimile,
        }
        report_output = (
            args.verify_report.open("w", encoding="utf-8")
            if args.verify_report
            else None
        )
        try:
            summary = verify_documents(
                con=resumos_con,
                docs=docs,
                apply_fix=apply_fix,
                rerun_bad=rerun_bad,
                process_params=process_params,
                page=args.page,
                limit=args.limit,
                report_output=report_output,
                check_scripture_warrant=args.verify_scripture_warrant,
            )
        finally:
            if report_output is not None:
                report_output.close()
        resumos_con.close()
        if args.fail_on_issues and summary["issue_pages"]:
            raise SystemExit(1)
        return

    total_kw = 0
    total_pages = 0
    total_skipped = 0
    total_skipped_noise = 0

    for doc in docs:
        pages = fetch_pages(resumos_con, doc, page=args.page, limit=args.limit)
        if not pages:
            log.warning("[%s] Nenhuma página encontrada", doc)
            continue

        log.info("[%s] %d páginas a processar", doc, len(pages))

        for row in pages:
            pnum = row["pagina_num"]

            # Pula já processadas
            if args.skip_done:
                if use_resumos_inline and is_already_done_resumos(
                    resumos_con, doc, pnum
                ):
                    total_skipped += 1
                    continue
                if (
                    use_separate_db
                    and kw_con
                    and is_already_done(kw_con, doc, pnum, args.source, args.model)
                ):
                    total_skipped += 1
                    continue

            # Pula páginas lixo/boilerplate detectadas pelo pipeline de limpeza
            if args.skip_noise:
                texto_original = row.get("pagina_texto") or ""
                texto_limpo, meta = clean_ocr_text_optimized(texto_original)
                quality = classify_page_noise(texto_original, texto_limpo, meta=meta)
                if quality.get("drop_original_embedding"):
                    total_skipped_noise += 1
                    log.info(
                        "[%s] p%d pulada (noise): %s",
                        doc,
                        pnum,
                        quality.get("reason", "noise"),
                    )
                    continue

            t0 = time.time()
            result, raw = process_page(
                row,
                source=args.source,
                provider=args.provider,
                model=args.model,
                base_url=base_url,
                timeout=args.timeout,
                num_ctx=args.num_ctx,
                reasoning_effort=args.reasoning_effort,
                api_key_env=args.api_key_env,
                retries=args.retries,
                think=args.think,
                include_facsimile=args.facsimile,
            )
            elapsed = time.time() - t0

            keywords = result.get("keywords", [])
            n_cats = len(result.get("categorias", {}))
            total_pages += 1
            total_kw += len(keywords)

            # Output
            if dry_run:
                if args.json_output:
                    obj = {
                        "documento": doc,
                        "pagina_num": pnum,
                        "source": args.source,
                        "modelo": args.model,
                        **result,
                        "elapsed_s": round(elapsed, 1),
                    }
                    if args.verbose:
                        obj["raw"] = raw
                    print(json.dumps(obj, ensure_ascii=False))
                else:
                    print(
                        format_dry_output(
                            doc,
                            pnum,
                            args.source,
                            result,
                            raw,
                            verbose=args.verbose,
                        )
                    )
            elif use_resumos_inline:
                # Grava na tabela resumos (JSON estruturado)
                kw_json = json.dumps(result, ensure_ascii=False)
                save_keywords_to_resumos(
                    con=resumos_con,
                    documento=doc,
                    pagina_num=pnum,
                    keywords_json=kw_json,
                    source=args.source,
                    modelo=args.model,
                )
            elif use_separate_db:
                # Grava no DB separado (JSON estruturado)
                kw_json = json.dumps(result, ensure_ascii=False)
                save_keywords(
                    con=kw_con,
                    documento=doc,
                    pagina_num=pnum,
                    source=args.source,
                    keywords_json=kw_json,
                    keywords_raw=raw,
                    modelo=args.model,
                )

            cat_info = f"  {n_cats} cats" if n_cats else ""
            log.info(
                "[%s] p%d  %d keywords%s  %.1fs  ✓",
                doc,
                pnum,
                len(keywords),
                cat_info,
                elapsed,
            )

    # Resumo final
    log.info("─" * 60)
    log.info("Total: %d páginas, %d keywords extraídas", total_pages, total_kw)
    if total_skipped:
        log.info("Puladas (já feitas): %d", total_skipped)
    if total_skipped_noise:
        log.info("Puladas (ruído/boilerplate): %d", total_skipped_noise)
    if total_pages:
        log.info("Média: %.1f keywords/página", total_kw / total_pages)
    log.info("─" * 60)

    resumos_con.close()
    if not verify_mode and kw_con:
        kw_con.close()


if __name__ == "__main__":
    main()
