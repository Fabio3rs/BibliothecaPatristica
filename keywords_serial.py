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
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import List, Optional, Tuple

# Heurísticas de ruído/rejeição de página OCR
from test_limpeza_ocr import clean_ocr_text_optimized, classify_page_noise

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUMOS_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_KEYWORDS_DB = PROJECT_ROOT / "data" / "patristica_keywords.db"
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

VALID_SOURCES = [
    "resumo_pagina",
    "resumo_global",
    "pagina_texto",
    "resumo+pagina",
    "tudo",
]


TEMPERATURE_DEFAULT = 0.1
TOP_P_DEFAULT = 0.3


logging.basicConfig(
    level=logging.INFO,
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
) -> str:
    url = f"{base_url}/api/chat"
    payload: dict = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "options": {
            "temperature": TEMPERATURE_DEFAULT,
            "top_p": TOP_P_DEFAULT,
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
) -> str:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Variável de ambiente {api_key_env} não definida.")
    url = f"{base_url}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "response_format": {"type": "json_object"},
        "service_tier": "flex",
    }
    if reasoning_effort:
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
    timeout: int = DEFAULT_TIMEOUT_OLLAMA,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
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
        )
    return ollama_chat(
        prompt_system=prompt_system,
        prompt_user=prompt_user,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_ctx=num_ctx,
        think=think,
    )


# ---------------------------------------------------------------------------
# Prompt de keywords (edite à vontade para testar)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Você é um Especialista em Catalogação de Patrística. Sua tarefa é gerar metadados precisos cruzando o texto original e seu resumo técnico.

### DIRETRIZES DE EXTRAÇÃO:
1. VALIDAÇÃO DE ENTIDADES (Texto Original): Use o texto bruto para extrair a grafia exata de nomes próprios (Santos, Autores, Hereges), Cidades e Obras citadas. Ignore erros de OCR, mas mantenha a terminologia técnica no original (latim/grego) caso presente no texto_original (ex: 'Logos', 'Ousia', etc.).
2. MAPEAMENTO TEMÁTICO (Resumo): Bússola temática: resumo_global e resumo_da_pagina para identificar os grandes temas teológicos (ex: 'Cristologia', 'Soteriologia', 'Eclesiologia', etc.), entretanto, cite a keyword apenas se a ver dentro do texto_original.
3. HIERARQUIA: Priorize keywords que apareçam em texto_original e que definam o núcleo do argumento da página. Cite em ordem aproximada de importância, mais importante primeiro, em keywords_ranking.
4. LITERALIDADE: Preserve a literalidade dos textos originais, evitando paráfrases ou interpretações, usando traduções literais para PT-BR quando possível ou citando no original quando termo consagrado. Se for uma citação bíblica, evite abreviar, cite o nome completo do livro.

OBSERVAÇÕES:
- resumo_global se trata do contexto geral da obra até agora, enquanto resumo_da_pagina foca em aspectos específicos desta página.
- Se forem vários autores na página, extraia todos os nomes e trate-os como entidades separadas.
- Normalização de Nomes: Para nomes de pessoas, use a forma canônica em português sempre que possível (ex: 'Ioannes Chrysostomus' -> 'João Crisóstomo'), a menos que seja um autor muito obscuro, mantendo a grafia do original.
- Não abrevie livros bíblicos nem nomes de obras/autores; escreva o nome completo (ex.: ‘Apocalipse de João’, ‘Atos dos Apóstolos’).
- texto_original é a âncora
- notas são totalmente opcionais, não coloque explicações dentro de keywords_ranking ou categorias, se precisar explicar algo use "notas"

