#!/usr/bin/env python3
"""
resumo_serial.py – Resumo serial página-a-página de volumes da Patrística via Ollama ou OpenAI.

Para cada volume (ex: teste/PL001/text/*.txt), percorre todas as páginas em
ordem numérica, envia o conteúdo + contexto acumulado para o LLM e grava
o resultado num SQLite dedicado (data/patristica_resumos.db).

Uso (Ollama – padrão):
    python resumo_serial.py --volume-dir teste/PL001
    python resumo_serial.py --volume-dir teste/PL001 --model qwen3:30b
    python resumo_serial.py --root teste --series PL --limit 2
    python resumo_serial.py --resume          # retoma de onde parou

Uso (OpenAI):
    python resumo_serial.py --provider openai --model gpt-5-mini --volume-dir teste/PL001
    python resumo_serial.py --provider openai --model gpt-5-mini --reasoning-effort high
    python resumo_serial.py --provider openai --model gpt-5-mini --facsimile --page 5 --dry-run
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, TextIO, Tuple

# Permite importar utilitários de limpeza compartilhados
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts.limpeza_ocr import (  # type: ignore  # noqa: E402
    clean_ocr_text_optimized,
    clean_summary_global_for_search,
    clean_summary_page_for_embedding,
)
from tools.indexing.index_target_locator import (  # type: ignore  # noqa: E402
    resolve_paired_page_image,
)
from facsimile_transport import (  # noqa: E402
    DEFAULT_FACSIMILE_JPEG_QUALITY,
    encode_facsimile_for_transport,
)
from resumo_v2 import (  # noqa: E402
    SUMMARY_PROMPT_VERSION,
    SummaryCandidate,
    analyze_page,
    build_embedding_text,
    build_summary_search_text,
    canonical_labels_from_hints,
    extract_json_object_response,
    generation_for_page,
    get_or_create_run,
    generation_last_work_key,
    initial_active_work_key,
    init_v2_schema,
    invalidate_generation_range,
    load_volume_index_hints,
    mark_run_blocked,
    mark_run_complete,
    next_trusted_work_start,
    open_indices_readonly,
    parse_summary_candidate,
    promote_segment,
    reconcile_candidate_work_keys,
    resume_page_number,
    sha256_text,
    store_context_anchor,
    store_generation,
)

# Importa módulo de versionamento OCR (opcional — degrada graciosamente)
try:
    import ocr_versions_db as _vdb
except ImportError:
    _vdb = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_ROOT = PROJECT_ROOT / "teste"
DEFAULT_MODEL = "gemma4:cloud"
DEFAULT_INDICES_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300  # segundos – resumos longos podem demorar

DEFAULT_NUM_CTX = 16384  # janela de contexto padrão do Ollama (tokens)
DEFAULT_V2_GEMMA4_NUM_CTX = 131072  # metade da janela anunciada de 256K
TOKEN_RESERVE_OUTPUT = 2048  # tokens reservados para a resposta do modelo
TOKEN_RESERVE_SYSTEM = 600  # estimativa p/ system prompt + boilerplate
CHARS_PER_TOKEN = 3.8  # heurística: ~3.8 chars/token (latim/grego/pt misturados)
MAX_FACSIMILE_BYTES = 20 * 1024 * 1024
FACSIMILE_JPEG_QUALITY = DEFAULT_FACSIMILE_JPEG_QUALITY

SERIES_RE = re.compile(r"^(PG|PL|PO)(\d+)(.*)$")
PAGE_NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
PAGE_NUM_FALLBACK_RE = re.compile(r"(\d+)(?=\.[^.]+$)")

TEMPERATURE_DEFAULT = 0.2
TOP_P_DEFAULT = 0.5


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("resumo_serial")


@dataclass(frozen=True)
class TokenUsage:
    """Contagem de tokens de uma ou mais chamadas ao modelo."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            requests=self.requests + other.requests,
            estimated=self.estimated or other.estimated,
        )


class LLMResponse(str):
    """String compatível com os consumidores antigos, acrescida do usage."""

    usage: TokenUsage

    def __new__(cls, content: str, usage: TokenUsage) -> "LLMResponse":
        instance = super().__new__(cls, content)
        instance.usage = usage
        return instance

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")  # 30s de espera em caso de lock
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def acquire_volume_processing_lock(db_path: Path, documento: str) -> TextIO:
    """Impede duas cadeias graváveis para o mesmo volume no mesmo DB."""
    lock_dir = db_path.resolve().parent / ".resumo_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_db = re.sub(r"[^A-Za-z0-9_.-]+", "_", db_path.name)
    safe_documento = re.sub(r"[^A-Za-z0-9_.-]+", "_", documento)
    lock_path = lock_dir / f"{safe_db}.{safe_documento}.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            f"Volume {documento} já está sendo processado por outra execução "
            f"(lock: {lock_path})"
        ) from exc
    return handle


