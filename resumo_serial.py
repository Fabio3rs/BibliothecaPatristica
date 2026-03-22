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
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# Permite importar utilitários de limpeza compartilhados
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from test_limpeza_ocr import (  # type: ignore  # noqa: E402
    clean_summary_global_for_search,
    clean_summary_page_for_embedding,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_ROOT = PROJECT_ROOT / "teste"
DEFAULT_MODEL = "qwen3:30b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300  # segundos – resumos longos podem demorar

DEFAULT_NUM_CTX = 16384  # janela de contexto padrão do Ollama (tokens)
TOKEN_RESERVE_OUTPUT = 2048  # tokens reservados para a resposta do modelo
TOKEN_RESERVE_SYSTEM = 600  # estimativa p/ system prompt + boilerplate
CHARS_PER_TOKEN = 3.8  # heurística: ~3.8 chars/token (latim/grego/pt misturados)

SERIES_RE = re.compile(r"^(PG|PL|PO)(\d+)(.*)$")
PAGE_NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
PAGE_NUM_FALLBACK_RE = re.compile(r"(\d+)(?=\.[^.]+$)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("resumo_serial")

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")  # 30s de espera em caso de lock
    con.execute("PRAGMA foreign_keys = ON")
    return con


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
        con.execute("ALTER TABLE resumos ADD COLUMN resumo_global_hdbscan_group_id INTEGER")

        # Resumo da página provavelmente irá variar um pouco mais conforme o autor for argumentando
        # Iremos testar 15D UMAP
        con.execute("ALTER TABLE resumos ADD COLUMN resumo_pagina_hdbscan_group_id INTEGER")
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
) -> None:
    con.execute(
        """INSERT OR REPLACE INTO resumos
           (documento, pagina_num, pagina_file, pagina_texto,
            resumo_pagina, resumo_global, modelo, criado_em,
            author_detected, work_detected, summary_page_clean, summary_global_clean)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        ),
    )
    con.commit()


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
) -> str:
    """Chama Ollama /api/chat e retorna a resposta como texto puro."""
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "options": {
            "temperature": 0.3,
            "num_ctx": num_ctx,
        },
        "reasoning_effort": "high",
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
    if not content.strip() and thinking.strip():
        return thinking.strip()

    return content.strip()


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
) -> str:
    """Chama OpenAI /chat/completions e retorna a resposta como texto puro."""
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Variável de ambiente {api_key_env} não definida. "
            f"Defina-a com sua chave de API OpenAI."
        )

    url = f"{base_url}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "response_format": { "type": "json_object" },
        "top_p": 1.0,
        "service_tier": "flex",
    }

    # reasoning_effort é suportado por modelos com reasoning (o1, o3, gpt-5-mini, etc.)
    if reasoning_effort and len(reasoning_effort) > 0:
        payload["reasoning_effort"] = reasoning_effort

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
    timeout: int = DEFAULT_TIMEOUT,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    num_ctx: int = DEFAULT_NUM_CTX,
) -> str:
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
        )
    # default: ollama
    return ollama_chat(
        prompt_system=prompt_system,
        prompt_user=prompt_user,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_ctx=num_ctx,
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

OBSERVAÇÕES:
- A qualquer momento, pode-se iniciar um a nova obra ou autor durante o volume, quando acontecer, atualize os campos "autor" e "obra" no JSON para o autor atual.
- Se estamos trocando de obra (apresentada por um texto especial), é importante também atualizar a sintese_acumulada para refletir que é o início da obra.
- Se forem vários autores, pode separar os nomes com vírgula no campo autor.

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
    contexto_previo: str, page_text: str, page_num: int, doc_name: str, author: str, work: str
) -> str:
    parts: List[str] = []

    # Cabeçalho técnico para o modelo se localizar
    # Mapeamento de siglas para nomes extensos
    MAPA_SERIES = {
        "PG": "Patrologia Graeca (Migne)",
        "PL": "Patrologia Latina (Migne)",
        "PO": "Patrologia Orientalis (Graffin/Nau)",
        "ACO": "Acta Conciliorum Oecumenicorum", # Caso decidas expandir no futuro
    }

    # No build_user_prompt, extraímos o prefixo (ex: 'PG' de 'PG005')
    prefixo = "".join(re.findall(r'[A-Za-z]+', doc_name))
    serie_nome = MAPA_SERIES.get(prefixo, "Coleção Patrística")

    parts.append(f"### DADOS DA ENTRADA: Documento {doc_name} | Coleção: {serie_nome} | Página {page_num}")

    page_text = remove_noise(page_text)

    if author and work:
        parts.append(f"### DADOS DA OBRA: Autor: {author} | Obra: {work}")

    # Contexto Prévio (Sintese Acumulada das páginas anteriores)
    parts.append("<contexto_prévio_acumulado>")
    if contexto_previo:
        parts.append(contexto_previo)
    else:
        parts.append("(Início da obra: não há conteúdo anterior)")
    parts.append("</contexto_prévio_acumulado>\n")

    # Conteúdo da Página Atual
    parts.append("<conteudo_pagina_atual>")
    parts.append(page_text)
    parts.append("</conteudo_pagina_atual>\n")

    # Instrução de fechamento alinhada ao JSON
    parts.append(
        "COMANDO:\n"
        "1. 'traducao_compacta': Tradução (para português do Brasil) direta e densa do conteúdo novo na <conteudo_pagina_atual>.\n"
        "2. 'sintese_acumulada': Evolução do argumento em português do Brasil. Adicione os pontos novos da página atual ao que já existe no <contexto_prévio_acumulado>.\n"
        "3. ESTAGNAÇÃO: Se a página for administrativamente irrelevante (índice, em branco, capa), a 'traducao_compacta' deve ser exatamente 'Conteúdo administrativo' e a 'sintese_acumulada' deve ser UMA CÓPIA IDENTICA do <contexto_prévio_acumulado>."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing da resposta da LLM
# ---------------------------------------------------------------------------


def parse_llm_response(raw: str) -> Tuple[str, str, str, str]:
    """
    Retorna (resumo_pagina, sintese_pura, autor, obra).
    Se não conseguir parsear, retorna strings vazias para indicar falha.
    """
    # Remove blocos de raciocínio que o modelo pode vazar mesmo com think=false
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    if len(clean) == 0:
        print(f"Raw response was empty: {raw}")

    # ------------------------------------------------------------
    # Utilitários de limpeza simples (mínimo de regex)
    # ------------------------------------------------------------
    def clean_summary_text(text: str) -> str:
        if not text:
            return ""
        return " ".join(text.replace("\r", "\n").replace("\n", " ").split()).strip()

    def strip_code_fence(text: str) -> str:
        if "```" in text:
            m = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
            if m:
                return m.group(1)
        return text

    def try_parse_json_like(text: str) -> Optional[dict]:
        cand = strip_code_fence(text)
        # Pega apenas o primeiro bloco {...} se houver ruído antes/depois
        m = re.search(r"\{.*\}", cand, re.DOTALL)
        if m:
            cand = m.group(0)
        try:
            return json.loads(cand)
        except Exception:
            return None

    def pick_line_prefix(lines: list[str], prefix: str) -> Optional[str]:
        pref = prefix.lower()
        for ln in lines:
            if ln.lower().startswith(pref):
                return ln[len(prefix):].strip()
        return None

    resumo_pagina = ""
    sintese = ""
    autor = ""
    obra = ""

    parsed = try_parse_json_like(clean)
    if isinstance(parsed, dict):
        resumo_pagina = clean_summary_text(str(parsed.get("traducao_compacta", "")))
        sintese = clean_summary_text(str(parsed.get("sintese_acumulada", "")))
        autor = clean_summary_text(str(parsed.get("autor", "")))
        obra = clean_summary_text(str(parsed.get("obra", "")))
        return resumo_pagina, sintese, autor, obra

    # Fallback leve: procura linhas com "autor:" / "obra:" e o resto vira síntese
    lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    autor_line = pick_line_prefix(lines, "autor:")
    obra_line = pick_line_prefix(lines, "obra:")
    if autor_line:
        autor = clean_summary_text(autor_line)
    if obra_line:
        obra = clean_summary_text(obra_line)

    # Remove eventuais linhas de autor/obra para formar síntese
    filtered = []
    for ln in lines:
        low = ln.lower()
        if low.startswith("autor:") or low.startswith("obra:") or low.startswith("resumo global:"):
            continue
        filtered.append(ln)
    if filtered:
        sintese = clean_summary_text(" ".join(filtered))

    # Por padrão, resumo_pagina recebe a mesma síntese quando não há separação clara
    resumo_pagina = resumo_pagina or sintese

    return resumo_pagina, sintese, autor, obra


def is_page_administrative(text: str) -> bool:
    t = (text or "").lower()
    return "administrativ" in t  # cobre "administrativo", "administrativa", etc.


def is_parseable_response(resumo_pagina: str, sintese_pura: str) -> bool:
    if is_page_administrative(resumo_pagina):
        return True

    """Verifica se a resposta tem conteúdo mínimo nos dois campos."""
    MIN_CHARS = 30  # mínimo ~uma frase curta
    return len(resumo_pagina) >= MIN_CHARS and len(sintese_pura) >= MIN_CHARS


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
) -> None:
    """Processa todas as páginas de um volume sequencialmente.

    Se dry_run=True, chama o LLM mas imprime no stdout sem gravar no DB.
    page_filter filtra uma página específica (para testes rápidos).
    page_limit limita a quantidade de páginas a processar.
    fill_gaps verifica individualmente e preenche páginas que faltam no DB.
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

        if not page_text:
            log.info("[%s] Página %d vazia, marcando como administrativa e seguindo", doc_name, pnum)
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
                )
                if fill_gaps:
                    processed_set.add(pnum)
            continue

        # Trunca contexto se necessário para caber na janela (relevante p/ Ollama)
        ctx_window = (
            num_ctx if provider == "ollama" else 128000
        )  # OpenAI tem janela grande
        contexto_safe = truncate_context(contexto, page_text, ctx_window)

        # Monta prompt
        user_prompt = build_user_prompt(
            contexto_safe, page_text, pnum, doc_name, author_ctx, work_ctx
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
                )
                log.info("[%s] Página %d tentativa %d/%d – resposta LLM: %s",
                         doc_name, pnum, attempt, retries, raw_response)
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
            resumo_pagina, sintese_pura, autor_detectado, obra_detectada = parse_llm_response(
                raw_response
            )
            if is_page_administrative(resumo_pagina):
                resumo_pagina = "Conteúdo administrativo"
                # Mantém continuidade da síntese para não quebrar parsing/fluidez
                if not sintese_pura.strip():
                    sintese_pura = contexto or "(Início da obra: não há conteúdo anterior)"
                autor_detectado = ""
                obra_detectada = ""
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
            continue

        if not is_parseable_response(resumo_pagina, sintese_pura):
            log.error(
                "[%s] Página %d esgotou tentativas – parsing falhou, "
                "usando raw como fallback",
                doc_name,
                pnum,
            )
            # Fallback: usa raw inteiro para ambos (melhor que perder a página)
            resumo_pagina = raw_response.strip()
            sintese_pura = raw_response.strip()
            autor_detectado = ""
            obra_detectada = ""

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
            )
            log.info(
                "[%s] Página %d/%d  (arquivo %s)  ✓",
                doc_name,
                pnum,
                total,
                page_path.name,
            )

    log.info("[%s] %d página(s) processada(s).", doc_name, pages_done)


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
        default=DEFAULT_NUM_CTX,
        help=f"Janela de contexto em tokens para Ollama (default: {DEFAULT_NUM_CTX}). "
        "O contexto prévio será truncado automaticamente se o total estimado exceder este valor.",
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
        "-v",
        "--verbose",
        action="store_true",
        help="Mostra raw response completa no modo dry-run.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    # Resolve modelo default conforme provider
    if args.model is None:
        if args.provider == "openai":
            args.model = "gpt-5-mini"
        else:
            args.model = DEFAULT_MODEL

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
        log.info("DB: %s", args.db)

    log.info("Provider: %s   Modelo: %s", args.provider, args.model)
    if args.provider == "openai":
        log.info("Reasoning effort: %s", args.reasoning_effort)

    # Descobre volumes a processar
    if args.volume_dir:
        volumes = [args.volume_dir.resolve()]
    else:
        volumes = discover_volumes(args.root, args.series, args.limit)

    if not volumes:
        log.warning("Nenhum volume encontrado.")
        sys.exit(1)

    log.info("Volumes a processar: %d", len(volumes))

    for vol in volumes:
        log.info("=" * 60)
        log.info("Iniciando volume: %s", vol.name)
        log.info("=" * 60)
        t0 = time.time()
        try:
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
            )
        except KeyboardInterrupt:
            log.info("Interrompido pelo usuário. Progresso salvo no DB.")
            break
        except Exception as exc:
            log.error("Erro fatal no volume %s: %s", vol.name, exc, exc_info=True)
            continue
        elapsed = time.time() - t0
        log.info("Volume %s concluído em %.1f s", vol.name, elapsed)

    if con is not None:
        con.close()
    log.info("Fim.")


if __name__ == "__main__":
    main()