### FORMATO DE SAÍDA (JSON):
Retorne EXCLUSIVAMENTE um JSON puro, sem markdown:
{
  "keywords_ranking": ["Termo 1", "Termo 2", "..."],
  "categorias": {
    "pessoas": ["Nome 1", "Nome 2"],
    "obras_citadas": ["Obra A", "Obra B"],
    "temas_teologicos": ["Tema X", "Tema Y"],
    "termos_tecnicos_lat_gr": ["Termo 1", "Termo 2"]
  },
  "notas": { "Termo 1": "Nota sobre o termo 1 (opcional)" }
}
"""


def replace_linebreak(text: str) -> str:
    # Captura hífen padrão, meia-risca (–) ou travessão (—)
    # seguido de espaços/quebras e remove também espaços no início da próxima linha
    return re.sub(r"[-–—]\s*[\r\n]+\s*", "", text)


def build_user_prompt(
    row: dict,
    source: str,
    doc_name: str,
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
        "Extraia as keywords do texto_original seguindo rigorosamente "
        "o formato de resposta especificado."
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

        # Split por , ou ; (respeitando itens entre aspas)
        items = re.split(r"\s*[,;]\s*", cat_content)
        items = [it.strip().strip("*").strip() for it in items if it.strip()]
        # Remove itens vazios ou muito longos (noise)
        items = [it for it in items if 1 < len(it) < 200]

        if items:
            categorias[cat_name] = items

    return categorias


def _strip_think_block(raw: str) -> str:
    """Remove blocos <think>...</think> da resposta (chain-of-thought vazado)."""
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


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
    clean = _strip_think_block(raw)

    # Tenta parsing direto de JSON no formato esperado do system prompt
    try:
        data = json.loads(clean)
    except Exception:
        data = None

    if isinstance(data, dict):
        kws_from_json: List[str] = []
        if isinstance(data.get("keywords_ranking"), list):
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
# Validação e limpeza de keywords existentes
# ---------------------------------------------------------------------------


def normalize_keywords_payload(raw: str) -> Tuple[KeywordsResult, Optional[str]]:
    """
    Converte keywords_json em estrutura padrão {"keywords": [...], "categorias": {...}}
    e sinaliza problemas de parsing.
    """
    if not raw or not raw.strip():
        return {"keywords": []}, "missing_keywords_json"

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
        kws = data.get("keywords") or data.get("keywords_ranking")
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

        return result, parse_issue

    return {"keywords": []}, "unexpected_structure"


def _strip_markdown_wrappers(text: str) -> str:
    """Remove bullets/ênfase/code simples que podem envolver a keyword."""
    t = (text or "").strip()
    # Fences e blocos de code inline
    t = re.sub(r"^```[\w-]*\s*|\s*```$", "", t, flags=re.DOTALL)
    t = re.sub(r"`{1,3}([^`]+?)`{1,3}", r"\1", t)
    # Bullets / headers iniciais
    t = re.sub(r"^(?:[>#]+|\*+|[-+\u2022•]+|#+)\s*", "", t)
    # Ênfase simples
    t = re.sub(r"\*{1,3}([^*]+?)\*{1,3}", r"\1", t)
    t = re.sub(r"_{1,3}([^_]+?)_{1,3}", r"\1", t)
    # Aspas/fences simétricos
    while len(t) >= 2 and t[0] == t[-1] and t[0] in "*_`'\"":
        t = t[1:-1].strip()
    return t


def _deep_clean_keyword(text: str) -> str:
    """Normalização agressiva para keywords vindas do LLM (quotes/ênfase/whitespace)."""
    t = _strip_markdown_wrappers(text)
    t = unicodedata.normalize("NFKC", t or "")
    t = t.replace("\u00a0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    # Keep parentheses intact to avoid stripping closing ")" from keywords that carry context
    strip_chars = " \"'«»“”‘’[]{}|\\/–—-:;.,!?·•*&"
    t = t.strip(strip_chars)
    return t


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

    for raw in items:
        if not isinstance(raw, str):
            issues.add("removed_non_string")
            continue

        kw = _deep_clean_keyword(raw)
        if not kw:
            issues.add("removed_empty")
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

            key = part.casefold()
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

    # Normaliza duplicatas de issues
    issues = sorted(set(issues))
    return cleaned, issues


def validate_keywords(cleaned: KeywordsResult, parse_issue: Optional[str]) -> List[str]:
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

    if any(len((kw or "").split()) >= 9 for kw in cleaned.get("keywords", [])):
        issues.append("keyword_looks_sentence")

    if any("a lista " in (kw or "") for kw in cleaned.get("keywords", [])):
        issues.append("keyword_contains_lista")

    if any("palavras-chave" in (kw or "") for kw in cleaned.get("keywords", [])):
        issues.append("keyword_contains_palavras-chave")

    # Verifica por misturas de latim e grego: has_mixed_scripts
    if any(has_mixed_scripts(kw) for kw in cleaned.get("keywords", [])):
        issues.append("keyword_mixed_scripts")

    if any(has_unbalanced_parentheses(kw) for kw in cleaned.get("keywords", [])):
        issues.append("keyword_unbalanced_parentheses")

    # Flag números isolados virando keywords (ex.: "4")
    if any(
        re.fullmatch(r"\d+[.,]?", (kw or "").strip())
        for kw in cleaned.get("keywords", [])
    ):
        issues.append("keyword_isolated_number")

    return issues


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
) -> None:
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
            issues = clean_issues + validate_keywords(cleaned, parse_issue)

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
) -> Tuple[KeywordsResult, str]:
    """Processa uma página e retorna (resultado_estruturado, raw_response)."""
    doc_name = row["documento"]
    user_prompt = build_user_prompt(row, source, doc_name)

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
            )
        except Exception as exc:
            log.warning(
                "[%s] p%d tentativa %d/%d – erro LLM: %s",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                time.sleep(3 * attempt)
            continue

        # Valida se a resposta é parseável
        if not raw_response or not raw_response.strip():
            log.warning(
                "[%s] p%d tentativa %d/%d – resposta vazia",
                doc_name,
                row["pagina_num"],
                attempt,
                retries,
            )
            if attempt < retries:
                time.sleep(2 * attempt)
            continue

        parsed_result, _ = parse_keywords_response(raw_response)
        cleaned_result, clean_issues = clean_keywords_structure(parsed_result)
        if clean_issues:
            log.debug(
                "[%s] p%d parsing issues: %s",
                doc_name,
                row["pagina_num"],
                ",".join(clean_issues),
            )
        result = cleaned_result
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
        choices=["minimal", "low", "medium", "high"],
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

    # Controle
    p.add_argument(
        "--skip-done",
        action="store_true",
        help="Pula páginas que já têm keywords (com --write).",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()

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

    # Conexões
    if verify_mode:
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
        }
        verify_documents(
            con=resumos_con,
            docs=docs,
            apply_fix=apply_fix,
            rerun_bad=rerun_bad,
            process_params=process_params,
            page=args.page,
            limit=args.limit,
        )
        resumos_con.close()
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