def release_volume_processing_lock(handle: TextIO | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def init_resumo_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS resumos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            documento     TEXT    NOT NULL,
            pagina_num    INTEGER NOT NULL,
            pagina_file   TEXT    NOT NULL,
            pagina_texto  TEXT    NOT NULL,
            resumo_pagina TEXT    NOT NULL,
            resumo_global TEXT    NOT NULL,
            modelo        TEXT    NOT NULL,
            criado_em     TEXT    NOT NULL,
            keywords_json TEXT    NOT NULL DEFAULT '',
            keywords_source TEXT  NOT NULL DEFAULT '',
            keywords_modelo TEXT  NOT NULL DEFAULT '',
            UNIQUE(documento, pagina_num)
        );
        CREATE INDEX IF NOT EXISTS idx_resumos_doc
            ON resumos(documento);
        CREATE INDEX IF NOT EXISTS idx_resumos_doc_page
            ON resumos(documento, pagina_num);
        """
    )
    # Migração: adiciona colunas se tabela já existia sem elas
    for col, typedef in [
        ("keywords_json", "TEXT NOT NULL DEFAULT ''"),
        ("keywords_source", "TEXT NOT NULL DEFAULT ''"),
        ("keywords_modelo", "TEXT NOT NULL DEFAULT ''"),
        ("author_detected", "TEXT NOT NULL DEFAULT ''"),
        ("work_detected", "TEXT NOT NULL DEFAULT ''"),
        ("summary_page_clean", "TEXT NOT NULL DEFAULT ''"),
        ("summary_global_clean", "TEXT NOT NULL DEFAULT ''"),
        ("ocr_result_id", "INTEGER"),
    ]:
        try:
            con.execute(f"ALTER TABLE resumos ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # coluna já existe
    con.commit()


def ensure_resumos_embedding_schema(con: sqlite3.Connection) -> None:
    # Garante colunas hdbscan_group_id em resumos
    try:
        # Adiciona colunas para armazenar os IDs dos grupos HDBSCAN
        # Resumo global deve conter pouca variação dentro de um mesmo livro de um autor dentro de um volume
        # Usaremos para agrupamento e otimização indexação do pagefind
        # Iremos testar 5D UMAP
        con.execute(
            "ALTER TABLE resumos ADD COLUMN resumo_global_hdbscan_group_id INTEGER"
        )

        # Resumo da página provavelmente irá variar um pouco mais conforme o autor for argumentando
        # Iremos testar 15D UMAP
        con.execute(
            "ALTER TABLE resumos ADD COLUMN resumo_pagina_hdbscan_group_id INTEGER"
        )
    except sqlite3.OperationalError:
        pass


def get_last_processed_page(con: sqlite3.Connection, documento: str) -> Optional[int]:
    """Retorna o maior pagina_num já processado para este documento, ou None."""
    row = con.execute(
        "SELECT MAX(pagina_num) AS mx FROM resumos WHERE documento = ?",
        (documento,),
    ).fetchone()
    val = row["mx"] if row else None
    return int(val) if val is not None else None


def get_processed_pages_set(con: sqlite3.Connection, documento: str) -> set[int]:
    """Retorna um conjunto com todas as páginas já processadas para este documento."""
    rows = con.execute(
        "SELECT pagina_num FROM resumos WHERE documento = ?", (documento,)
    ).fetchall()
    return {r["pagina_num"] for r in rows}


def get_last_resumo_global(
    con: sqlite3.Connection, documento: str, before_page: Optional[int] = None
) -> Tuple[str, str, str]:
    """Retorna (resumo_global, author_detected, work_detected) da última página processada.
    Se before_page for informado, pega a página anterior mais próxima."""
    if before_page is not None:
        row = con.execute(
            """SELECT resumo_global, author_detected, work_detected FROM resumos
               WHERE documento = ? AND pagina_num < ?
               ORDER BY pagina_num DESC LIMIT 1""",
            (documento, before_page),
        ).fetchone()
    else:
        row = con.execute(
            """SELECT resumo_global, author_detected, work_detected FROM resumos
               WHERE documento = ?
               ORDER BY pagina_num DESC LIMIT 1""",
            (documento,),
        ).fetchone()
    if not row:
        return ("", "", "")
    return (
        str(row["resumo_global"] or ""),
        str(row["author_detected"] or ""),
        str(row["work_detected"] or ""),
    )


def save_resumo(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    pagina_file: str,
    pagina_texto: str,
    resumo_pagina: str,
    resumo_global: str,
    modelo: str,
    author_detected: str,
    work_detected: str,
    summary_page_clean: str,
    summary_global_clean: str,
    ocr_result_id: Optional[int] = None,
) -> None:
    con.execute(
        """INSERT INTO resumos
           (documento, pagina_num, pagina_file, pagina_texto,
            resumo_pagina, resumo_global, modelo, criado_em,
            author_detected, work_detected, summary_page_clean,
            summary_global_clean, ocr_result_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(documento, pagina_num) DO UPDATE SET
             pagina_file=excluded.pagina_file,
             pagina_texto=excluded.pagina_texto,
             resumo_pagina=excluded.resumo_pagina,
             resumo_global=excluded.resumo_global,
             modelo=excluded.modelo,
             criado_em=excluded.criado_em,
             author_detected=excluded.author_detected,
             work_detected=excluded.work_detected,
             summary_page_clean=excluded.summary_page_clean,
             summary_global_clean=excluded.summary_global_clean,
             ocr_result_id=excluded.ocr_result_id""",
        (
            documento,
            pagina_num,
            pagina_file,
            pagina_texto,
            resumo_pagina,
            resumo_global,
            modelo,
            _now_iso(),
            author_detected,
            work_detected,
            summary_page_clean,
            summary_global_clean,
            ocr_result_id,
        ),
    )
    con.commit()


def resolve_page_facsimile(page_path: Path) -> Path | None:
    """Resolve a imagem física pareada sem inferir número editorial."""
    paired = resolve_paired_page_image(str(page_path))
    if not paired:
        return None
    image_path = Path(paired)
    try:
        if not image_path.is_file() or image_path.stat().st_size > MAX_FACSIMILE_BYTES:
            return None
    except OSError:
        return None
    return image_path.resolve()


# ---------------------------------------------------------------------------
# Ollama (texto livre, sem forçar JSON)
# ---------------------------------------------------------------------------


def ollama_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: int = DEFAULT_TIMEOUT,
    num_ctx: int = DEFAULT_NUM_CTX,
    image_path: Path | None = None,
) -> LLMResponse:
    """Chama Ollama /api/chat e retorna a resposta como texto puro."""
    url = f"{base_url}/api/chat"
    user_message: dict = {"role": "user", "content": prompt_user}
    if image_path is not None:
        encoded, _mime_type = encode_facsimile_for_transport(image_path)
        user_message["images"] = [encoded]
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt_system},
            user_message,
        ],
        "options": {
            "temperature": TEMPERATURE_DEFAULT,
            "top_p": TOP_P_DEFAULT,
            "num_ctx": num_ctx,
        },
        # "reasoning_effort": "high",
    }
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

    message = body.get("message") or {}
    content = message.get("content", "")
    thinking = message.get("thinking", "")

    # Se o conteúdo principal estiver vazio, mas houver raciocínio,
    # usamos o raciocínio como resposta.
    response_text = (
        thinking.strip()
        if not content.strip() and thinking.strip()
        else content.strip()
    )
    has_usage = "prompt_eval_count" in body or "eval_count" in body
    usage = TokenUsage(
        input_tokens=int(body.get("prompt_eval_count") or 0),
        output_tokens=int(body.get("eval_count") or 0),
        requests=1,
        estimated=not has_usage,
    )
    if not has_usage:
        usage = TokenUsage(
            input_tokens=estimate_tokens(prompt_system + "\n" + prompt_user),
            output_tokens=estimate_tokens(response_text),
            requests=1,
            estimated=True,
        )
    return LLMResponse(response_text, usage)


# ---------------------------------------------------------------------------
# OpenAI (texto livre, suporte a reasoning)
# ---------------------------------------------------------------------------


def openai_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: int = DEFAULT_TIMEOUT,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    image_path: Path | None = None,
) -> LLMResponse:
    """Chama OpenAI /chat/completions e retorna a resposta como texto puro."""
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Variável de ambiente {api_key_env} não definida. "
            f"Defina-a com sua chave de API OpenAI."
        )

    url = f"{base_url}/chat/completions"
    user_content: str | list[dict] = prompt_user
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
        "response_format": {"type": "json_object"},
        "service_tier": "flex",
    }

    # reasoning_effort é suportado por modelos com reasoning (o1, o3, gpt-5-mini, etc.)
    if reasoning_effort and len(reasoning_effort) > 0:
        payload["reasoning_effort"] = reasoning_effort

    if not "gpt-5" in model:
        # Model não é gpt-5, portanto deve suportar temperatura
        payload["temperature"] = TEMPERATURE_DEFAULT
        payload["top_p"] = TOP_P_DEFAULT
    else:
        payload["verbosity"] = "low"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        service_tier = body.get("service_tier", "")
        log.debug(f"Service Tier: {service_tier}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI falhou: {exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI retornou sem choices")
    content = choices[0].get("message", {}).get("content", "").strip()
    raw_usage = body.get("usage") or {}
    has_usage = bool(raw_usage)
    usage = TokenUsage(
        input_tokens=int(raw_usage.get("prompt_tokens") or 0),
        output_tokens=int(raw_usage.get("completion_tokens") or 0),
        requests=1,
        estimated=not has_usage,
    )
    if not has_usage:
        usage = TokenUsage(
            input_tokens=estimate_tokens(prompt_system + "\n" + prompt_user),
            output_tokens=estimate_tokens(content),
            requests=1,
            estimated=True,
        )
    return LLMResponse(content, usage)


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
    timeout: int = DEFAULT_TIMEOUT,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    num_ctx: int = DEFAULT_NUM_CTX,
    image_path: Path | None = None,
) -> LLMResponse:
    """Despacha para Ollama ou OpenAI conforme o provider."""
    if provider == "openai":
        return openai_chat(
            prompt_system=prompt_system,
            prompt_user=prompt_user,
            model=model,
            base_url=base_url,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            api_key_env=api_key_env,
            image_path=image_path,
        )
    # default: ollama
    return ollama_chat(
        prompt_system=prompt_system,
        prompt_user=prompt_user,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_ctx=num_ctx,
        image_path=image_path,
    )


# ---------------------------------------------------------------------------
# Estimativa de tokens e truncamento de contexto
# ---------------------------------------------------------------------------


def estimate_tokens(text: str, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Estima o número de tokens de um texto.

    Heurística conservadora: ~3.8 chars/token para textos mistos
    (latim, grego, português). Para textos predominantemente em scripts
    não-latinos (grego antigo com diacríticos), pode ser ~3.0-3.5.
    """
    if not text:
        return 0
    return int(len(text) / chars_per_token) + 1


def response_token_usage(
    response: str, prompt_system: str, prompt_user: str
) -> TokenUsage:
    """Obtém usage real da resposta ou estima quando um provider/mock não o envia."""
    usage = getattr(response, "usage", None)
    if isinstance(usage, TokenUsage):
        return usage
    return TokenUsage(
        input_tokens=estimate_tokens(prompt_system + "\n" + prompt_user),
        output_tokens=estimate_tokens(str(response)),
        requests=1,
        estimated=True,
    )


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def _format_token_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def format_token_usage(usage: TokenUsage) -> str:
    approximation = "≈" if usage.estimated else "="
    requests = f"; chamadas={usage.requests}" if usage.requests > 1 else ""
    return (
        f"tokens{approximation}{_format_token_count(usage.total_tokens)} "
        f"(entrada={_format_token_count(usage.input_tokens)}, "
        f"saída={_format_token_count(usage.output_tokens)}{requests})"
    )


def truncate_context(
    contexto: str,
    page_text: str,
    num_ctx: int,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> str:
    """Trunca o contexto prévio para que tudo caiba na janela do modelo.

    Prioridade: a página atual NUNCA é truncada (é o input principal).
    O system prompt e a reserva de output são descontados primeiro.
    Se ainda não couber, o contexto prévio é cortado pelo final
    (mantendo o início, que costuma ter identificação do documento).

    Retorna o contexto (possivelmente truncado) e loga aviso se truncou.
    """
    budget_total = num_ctx - TOKEN_RESERVE_OUTPUT - TOKEN_RESERVE_SYSTEM
    if budget_total <= 0:
        log.warning(
            "num_ctx=%d é muito pequeno (reserve output=%d, system=%d). "
            "Enviando sem contexto prévio.",
            num_ctx,
            TOKEN_RESERVE_OUTPUT,
            TOKEN_RESERVE_SYSTEM,
        )
        return ""

    tokens_page = estimate_tokens(page_text, chars_per_token)
    budget_context = budget_total - tokens_page

    if budget_context <= 0:
        # Página sozinha já estoura — envia sem contexto
        log.warning(
            "Página sozinha (~%d tokens) excede budget disponível (%d tokens). "
            "Enviando sem contexto prévio.",
            tokens_page,
            budget_total,
        )
        return ""

    tokens_context = estimate_tokens(contexto, chars_per_token)

    if tokens_context <= budget_context:
        # Cabe tudo, sem truncamento
        return contexto

    # Precisa truncar — calcula chars máximos e corta
    max_chars = int(budget_context * chars_per_token)
    truncated = contexto[:max_chars]

    # Tenta cortar numa quebra de linha limpa
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars * 0.7:  # não perde mais que 30%
        truncated = truncated[:last_nl]

    truncated = (
        truncated.rstrip() + "\n[... contexto truncado para caber na janela do modelo]"
    )

    log.info(
        "Contexto truncado: %d→%d tokens (budget=%d, página=~%d tokens, janela=%d)",
        tokens_context,
        estimate_tokens(truncated, chars_per_token),
        budget_context,
        tokens_page,
        num_ctx,
    )
    return truncated


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Você é um REDATOR TÉCNICO de enciclopédias teológicas modernas.
Sua missão: REESCREVER o conteúdo da página em PORTUGUÊS DO BRASIL denso e direto.

REGRAS DE OURO (SEM EXCEÇÕES):
0. IDIOMA ÚNICO: 100% da resposta deve ser em PORTUGUÊS DO BRASIL. 
   - É PROIBIDO manter frases, expressões ou listas em latim (ex: não escreva 'ad Ephesios', escreva 'aos Efésios').
   - Termos em latim/grego são permitidos APENAS se forem conceitos técnicos sem tradução (ex: Logos, ousia, hypostasis).
1. ESTILO TELEGRÁFICO: Elimine preâmbulos ("A página trata", "O autor diz"). Use: [Conceito]: [Explicação técnica].
2. DENSIDADE: O papel é caro. Use frases nominais.
   - Ruim: "Inácio escreveu uma carta para os Romanos onde ele pede martírio."
   - Bom: "Epístola aos Romanos: petição pelo martírio; desejo de união com Cristo via feras."
3. FONTE: Use o texto latino para extrair o fatos, mas entregue o produto final totalmente em português.
4. FOCO SEMÂNTICO TOTAL: Ignore ruídos de OCR, caracteres corrompidos ou formatação de página.
   - Proibido comentar sobre a qualidade do reconhecimento de texto.
   - Extraia exclusivamente o sumo teológico, os argumentos filosóficos e a linha narrativa.
   - Se a página for totalmente ilegível, retorne apenas "Conteúdo ilegível" no campo de tradução e repita os outros campos.

OBSERVAÇÕES:
- A qualquer momento, pode-se iniciar um a nova obra ou autor durante o volume, quando acontecer, atualize os campos "autor" e "obra" no JSON para o autor atual.
- Se estamos trocando de obra (apresentada por um texto especial), é importante também atualizar a sintese_acumulada para refletir que é o início da obra.
- Se forem vários autores, pode separar os nomes com vírgula no campo autor.
- Se não houver autor ou obra identificável, pode deixar os campos como 'Não identificado'.

REGRAS DE FORMATAÇÃO (JSON PURO):
{
"autor": "Nome em português.",
"obra": "Título em português.",
"traducao_compacta": "CONCEITO 1: Texto denso em PT-BR. CONCEITO 2: Texto denso em PT-BR.",
"sintese_acumulada": "Resumo do progresso em português do Brasil."
}""".strip()


def remove_noise(text: str) -> str:
    # normalização básica
    # text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # remove indentação/espaços no início das linhas
    text = re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)

    # une hifenização de fim de linha (ex: "interver-\nsion" → "interversion")
    text = re.sub(r"-\n([a-zA-ZÀ-öø-ÿ])", r"\1", text)

    # elimina quebras de linha restantes e colapsa múltiplos espaços
    # text = text.replace("\n", " ")

    text = re.sub(r"[ \t]+", " ", text).strip()

    # remove XML tags se vier do LLM
    # text = re.sub(r"<[^>]+>", "", text)

    return text


def build_user_prompt(
    contexto_previo: str,
    page_text: str,
    page_num: int,
    doc_name: str,
    author: str,
    work: str,
    facsimile_attached: bool = False,
) -> str:
    parts: List[str] = []

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

    parts.append(
        f"### DADOS DA ENTRADA: Documento {doc_name} | Coleção: {serie_nome} | Página {page_num}"
    )

    page_text = remove_noise(page_text)

    if author and work:
        parts.append(f"### DADOS DA OBRA: Autor: {author} | Obra: {work}")

    if facsimile_attached:
        parts.append(
            "<facsimile_anexado>\n"
            "A imagem anexada é o fac-símile desta mesma página física. Use-a "
            "para conferir texto, diacríticos, colunas, títulos, autor e obra "
            "quando o OCR estiver truncado ou ambíguo. Não descreva a imagem "
            "nem mencione OCR/fac-símile na resposta; não invente trechos que "
            "não estejam legíveis em nenhuma das duas fontes.\n"
            "</facsimile_anexado>"
        )

    # Contexto Prévio (Sintese Acumulada das páginas anteriores)
    parts.append("<contexto_prévio_acumulado>")
    if contexto_previo:
        parts.append(contexto_previo)
    else:
        parts.append("(Início da obra: não há conteúdo anterior)")
    parts.append("</contexto_prévio_acumulado>\n")

    # Conteúdo da Página Atual
    parts.append("<conteudo_pagina_atual>")
    parts.append(page_text or "(OCR vazio; confira o fac-símile anexado.)")
    parts.append("</conteudo_pagina_atual>\n")

    # Instrução de fechamento alinhada ao JSON
    parts.append(
        "COMANDO:\n"
        "1. 'traducao_compacta': Tradução (para português do Brasil) direta e densa do conteúdo novo na <conteudo_pagina_atual>.\n"
        "2. 'sintese_acumulada': Evolução do argumento em português do Brasil. Adicione os pontos novos da página atual ao que já existe no <contexto_prévio_acumulado>. Se a sintese_acumulada estiver muito longa, torne-a concisa para os pontos mais relevantes para esta página.\n"
        "3. ESTAGNAÇÃO: Se a página for administrativamente irrelevante (índice, em branco, capa), a 'traducao_compacta' deve ser exatamente 'Conteúdo administrativo' e a 'sintese_acumulada' deve ser UMA CÓPIA IDENTICA do <contexto_prévio_acumulado>."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing da resposta da LLM
# ---------------------------------------------------------------------------


def parse_llm_response(raw: str) -> Tuple[str, str, str, str, bool]:
    """
    Retorna (resumo_pagina, sintese_pura, autor, obra, parsed_ok).
    parsed_ok=True somente quando o JSON foi parseado com sucesso.
    Se não conseguir parsear, retorna strings vazias e parsed_ok=False.
    """
    # Remove blocos de raciocínio que o modelo pode vazar mesmo com think=false
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    if len(clean) == 0:
        print(f"Raw response was empty: {raw}")

    def clean_summary_text(text: str) -> str:
        if not text:
            return ""
        return " ".join(text.replace("\r", "\n").replace("\n", " ").split()).strip()

    resumo_pagina = ""
    sintese = ""
    autor = ""
    obra = ""

    try:
        parsed = extract_json_object_response(clean)
    except ValueError:
        return "", "", "", "", False

    resumo_pagina = clean_summary_text(str(parsed.get("traducao_compacta", "")))
    sintese = clean_summary_text(str(parsed.get("sintese_acumulada", "")))
    autor = clean_summary_text(str(parsed.get("autor", "")))
    obra = clean_summary_text(str(parsed.get("obra", "")))

    required_keys_present = (
        "traducao_compacta" in parsed and "sintese_acumulada" in parsed
    )

    if not required_keys_present:
        return "", "", "", "", False

    return resumo_pagina, sintese, autor, obra, True


def is_page_administrative(text: str) -> bool:
    marker = unicodedata.normalize("NFKD", text or "")
    marker = "".join(char for char in marker if not unicodedata.combining(char))
    marker = " ".join(marker.casefold().split())
    return marker == "conteudo administrativo"


def is_page_ilegible(text: str) -> bool:
    t = (text or "").lower()
    return "conteúdo" in t and "ilegível" in t


def is_parseable_response(resumo_pagina: str, sintese_pura: str) -> bool:
    if is_page_administrative(resumo_pagina):
        return True

    if is_page_ilegible(resumo_pagina):
        return True

    """Verifica se a resposta tem conteúdo mínimo nos dois campos."""
    MIN_CHARS = 30  # mínimo ~uma frase curta
    return len(resumo_pagina) >= MIN_CHARS and len(sintese_pura) >= MIN_CHARS


def is_page_xml(text: str) -> bool:
    t = (text or "").lower().strip()
    return t.startswith("<") and t.endswith(">") and ("<pagina" in t or "</pagina" in t)


# ---------------------------------------------------------------------------
# Descoberta de volumes e páginas
# ---------------------------------------------------------------------------


def page_sort_key(path: Path) -> Tuple[int, str]:
    m = PAGE_NUM_RE.search(path.name)
    if m:
        return (int(m.group(1)), path.name)
    m2 = PAGE_NUM_FALLBACK_RE.search(path.name)
    if m2:
        return (int(m2.group(1)), path.name)
    return (0, path.name)


def page_number(path: Path) -> int:
    m = PAGE_NUM_RE.search(path.name)
    if m:
        return int(m.group(1))
    m2 = PAGE_NUM_FALLBACK_RE.search(path.name)
    return int(m2.group(1)) if m2 else 0


def discover_pages(text_dir: Path) -> List[Path]:
    """Retorna as páginas .txt ordenadas numericamente."""
    pages = sorted(text_dir.glob("*.txt"), key=page_sort_key)
    return pages


def discover_volumes(
    root: Path, series_filter: str, limit: Optional[int] = None
) -> List[Path]:
    """Descobre diretórios PG*/PL*/PO* que possuem subdir text/."""
    series_set = {s.strip().upper() for s in series_filter.split(",") if s.strip()}
    volumes: List[Path] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        m = SERIES_RE.match(d.name)
        if not m:
            continue
        if series_set and m.group(1).upper() not in series_set:
            continue
        text_dir = d / "text"
        if text_dir.is_dir():
            volumes.append(d)
    if limit:
        volumes = volumes[:limit]
    return volumes


# ---------------------------------------------------------------------------
# Pipeline v2 (shadow, esquema reduzido e cadeia serial verificável)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_V2 = """\
Você é o editor de metadados de uma biblioteca patrística digital. Produza um
resumo canônico em português do Brasil, útil ao leitor, à busca lexical e à
similaridade semântica. Trabalhe somente com as evidências fornecidas.

GUARDRAILS:
1. Prefácio, proêmio, introdução editorial, dedicatória, página de título,
   bibliografia, índice e aparato crítico têm conteúdo descritível. Não os
   chame de administrativos apenas por não serem o corpo principal da obra.
2. "administrative" é reservado a página vazia, encadernação, aviso técnico de
   digitalização ou ruído sem conteúdo bibliográfico, histórico, editorial,
   filosófico ou teológico recuperável. Explique administrative_reason.
3. Hints de catálogo e regex são evidência auxiliar. Confirme-os no OCR ou no
   fac-símile; registre contradições em source_conflicts e não invente.
4. Uma página pode terminar uma obra e iniciar outra. Nesse caso, devolva os
   segmentos na ordem física da página.
5. work_key só pode ser copiado literalmente da lista allowed_work_keys. Se a
   obra for incerta ou não estiver na lista, use string vazia.
6. Diferencie autor da obra de editor, tradutor, dedicante, dedicatário,
   comentador e impressor. Só registre contributors explicitamente sustentados.
7. cumulative_summary descreve somente a obra corrente. Prefira uma síntese
   de até 1.800 caracteres, mas preserve conteúdo indispensável em vez de
   cortar frases. Reinicie-o numa mudança segura de obra; fora disso, atualize-o
   com o conteúdo novo sem repetir mecanicamente a página anterior.
8. Referências bíblicas só entram quando sustentadas pelo OCR/fac-símile ou
   pelos candidatos determinísticos fornecidos.
9. Não descreva OCR, fac-símile ou seu raciocínio na saída. Não gere Markdown.

Retorne JSON puro exatamente com esta estrutura:
{
  "page_kinds": ["body|work_start|transition|preface|dedication|title_page|index|bibliography|critical_apparatus|administrative|illegible|other"],
  "segments": [
    {"order": 1, "kind": "body", "work_key": "", "summary": "resumo concreto do conteúdo novo"}
  ],
  "contributors": [
    {"name": "Nome", "role": "editor|translator|dedicant|dedicatee|commentator|printer"}
  ],
  "cumulative_summary": "síntese concisa da obra corrente",
  "source_conflicts": [],
  "administrative_reason": ""
}
""".strip()


def build_v2_user_prompt(
    *,
    documento: str,
    pagina_num: int,
    ocr_clean: str,
    previous_context: str,
    analysis: Any,
    index_hints: dict[str, Any],
    facsimile_attached: bool,
    active_work_key: str = "",
    lookahead: list[dict[str, Any]] | None = None,
    context_window: int | None = None,
) -> str:
    allowed_work_keys = sorted(
        {
            str(item.get("work_key") or "")
            for item in [
                *(index_hints.get("exact_start_candidates") or []),
                *(index_hints.get("containing_work_candidates") or []),
            ]
            if item.get("work_key")
        }
        | ({active_work_key} if active_work_key else set())
    )
    evidence = {
        "active_work_key": active_work_key,
        "header_original": analysis.header_original,
        "page_kind_hints": list(analysis.page_kind_hints),
        "exact_start_candidates": list(analysis.exact_start_candidates),
        "containing_work_candidates": list(analysis.containing_work_candidates),
        "scripture_candidates": list(analysis.scripture_candidates),
        "facsimile_reasons": list(analysis.facsimile_reasons),
    }
    parts_before_context = [
        f'<page document="{documento}" physical_page="{pagina_num}">',
        "<allowed_work_keys>",
        json.dumps(allowed_work_keys, ensure_ascii=False),
        "</allowed_work_keys>",
        "<deterministic_evidence>",
        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
        "</deterministic_evidence>",
        "<previous_context>",
    ]
    parts_after_context = ["</previous_context>"]
    if facsimile_attached:
        parts_after_context.extend(
            [
                "<facsimile_attached>",
                "A imagem anexada corresponde à página física atual. Use-a para "
                "conferir títulos, hierarquia, colunas, nomes e transições; não a descreva.",
                "</facsimile_attached>",
            ]
        )
    if lookahead:
        parts_after_context.extend(
            [
                "<deterministic_lookahead>",
                "As páginas seguintes servem apenas para resolver a fronteira/contexto "
                "da página atual. Não resuma seu conteúdo como se pertencesse à página atual.",
                json.dumps(lookahead, ensure_ascii=False, separators=(",", ":")),
                "</deterministic_lookahead>",
            ]
        )
    parts_after_context.extend(
        [
            "<current_page>",
            (ocr_clean or "(sem texto OCR recuperável)")[:30000],
            "</current_page>",
            "</page>",
        ]
    )
    context = previous_context or "(início do volume; sem contexto anterior)"
    if context_window is not None:
        fixed_prompt = "\n".join([*parts_before_context, *parts_after_context])
        context = fit_v2_previous_context(context, fixed_prompt, context_window)
    return "\n".join([*parts_before_context, context, *parts_after_context])


def fit_v2_previous_context(
    previous_context: str,
    fixed_user_prompt: str,
    context_window: int,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> str:
    """Reduz o contexto anterior somente quando a janela real exigir.

    O texto persistido nunca é alterado. Quando necessário, preservamos início
    e fim da síntese: o início identifica a obra e o fim contém o estado mais
    recente da corrente serial.
    """
    available_tokens = (
        context_window
        - TOKEN_RESERVE_OUTPUT
        - estimate_tokens(SYSTEM_PROMPT_V2, chars_per_token)
        - estimate_tokens(fixed_user_prompt, chars_per_token)
    )
    context_tokens = estimate_tokens(previous_context, chars_per_token)
    if context_tokens <= max(0, available_tokens):
        return previous_context
    if available_tokens <= 32:
        log.warning(
            "Prompt v2 ocupa a janela de %d tokens; enviando sem contexto anterior",
            context_window,
        )
        return ""

    marker = "\n[... contexto intermediário omitido para caber na janela ...]\n"
    max_chars = max(0, int(available_tokens * chars_per_token) - len(marker))
    if max_chars <= 64:
        return previous_context[-max_chars:] if max_chars else ""
    head_chars = max_chars // 3
    tail_chars = max_chars - head_chars
    fitted = previous_context[:head_chars].rstrip() + marker + previous_context[-tail_chars:].lstrip()
    log.info(
        "Contexto v2 ajustado à janela: %d→%d tokens (janela=%d)",
        context_tokens,
        estimate_tokens(fitted, chars_per_token),
        context_window,
    )
    return fitted


def _scripture_evidence_for_page(
    documento: str, pagina_num: int, pagina_file: str
) -> dict[str, Any]:
    try:
        from keywords_serial import collect_page_scripture_evidence

        return collect_page_scripture_evidence(
            {
                "documento": documento,
                "pagina_num": pagina_num,
                "pagina_file": pagina_file,
            }
        )
    except Exception as exc:
        log.warning(
            "[%s] p%d detector bíblico indisponível: %s",
            documento,
            pagina_num,
            exc,
        )
        return {"status": "detector_error", "candidates": [], "candidate_count": 0}


def _empty_page_candidate(previous_context: str) -> SummaryCandidate:
    summary = "Página física sem conteúdo textual ou bibliográfico recuperável."
    return SummaryCandidate(
        page_kinds=("administrative",),
        segments=(
            {"order": 1, "kind": "administrative", "work_key": "", "summary": summary},
        ),
        contributors=(),
        summary_display_pt=summary,
        cumulative_summary=previous_context,
        source_conflicts=(),
        administrative_reason="Página vazia ou sem conteúdo recuperável.",
        primary_page_kind="administrative",
        status="valid",
        validation_issues=(),
        context_reset=False,
        context_reset_confidence=0.0,
    )


def _lookahead_payload(pages: list[Path], current_index: int, amount: int) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for path in pages[current_index + 1 : current_index + 1 + max(0, amount)]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            clean, _meta = clean_ocr_text_optimized(raw)
        except OSError:
            continue
        payload.append(
            {
                "physical_page": page_number(path),
                "header": clean.splitlines()[0][:300] if clean else "",
                "text_preview": clean[:1800],
            }
        )
    return payload


def _call_v2_candidate(
    *,
    user_prompt: str,
    analysis: Any,
    allowed_work_keys: set[str],
    provider: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
    reasoning_effort: str,
    api_key_env: str,
    num_ctx: int,
    image_path: Path | None,
) -> tuple[SummaryCandidate, str, TokenUsage]:
    last_error = ""
    last_raw = ""
    usage = TokenUsage()
    for attempt in range(1, retries + 1):
        try:
            last_raw = llm_chat(
                prompt_system=SYSTEM_PROMPT_V2,
                prompt_user=user_prompt,
                provider=provider,
                model=model,
                base_url=base_url,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
                api_key_env=api_key_env,
                num_ctx=num_ctx,
                image_path=image_path,
            )
            usage += response_token_usage(last_raw, SYSTEM_PROMPT_V2, user_prompt)
            candidate = parse_summary_candidate(last_raw, analysis, allowed_work_keys)
            return candidate, last_raw, usage
        except Exception as exc:
            last_error = str(exc)
            log.warning("Tentativa v2 %d/%d falhou: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(min(10, 2 * attempt))
    raise ValueError(f"Falha na geração/validação v2: {last_error}; raw={last_raw[:500]}")


def _legacy_context_before(
    con: sqlite3.Connection, documento: str, pagina_num: int
) -> tuple[str, str]:
    row = con.execute(
        """
        SELECT id, resumo_global FROM resumos
         WHERE documento=? AND pagina_num<?
         ORDER BY pagina_num DESC LIMIT 1
        """,
        (documento, pagina_num),
    ).fetchone()
    if row is None:
        return "", ""
    context = str(row["resumo_global"] or "")
    return context, sha256_text("legacy-context", row["id"], context)


def _promote_v2_run_segments(
    con: sqlite3.Connection, run_id: int, documento: str
) -> int:
    rows = con.execute(
        "SELECT pagina_num, context_reset FROM resumo_generations WHERE run_id=? AND documento=? ORDER BY pagina_num",
        (run_id, documento),
    ).fetchall()
    if not rows:
        return 0
    boundaries = [0]
    boundaries.extend(
        index
        for index, row in enumerate(rows)
        if index > 0 and int(row["context_reset"] or 0) == 1
    )
    boundaries.append(len(rows))
    promoted = 0
    for start_index, end_index in zip(boundaries, boundaries[1:]):
        segment = rows[start_index:end_index]
        promoted += promote_segment(
            con,
            run_id,
            documento,
            int(segment[0]["pagina_num"]),
            int(segment[-1]["pagina_num"]),
        )
    return promoted


def process_volume_v2(
    *,
    volume_dir: Path,
    con: sqlite3.Connection,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
    provider: str,
    reasoning_effort: str,
    api_key_env: str,
    num_ctx: int,
    dry_run: bool,
    page_filter: int | None,
    verbose: bool,
    versions_con: sqlite3.Connection | None,
    facsimile_mode: str,
    facsimile_threshold: int,
    lookahead_pages: int,
    force_replace_from: int | None,
    force_replace_through: str,
    promote: bool,
    indices_db: Path,
) -> None:
    documento = volume_dir.name
    all_pages = discover_pages(volume_dir / "text")
    if not all_pages:
        log.warning("Nenhuma página encontrada em %s", volume_dir / "text")
        return
    if dry_run and page_filter is not None:
        selected = [path for path in all_pages if page_number(path) == page_filter]
        if not selected:
            log.warning("[%s] Página %d não encontrada", documento, page_filter)
            return
    else:
        selected = all_pages

    indices = open_indices_readonly(indices_db)
    try:
        hints_by_page = load_volume_index_hints(indices, documento, all_pages)
    finally:
        if indices is not None:
            indices.close()

    if dry_run:
        start_page = page_filter if page_filter is not None else page_number(selected[0])
        run = None
    else:
        init_v2_schema(con)
        force_through_num: int | None = None
        if force_replace_from is not None:
            if force_replace_through == "next-work":
                next_start = next_trusted_work_start(hints_by_page, force_replace_from)
                force_through_num = next_start - 1 if next_start is not None else page_number(all_pages[-1])
            elif force_replace_through == "end":
                force_through_num = page_number(all_pages[-1])
            else:
                force_through_num = int(force_replace_through)
            if force_through_num < force_replace_from:
                raise ValueError("--force-replace-through anterior a --force-replace-from")
            invalidated = invalidate_generation_range(
                con, documento, force_replace_from, force_through_num
            )
            log.info(
                "[%s] Reparação explícita p%d–p%d; %d geração(ões) promovida(s) invalidadas",
                documento,
                force_replace_from,
                force_through_num,
                invalidated,
            )
        run = get_or_create_run(
            con,
            documento=documento,
            pages=all_pages,
            provider=provider,
            model=model,
            force_from_page=force_replace_from,
            force_through_page=force_through_num,
        )
        start_page = resume_page_number(con, run, all_pages, force_replace_from)
        if start_page is None:
            if promote:
                promoted = _promote_v2_run_segments(con, run["id"], documento)
                log.info(
                    "[%s] Run v2 já concluído; %d página(s) promovida(s) agora",
                    documento,
                    promoted,
                )
            else:
                log.info("[%s] Run v2 já concluído; nada a retomar", documento)
            return
        selected = [
            path
            for path in all_pages
            if page_number(path) >= start_page
            and (force_through_num is None or page_number(path) <= force_through_num)
        ]

    legacy_rows = {
        int(row["pagina_num"]): row
        for row in con.execute(
            "SELECT id, pagina_num, resumo_global, ocr_result_id FROM resumos WHERE documento=?",
            (documento,),
        ).fetchall()
    }
    previous_generation = None
    previous_context, legacy_chain = _legacy_context_before(con, documento, start_page)
    if run is not None:
        prior = con.execute(
            "SELECT * FROM resumo_generations WHERE run_id=? AND documento=? AND pagina_num<? ORDER BY pagina_num DESC LIMIT 1",
            (run["id"], documento, start_page),
        ).fetchone()
        if prior is not None:
            previous_generation = prior
            previous_context = str(prior["cumulative_summary"] or "")
            legacy_chain = ""
    active_work_key = generation_last_work_key(previous_generation)

    processed = 0
    measured_items = 0
    volume_usage = TokenUsage()
    volume_llm_seconds = 0.0
    for page_path in selected:
        pagina_num = page_number(page_path)
        item_usage = TokenUsage()
        item_llm_seconds = 0.0
        try:
            raw_ocr = page_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            if run is not None:
                mark_run_blocked(con, run["id"], pagina_num, f"read_error:{exc}")
            raise
        clean_ocr, _clean_meta = clean_ocr_text_optimized(raw_ocr)
        hints = hints_by_page.get(pagina_num, {})
        scripture_evidence = _scripture_evidence_for_page(
            documento, pagina_num, page_path.name
        )
        analysis = analyze_page(
            documento=documento,
            pagina_num=pagina_num,
            pagina_file=page_path.name,
            page_text=raw_ocr,
            ocr_clean=clean_ocr,
            index_hints=hints,
            scripture_evidence=scripture_evidence,
        )
        if not active_work_key:
            active_work_key = initial_active_work_key(previous_generation, analysis)
        incoming_work_key = active_work_key
        ocr_result_id = None
        if versions_con is not None and _vdb is not None:
            try:
                current_ocr = _vdb.get_current_result(
                    versions_con, documento, pagina_num
                )
                if current_ocr is not None:
                    ocr_result_id = int(current_ocr["id"])
            except Exception as exc:
                log.debug(
                    "[%s] p%d ocr_result_id v2 indisponível: %s",
                    documento,
                    pagina_num,
                    exc,
                )
        attach_image = facsimile_mode == "always" or (
            facsimile_mode == "auto" and analysis.facsimile_score >= facsimile_threshold
        )
        image_path = resolve_page_facsimile(page_path) if attach_image else None
        labels = canonical_labels_from_hints(hints)
        allowed_keys = {
            str(item.get("work_key"))
            for item in [
                *(hints.get("exact_start_candidates") or []),
                *(hints.get("containing_work_candidates") or []),
            ]
            if item.get("work_key")
        }
        if active_work_key:
            allowed_keys.add(active_work_key)
        if analysis.is_empty and image_path is None:
            candidate = _empty_page_candidate(previous_context)
            raw_response = ""
            log.info(
                "[%s] p%d v2 sem chamada LLM; página vazia",
                documento,
                pagina_num,
            )
        else:
            prompt = build_v2_user_prompt(
                documento=documento,
                pagina_num=pagina_num,
                ocr_clean=clean_ocr,
                previous_context=previous_context,
                analysis=analysis,
                index_hints=hints,
                facsimile_attached=image_path is not None,
                active_work_key=active_work_key,
                context_window=num_ctx if provider == "ollama" else 128000,
            )
            log.info(
                "[%s] p%d v2 %s; fac-símile=%s score=%d (%s); iniciando",
                documento,
                pagina_num,
                model,
                "sim" if image_path else "não",
                analysis.facsimile_score,
                ",".join(analysis.facsimile_reasons) or "sem gatilhos",
            )
            try:
                call_started = time.monotonic()
                candidate, raw_response, call_usage = _call_v2_candidate(
                    user_prompt=prompt,
                    analysis=analysis,
                    allowed_work_keys=allowed_keys,
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    timeout=timeout,
                    retries=retries,
                    reasoning_effort=reasoning_effort,
                    api_key_env=api_key_env,
                    num_ctx=num_ctx,
                    image_path=image_path,
                )
                item_llm_seconds += time.monotonic() - call_started
                item_usage += call_usage
                if candidate.status == "context_provisional":
                    absolute_index = all_pages.index(page_path)
                    lookahead = _lookahead_payload(all_pages, absolute_index, lookahead_pages)
                    if lookahead:
                        prompt = build_v2_user_prompt(
                            documento=documento,
                            pagina_num=pagina_num,
                            ocr_clean=clean_ocr,
                            previous_context=previous_context,
                            analysis=analysis,
                            index_hints=hints,
                            facsimile_attached=image_path is not None,
                            active_work_key=active_work_key,
                            lookahead=lookahead,
                            context_window=num_ctx if provider == "ollama" else 128000,
                        )
                        call_started = time.monotonic()
                        candidate, raw_response, call_usage = _call_v2_candidate(
                            user_prompt=prompt,
                            analysis=analysis,
                            allowed_work_keys=allowed_keys,
                            provider=provider,
                            model=model,
                            base_url=base_url,
                            timeout=timeout,
                            retries=retries,
                            reasoning_effort=reasoning_effort,
                            api_key_env=api_key_env,
                            num_ctx=num_ctx,
                            image_path=image_path,
                        )
                        item_llm_seconds += time.monotonic() - call_started
                        item_usage += call_usage
            except Exception as exc:
                if run is not None:
                    mark_run_blocked(con, run["id"], pagina_num, str(exc))
                raise

        measured_items += 1
        volume_usage += item_usage
        volume_llm_seconds += item_llm_seconds
        log.info(
            "[%s] p%d concluída em %s; %s",
            documento,
            pagina_num,
            format_duration(item_llm_seconds),
            format_token_usage(item_usage),
        )

        candidate, active_work_key, work_resolution = reconcile_candidate_work_keys(
            candidate,
            analysis,
            incoming_work_key,
        )

        search_text = build_summary_search_text(
            candidate.summary_display_pt, candidate.segments, analysis, labels
        )
        embedding_text = build_embedding_text(candidate.summary_display_pt, labels)
        previous_chain = (
            str(previous_generation["chain_hash"] or "")
            if previous_generation is not None
            else legacy_chain
        )
        source_hash = sha256_text(
            SUMMARY_PROMPT_VERSION,
            documento,
            pagina_num,
            raw_ocr,
            ocr_result_id,
            json.dumps(hints, ensure_ascii=False, sort_keys=True),
            json.dumps(scripture_evidence, ensure_ascii=False, sort_keys=True),
            incoming_work_key,
            previous_chain,
        )
        if dry_run:
            print(
                json.dumps(
                    {
                        "documento": documento,
                        "pagina_num": pagina_num,
                        "status": candidate.status,
                        "facsimile_score": analysis.facsimile_score,
                        "facsimile_used": image_path is not None,
                        "page_kinds": candidate.page_kinds,
                        "segments": candidate.segments,
                        "contributors": candidate.contributors,
                        "cumulative_summary": candidate.cumulative_summary,
                        "validation_issues": candidate.validation_issues,
                        "search_text_pt": search_text,
                        "embedding_text": embedding_text,
                        **({"raw_response": raw_response} if verbose else {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            previous_context = candidate.cumulative_summary
            continue

        legacy = legacy_rows.get(pagina_num)
        generation = store_generation(
            con,
            run_id=run["id"],
            legacy_resumo_id=int(legacy["id"]) if legacy is not None else None,
            ocr_result_id=ocr_result_id,
            documento=documento,
            pagina_num=pagina_num,
            pagina_file=page_path.name,
            previous_generation=previous_generation,
            previous_chain_hash_override=legacy_chain,
            source_hash=source_hash,
            provider=provider,
            model=model,
            candidate=candidate,
            search_text_pt=search_text,
            embedding_text=embedding_text,
            analysis=analysis,
            raw_response=raw_response,
            facsimile_used=image_path is not None,
            tainted_by_page=pagina_num if candidate.status == "context_provisional" else None,
        )
        store_context_anchor(
            con,
            documento=documento,
            pagina_num=pagina_num,
            pagina_file=page_path.name,
            source_hash=source_hash,
            resolution=work_resolution,
        )
        processed += 1
        previous_generation = generation
        previous_context = candidate.cumulative_summary
        legacy_chain = ""
        if candidate.status == "context_provisional":
            mark_run_blocked(
                con,
                run["id"],
                pagina_num,
                "Contexto permaneceu provisório após lookahead determinístico",
            )
            log.warning("[%s] p%d mantida em shadow; cadeia pausada", documento, pagina_num)
            break
    else:
        if not dry_run:
            last_manifest_page = page_number(all_pages[-1])
            if force_replace_from is not None and force_through_num is not None and force_through_num < last_manifest_page:
                con.execute(
                    "UPDATE resumo_runs SET status='partial', next_page_num=? WHERE id=?",
                    (force_through_num + 1, run["id"]),
                )
                con.commit()
            else:
                mark_run_complete(con, run["id"])

    if not dry_run and promote:
        promoted = _promote_v2_run_segments(con, run["id"], documento)
        log.info("[%s] %d página(s) v2 promovida(s) em segmentos seguros", documento, promoted)
    log.info("[%s] %d página(s) processada(s) pelo v2", documento, processed)
    if measured_items:
        item_label = "item" if measured_items == 1 else "itens"
        log.info(
            "[%s] Métricas v2: %d %s; LLM em %s; %s; média=%s tokens/item",
            documento,
            measured_items,
            item_label,
            format_duration(volume_llm_seconds),
            format_token_usage(volume_usage),
            _format_token_count(volume_usage.total_tokens // measured_items),
        )


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------


def process_volume(
    volume_dir: Path,
    con: Optional[sqlite3.Connection],
    model: str,
    base_url: str,
    timeout: int,
    retries: int = 3,
    provider: str = "ollama",
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    num_ctx: int = DEFAULT_NUM_CTX,
    dry_run: bool = False,
    page_filter: Optional[int] = None,
    page_limit: Optional[int] = None,
    verbose: bool = False,
    fill_gaps: bool = False,
    fill_gaps_overlap: int = 10,
    versions_con: Optional[sqlite3.Connection] = None,
    include_facsimile: bool = False,
) -> None:
    """Processa todas as páginas de um volume sequencialmente.

    Se dry_run=True, chama o LLM mas imprime no stdout sem gravar no DB.
    page_filter filtra uma página específica (para testes rápidos).
    page_limit limita a quantidade de páginas a processar.
    fill_gaps verifica individualmente e preenche páginas que faltam no DB.
    include_facsimile anexa a imagem física pareada após conversão JPEG em memória.
    """
    doc_name = volume_dir.name
    text_dir = volume_dir / "text"
    pages = discover_pages(text_dir)

    if not pages:
        log.warning("Nenhuma página encontrada em %s", text_dir)
        return

    # Filtra página específica se solicitado
    if page_filter is not None:
        pages = [p for p in pages if page_number(p) == page_filter]
        if not pages:
            log.warning("[%s] Página %d não encontrada", doc_name, page_filter)
            return

    # Limita quantidade
    if page_limit is not None:
        pages = pages[:page_limit]

    total = len(pages)

    processed_set = set()
    last_done = None
    contexto = ""
    author_ctx = ""
    work_ctx = ""

    if dry_run:
        # Em dry run, tenta pegar contexto do DB se disponível
        if con is not None:
            contexto, author_ctx, work_ctx = get_last_resumo_global(con, doc_name)
    else:
        if con is not None:
            if fill_gaps:
                processed_set = get_processed_pages_set(con, doc_name)
            else:
                last_done = get_last_processed_page(con, doc_name)
                contexto, author_ctx, work_ctx = get_last_resumo_global(con, doc_name)

    if not fill_gaps and last_done is not None and not dry_run:
        log.info(
            "[%s] Retomando após página %d  (%d páginas total)",
            doc_name,
            last_done,
            total,
        )
    elif fill_gaps and not dry_run:
        log.info(
            "[%s] Modo fill-gaps: %d páginas no DB; reprocessando %d após cada lacuna.",
            doc_name,
            len(processed_set),
            fill_gaps_overlap,
        )

    pages_done = 0
    measured_items = 0
    volume_usage = TokenUsage()
    volume_llm_seconds = 0.0
    redo_until_page = 0
    overlap_forward = max(0, fill_gaps_overlap)
    for idx, page_path in enumerate(pages):
        pnum = page_number(page_path)

        if fill_gaps and not dry_run:
            missing_in_db = pnum not in processed_set
            in_overlap = redo_until_page > 0 and pnum <= redo_until_page

            if not missing_in_db and not in_overlap:
                continue

            if missing_in_db:
                redo_until_page = max(redo_until_page, pnum + overlap_forward)
                # Se a página estava em falta, precisamos do contexto cronológico da página anterior a ela
                if con is not None:
                    contexto, author_ctx, work_ctx = get_last_resumo_global(
                        con, doc_name, before_page=pnum
                    )
            # Em páginas de overlap mantemos o contexto recente gerado no loop
        else:
            # Pula páginas já processadas sequencialmente na lógica tradicional (só em gravação)
            if not dry_run and last_done is not None and pnum <= last_done:
                continue

        # Lê conteúdo da página
        try:
            page_text = page_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception as exc:
            log.error("[%s] Erro lendo %s: %s", doc_name, page_path.name, exc)
            continue

        image_path = resolve_page_facsimile(page_path) if include_facsimile else None
        if include_facsimile and image_path is None:
            log.debug(
                "[%s] p%d  fac-símile pareado indisponível; usando apenas OCR",
                doc_name,
                pnum,
            )

        # Tenta obter o ocr_result_id da versão corrente no versions DB
        ocr_result_id: Optional[int] = None
        if versions_con is not None and _vdb is not None:
            try:
                cur = _vdb.get_current_result(versions_con, doc_name, pnum)
                if cur is not None:
                    ocr_result_id = int(cur["id"])
            except Exception as exc:
                log.debug("[%s] p%d  ocr_result_id lookup falhou: %s", doc_name, pnum, exc)

        if not page_text and image_path is None:
            log.info(
                "[%s] Página %d vazia, marcando como administrativa e seguindo",
                doc_name,
                pnum,
            )
            if not dry_run and con is not None:
                resumo_pagina = "Conteúdo administrativo"
                resumo_global = contexto or "(Início da obra: não há conteúdo anterior)"
                summary_page_clean = clean_summary_page_for_embedding(resumo_pagina)
                summary_global_clean = clean_summary_global_for_search(resumo_global)
                save_resumo(
                    con=con,
                    documento=doc_name,
                    pagina_num=pnum,
                    pagina_file=page_path.name,
                    pagina_texto="",
                    resumo_pagina=resumo_pagina,
                    resumo_global=resumo_global,
                    modelo=model,
                    author_detected=author_ctx,
                    work_detected=work_ctx,
                    summary_page_clean=summary_page_clean,
                    summary_global_clean=summary_global_clean,
                    ocr_result_id=ocr_result_id,
                )
                if fill_gaps:
                    processed_set.add(pnum)
            measured_items += 1
            log.info(
                "[%s] p%d concluída em %s; %s",
                doc_name,
                pnum,
                format_duration(0.0),
                format_token_usage(TokenUsage()),
            )
            continue

        if not page_text and image_path is not None:
            log.info(
                "[%s] Página %d com OCR vazio; consultando o fac-símile",
                doc_name,
                pnum,
            )

        # Trunca contexto se necessário para caber na janela (relevante p/ Ollama)
        ctx_window = (
            num_ctx if provider == "ollama" else 128000
        )  # OpenAI tem janela grande
        contexto_safe = truncate_context(contexto, page_text, ctx_window)

        # Monta prompt
        user_prompt = build_user_prompt(
            contexto_safe,
            page_text,
            pnum,
            doc_name,
            author_ctx,
            work_ctx,
            facsimile_attached=image_path is not None,
        )

        prompt_tokens_est = estimate_tokens(user_prompt)
        log.info(
            "[%s] p%d  chamando LLM (%s)  ~%d tokens prompt",
            doc_name,
            pnum,
            model,
            prompt_tokens_est,
        )

        # Chama LLM com retry (inclui validação de parsing)
        raw_response = ""
        resumo_pagina = ""
        parsed_ok = False
        item_usage = TokenUsage()
        item_started = time.monotonic()
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
                    image_path=image_path,
                )
                item_usage += response_token_usage(
                    raw_response, SYSTEM_PROMPT, user_prompt
                )
                log.info(
                    "[%s] Página %d tentativa %d/%d – resposta LLM: %s",
                    doc_name,
                    pnum,
                    attempt,
                    retries,
                    raw_response,
                )
            except Exception as exc:
                log.warning(
                    "[%s] Página %d tentativa %d/%d – erro LLM: %s",
                    doc_name,
                    pnum,
                    attempt,
                    retries,
                    exc,
                )
                if attempt < retries:
                    time.sleep(5 * attempt)
                continue

            # Valida resposta não-vazia
            if not raw_response or not raw_response.strip():
                log.warning(
                    "[%s] Página %d tentativa %d/%d – resposta vazia",
                    doc_name,
                    pnum,
                    attempt,
                    retries,
                )
                if attempt > 4:
                    print(
                        f"Raw user prompt {user_prompt}; Raw response was empty: {raw_response}"
                    )

                if attempt < retries:
                    time.sleep(3 * attempt)
                continue

            # Valida parsing
            (
                resumo_pagina,
                sintese_pura,
                autor_detectado,
                obra_detectada,
                parsed_ok,
            ) = parse_llm_response(raw_response)
            if not parsed_ok:
                log.warning(
                    "[%s] Página %d tentativa %d/%d – JSON inválido; retry",
                    doc_name,
                    pnum,
                    attempt,
                    retries,
                )
                if attempt < retries:
                    time.sleep(3 * attempt)
                    continue

            if is_page_administrative(resumo_pagina):
                resumo_pagina = "Conteúdo administrativo"
                # Mantém continuidade da síntese para não quebrar parsing/fluidez
                if not sintese_pura.strip():
                    sintese_pura = (
                        contexto or "(Início da obra: não há conteúdo anterior)"
                    )
                autor_detectado = ""
                obra_detectada = ""

            if is_page_ilegible(resumo_pagina):
                is_xml = is_page_xml(page_text)
                if len(page_text) < 100 and not is_xml:
                    # página RAW
                    # TODO: Implementar lógica para lidar com páginas RAW chamando o main2.py na feature --refresh-pages e max retries
                    break

                if is_xml:
                    break

            if is_parseable_response(resumo_pagina, sintese_pura):
                break  # sucesso

            log.warning(
                "[%s] Página %d tentativa %d/%d – parsing falhou "
                "(resumo_pagina=%d chars, resumo_global=%d chars). "
                ": %s",
                doc_name,
                pnum,
                attempt,
                retries,
                len(resumo_pagina),
                len(sintese_pura),
                raw_response.replace("\n", " "),
            )
            if attempt < retries:
                time.sleep(3 * attempt)

        if not raw_response:
            log.error(
                "[%s] Página %d esgotou tentativas – nenhuma resposta, pulando",
                doc_name,
                pnum,
            )
            raise ValueError("Nenhuma resposta em todas as tentativas")

        if not parsed_ok:
            log.error(
                "[%s] Página %d esgotou tentativas – JSON inválido em todas as tentativas, pulando este volume",
                doc_name,
                pnum,
            )
            raise ValueError("JSON inválido em todas as tentativas")

        if not is_parseable_response(resumo_pagina, sintese_pura):
            log.error(
                "[%s] Página %d esgotou tentativas – JSON válido porém conteúdo mínimo ausente, pulando",
                doc_name,
                pnum,
            )
            raise ValueError("Conteúdo mínimo ausente, pulando este volume")

        item_llm_seconds = time.monotonic() - item_started
        measured_items += 1
        volume_usage += item_usage
        volume_llm_seconds += item_llm_seconds
        log.info(
            "[%s] p%d concluída em %s; %s",
            doc_name,
            pnum,
            format_duration(item_llm_seconds),
            format_token_usage(item_usage),
        )

        # Atualiza contexto para a próxima iteração
        contexto = sintese_pura
        author_ctx = autor_detectado
        work_ctx = obra_detectada

        pages_done += 1

        if fill_gaps and not dry_run:
            processed_set.add(pnum)

        if dry_run:
            # Imprime resultado formatado sem gravar
            sep = "─" * 70
            print(f"\n{sep}")
            print(
                f"📄  {doc_name}  │  Página {pnum}/{total}  │  {page_path.name}  │  modelo: {model}"
            )
            print(sep)
            print(f"\n{'━' * 30} RESUMO PÁGINA {'━' * 30}")
            print(resumo_pagina)
            print(f"\n{'━' * 30} SÍNTESE GLOBAL (pura) {'━' * 30}")
            print(sintese_pura)
            print(f"\nAutor detectado: {autor_detectado or '(vazio)'}")
            print(f"Obra detectada : {obra_detectada or '(vazio)'}")
            if verbose:
                print(f"\n{'━' * 30} RAW RESPONSE   {'━' * 30}")
                print(raw_response)
            print(sep)
            log.info(
                "[%s] Página %d/%d  (dry-run)  ✓  resumo_pag=%d chars  resumo_glob=%d chars",
                doc_name,
                pnum,
                total,
                len(resumo_pagina),
                len(sintese_pura),
            )
        else:
            # Salva no SQLite
            summary_page_clean = clean_summary_page_for_embedding(resumo_pagina)
            summary_global_clean = clean_summary_global_for_search(sintese_pura)
            save_resumo(
                con=con,
                documento=doc_name,
                pagina_num=pnum,
                pagina_file=page_path.name,
                pagina_texto=page_text,
                resumo_pagina=resumo_pagina,
                resumo_global=sintese_pura,
                modelo=model,
                author_detected=autor_detectado,
                work_detected=obra_detectada,
                summary_page_clean=summary_page_clean,
                summary_global_clean=summary_global_clean,
                ocr_result_id=ocr_result_id,
            )
            log.info(
                "[%s] Página %d/%d  (arquivo %s)  ✓",
                doc_name,
                pnum,
                total,
                page_path.name,
            )

    log.info("[%s] %d página(s) processada(s).", doc_name, pages_done)
    if measured_items:
        item_label = "item" if measured_items == 1 else "itens"
        log.info(
            "[%s] Métricas: %d %s; LLM em %s; %s; média=%s tokens/item",
            doc_name,
            measured_items,
            item_label,
            format_duration(volume_llm_seconds),
            format_token_usage(volume_usage),
            _format_token_count(volume_usage.total_tokens // measured_items),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resumo serial página-a-página de volumes da Patrística via Ollama ou OpenAI.",
    )
    p.add_argument(
        "--provider",
        choices=["ollama", "openai"],
        default="ollama",
        help="Provider LLM: ollama (default) ou openai.",
    )
    p.add_argument(
        "--pipeline",
        choices=["v2", "legacy"],
        default="v2",
        help="Pipeline de resumo: v2 shadow (default) ou legacy.",
    )
    p.add_argument(
        "--volume-dir",
        type=Path,
        default=None,
        help="Processar um único volume (ex: teste/PL001).",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Raiz contendo diretórios PG*/PL*/PO* (default: teste/).",
    )
    p.add_argument(
        "--series",
        default="PG,PL,PO",
        help="Séries a processar, separadas por vírgula (default: PG,PL,PO).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limitar quantidade de volumes a processar.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Caminho do SQLite de resumos (default: {DEFAULT_DB}).",
    )
    p.add_argument(
        "--indices-db",
        type=Path,
        default=DEFAULT_INDICES_DB,
        help=f"Índices de obras abertos somente para leitura (default: {DEFAULT_INDICES_DB}).",
    )
    p.add_argument(
        "--model",
        default=None,
        help=f"Modelo a usar (default: {DEFAULT_MODEL} para ollama, gpt-5-mini para openai).",
    )
    p.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help=f"URL base do Ollama (default: {DEFAULT_OLLAMA_URL}).",
    )
    p.add_argument(
        "--openai-url",
        default="https://api.openai.com/v1",
        help="URL base da API OpenAI (default: https://api.openai.com/v1).",
    )
    p.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Nome da variável de ambiente com a chave OpenAI (default: OPENAI_API_KEY).",
    )
    p.add_argument(
        "--reasoning-effort",
        choices=["", "none", "minimal", "low", "medium", "high"],
        default="high",
        help="Nível de reasoning para modelos OpenAI que suportam (default: high).",
    )
    p.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help=(
            "Janela de contexto em tokens para Ollama. Default automático: "
            f"{DEFAULT_V2_GEMMA4_NUM_CTX} no v2 com gemma4:cloud; "
            f"{DEFAULT_NUM_CTX} nos demais casos."
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout da requisição em segundos (default: {DEFAULT_TIMEOUT}).",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Número de tentativas por página em caso de erro (default: 3).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Retoma de onde parou no SQLite em cada diretório.",
    )
    p.add_argument(
        "--fill-gaps",
        action="store_true",
        help="Preenche buracos entre páginas processadas (útil após saltos no log).",
    )
    p.add_argument(
        "--fill-gaps-overlap",
        type=int,
        default=10,
        help="No modo --fill-gaps, reprocessa também N páginas subsequentes a cada lacuna (default: 10). Motivo: garantir coerência do resumo global.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Chama o LLM e imprime resultados sem gravar no banco. "
        "Útil para comparar modelos.",
    )
    p.add_argument(
        "--page",
        type=int,
        default=None,
        help="Processar apenas a página N (ex: --page 5).",
    )
    p.add_argument(
        "--force-replace-from",
        type=int,
        default=None,
        help="Reinicia explicitamente a cadeia v2 na página física N.",
    )
    p.add_argument(
        "--force-replace-through",
        default="next-work",
        help="Fim da reparação v2: next-work (default), end ou número físico.",
    )
    p.add_argument(
        "--lookahead-pages",
        type=int,
        default=3,
        help="Páginas futuras consultadas somente quando o contexto fica provisório (default: 3).",
    )
    p.add_argument(
        "--facsimile-mode",
        choices=["auto", "always", "never"],
        default="auto",
        help="Uso do fac-símile no v2 (default: auto por score determinístico).",
    )
    p.add_argument(
        "--facsimile-threshold",
        type=int,
        default=2,
        help="Score mínimo para anexar fac-símile em --facsimile-mode auto (default: 2).",
    )
    p.add_argument(
        "--promote",
        action="store_true",
        help="Promove somente segmentos v2 completos e seguros após o processamento.",
    )
    p.add_argument(
        "--facsimile",
        action="store_true",
        help=(
            "Compatibilidade: equivale a --facsimile-mode always no v2; no "
            "legacy anexa a imagem em todas as páginas disponíveis."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Mostra raw response completa no modo dry-run.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.lookahead_pages < 0:
        raise SystemExit("--lookahead-pages deve ser não negativo")
    if args.facsimile_threshold < 0:
        raise SystemExit("--facsimile-threshold deve ser não negativo")
    if args.pipeline == "v2" and args.page is not None and not args.dry_run:
        raise SystemExit(
            "No v2, --page isolada é apenas para --dry-run; use "
            "--force-replace-from para reparar a cadeia persistida."
        )
    if args.pipeline == "v2" and args.fill_gaps:
        raise SystemExit(
            "--fill-gaps pertence ao pipeline legacy. No v2 use "
            "--force-replace-from; o alcance padrão vai até a próxima obra segura."
        )
    if args.facsimile:
        args.facsimile_mode = "always"

    # Resolve modelo default conforme provider
    if args.model is None:
        if args.provider == "openai":
            args.model = "gpt-5-mini"
        else:
            args.model = DEFAULT_MODEL
    if args.num_ctx is None:
        if (
            args.pipeline == "v2"
            and args.provider == "ollama"
            and str(args.model).lower().startswith("gemma4:")
        ):
            args.num_ctx = DEFAULT_V2_GEMMA4_NUM_CTX
        else:
            args.num_ctx = DEFAULT_NUM_CTX
    if args.num_ctx <= 0:
        raise SystemExit("--num-ctx deve ser positivo")

    # Resolve base_url conforme provider
    if args.provider == "openai":
        base_url = args.openai_url
    else:
        base_url = args.ollama_url

    # Conecta DB (opcional em dry-run)
    con: Optional[sqlite3.Connection] = None
    if args.dry_run:
        # Tenta abrir DB read-only para buscar contexto, mas não é obrigatório
        if args.db.exists():
            try:
                con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                log.info("DB (read-only): %s", args.db)
            except Exception:
                log.info("DB indisponível, dry-run sem contexto prévio.")
        else:
            log.info("DB não encontrado, dry-run sem contexto prévio.")
        log.info("🔍 Modo DRY-RUN — resultados serão impressos, nada será gravado.")
    else:
        con = connect_db(args.db)
        init_resumo_schema(con)
        ensure_resumos_embedding_schema(con)
        if args.pipeline == "v2":
            init_v2_schema(con)
        log.info("DB: %s", args.db)

    log.info("Provider: %s   Modelo: %s", args.provider, args.model)
    if args.provider == "ollama":
        log.info("Janela de contexto: %d tokens", args.num_ctx)
    if args.pipeline == "v2":
        log.info(
            "Pipeline: v2 shadow (%s; promoção=%s; fac-símile=%s score≥%d)",
            SUMMARY_PROMPT_VERSION,
            "ativa" if args.promote else "desativada",
            args.facsimile_mode,
            args.facsimile_threshold,
        )
    elif args.facsimile:
        log.info(
            "Fac-símile: ativo (JPEG quality=%d; modelo deve aceitar imagens)",
            FACSIMILE_JPEG_QUALITY,
        )
    if args.provider == "openai":
        log.info("Reasoning effort: %s", args.reasoning_effort)

    # Abre conexão read-only com ocr_versions.db (se disponível)
    versions_con: Optional[sqlite3.Connection] = None
    if _vdb is not None:
        versions_db_path = PROJECT_ROOT / "data" / "ocr_versions.db"
        if versions_db_path.exists():
            try:
                versions_con = sqlite3.connect(
                    f"file:{versions_db_path}?mode=ro", uri=True
                )
                versions_con.row_factory = sqlite3.Row
                log.info("Versions DB (read-only): %s", versions_db_path)
            except Exception as exc:
                log.warning("Versions DB indisponível: %s", exc)

    # Descobre volumes a processar
    if args.volume_dir:
        volumes = [args.volume_dir.resolve()]
    else:
        volumes = discover_volumes(args.root, args.series, args.limit)

    if not volumes:
        log.warning("Nenhum volume encontrado.")
        sys.exit(1)

    log.info("Volumes a processar: %d", len(volumes))

    fatal_errors = 0
    interrupted = False
    for vol in volumes:
        log.info("=" * 60)
        log.info("Iniciando volume: %s", vol.name)
        log.info("=" * 60)
        t0 = time.time()
        volume_lock: TextIO | None = None
        try:
            if not args.dry_run:
                volume_lock = acquire_volume_processing_lock(args.db, vol.name)
            if args.pipeline == "v2":
                if con is None:
                    raise RuntimeError("Conexão de resumos indisponível")
                process_volume_v2(
                    volume_dir=vol,
                    con=con,
                    model=args.model,
                    base_url=base_url,
                    timeout=args.timeout,
                    retries=args.retries,
                    provider=args.provider,
                    reasoning_effort=args.reasoning_effort,
                    api_key_env=args.api_key_env,
                    num_ctx=args.num_ctx,
                    dry_run=args.dry_run,
                    page_filter=args.page,
                    verbose=args.verbose,
                    versions_con=versions_con,
                    facsimile_mode=args.facsimile_mode,
                    facsimile_threshold=args.facsimile_threshold,
                    lookahead_pages=args.lookahead_pages,
                    force_replace_from=args.force_replace_from,
                    force_replace_through=args.force_replace_through,
                    promote=args.promote,
                    indices_db=args.indices_db,
                )
            else:
                process_volume(
                    volume_dir=vol,
                    con=con,
                    model=args.model,
                    base_url=base_url,
                    timeout=args.timeout,
                    retries=args.retries,
                    provider=args.provider,
                    reasoning_effort=args.reasoning_effort,
                    api_key_env=args.api_key_env,
                    num_ctx=args.num_ctx,
                    dry_run=args.dry_run,
                    page_filter=args.page,
                    page_limit=args.limit if args.dry_run else None,
                    verbose=args.verbose,
                    fill_gaps=args.fill_gaps,
                    fill_gaps_overlap=args.fill_gaps_overlap,
                    versions_con=versions_con,
                    include_facsimile=args.facsimile,
                )
        except KeyboardInterrupt:
            log.info("Interrompido pelo usuário. Progresso salvo no DB.")
            interrupted = True
            break
        except Exception as exc:
            log.error("Erro fatal no volume %s: %s", vol.name, exc, exc_info=True)
            fatal_errors += 1
            continue
        finally:
            release_volume_processing_lock(volume_lock)
        elapsed = time.time() - t0
        log.info("Volume %s concluído em %s", vol.name, format_duration(elapsed))

    if con is not None:
        con.close()
    if versions_con is not None:
        versions_con.close()
    log.info("Fim.")
    if interrupted:
        raise SystemExit(130)
    if fatal_errors:
        log.error("%d volume(s) terminaram com erro fatal.", fatal_errors)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
