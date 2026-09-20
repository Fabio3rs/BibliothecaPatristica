#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Teste/aprimoramento de limpeza OCR + limpeza de resumos + diagnóstico de ruído.

O script:
1) lê registros da tabela `resumos` em data/patristica_resumos.db
2) limpa OCR com foco em busca/embedding
3) limpa resumo_pagina e resumo_global separadamente
4) classifica páginas de baixo valor para similares
5) imprime amostras e estatísticas separadas por fonte

Uso:
    python3 ./test_limpeza_ocr.py
"""

import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Dict, Any, List, Tuple

DB_PATH = Path("data/patristica_resumos.db")
TEST_LIMIT = 25

COLUMNS_ADD = """
-- ============================================================
-- MIGRAÇÃO 001: ampliar tabela resumos com campos derivados
-- Banco alvo: data/patristica_resumos.db
--
-- Observação:
-- - Em SQLite, ALTER TABLE ... ADD COLUMN adiciona a coluna no fim.
-- - Esta migração é para rodar uma vez.
-- - Depois do ALTER TABLE, rode o backfill em Python.
-- - Só depois crie os índices do bloco 2.
-- ============================================================

BEGIN IMMEDIATE;

-- ------------------------------------------------------------
-- Texto limpo para busca lexical / embedding terminológico
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN ocr_clean_search TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------------
-- Resumo de página limpo para embedding conceitual / snippets
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN summary_page_clean TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------------
-- Resumo global limpo (uso macro, navegação, snapshot de obra)
-- Não recomendado como embedding principal de página.
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN summary_global_clean TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------------
-- Metadados extraídos do resumo_global
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN author_detected TEXT NOT NULL DEFAULT '';
ALTER TABLE resumos ADD COLUMN work_detected   TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------------
-- Classificação da página:
--   noise     = aviso/lixo/Google Books/página inútil
--   editorial = capa, sumário, prefácio, catálogo, apparatus
--   content   = conteúdo patrístico/analítico real
--   unknown   = ainda não classificado
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN page_kind TEXT NOT NULL DEFAULT 'unknown';

-- ------------------------------------------------------------
-- Flags operacionais do pipeline
-- 0 = não descartar
-- 1 = descartar
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN drop_original_embedding INTEGER NOT NULL DEFAULT 0;
ALTER TABLE resumos ADD COLUMN drop_summary_embedding  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE resumos ADD COLUMN drop_pagefind           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE resumos ADD COLUMN drop_similarity         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE resumos ADD COLUMN drop_keywords_export    INTEGER NOT NULL DEFAULT 0;

-- ------------------------------------------------------------
-- Motivo resumido do descarte/classificação
-- Ex.: "empty_after_clean", "google_boilerplate_page",
--      "low_value_summary", "editorial_front_matter"
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN drop_reason TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------------
-- Versão da lógica de limpeza/backfill
-- Ex.: "clean-v1", "clean-v2"
-- ------------------------------------------------------------
ALTER TABLE resumos ADD COLUMN cleaning_version TEXT NOT NULL DEFAULT '';

COMMIT;
"""

INDEX_TO_ADD_LATER = """
-- ============================================================
-- MIGRAÇÃO 002: índices
-- Rode somente depois de preencher as colunas novas.
-- ============================================================

BEGIN IMMEDIATE;

-- ------------------------------------------------------------
-- Filtros por tipo de página + localização
-- Útil para export, depuração e relatórios
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_kind_doc_page
ON resumos(page_kind, documento, pagina_num);

-- ------------------------------------------------------------
-- Filtros por autor/obra detectados
-- Útil para buscas administrativas, QA e navegação
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_author_work
ON resumos(author_detected, work_detected);

-- ------------------------------------------------------------
-- Apenas páginas que entram no Pagefind
-- Índice parcial: menor e mais útil que indexar tudo
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_pagefind_live
ON resumos(documento, pagina_num)
WHERE drop_pagefind = 0;

-- ------------------------------------------------------------
-- Apenas páginas que entram em similares
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_similarity_live
ON resumos(documento, pagina_num)
WHERE drop_similarity = 0;

-- ------------------------------------------------------------
-- Apenas páginas que entram no embedding original
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_original_embedding_live
ON resumos(documento, pagina_num)
WHERE drop_original_embedding = 0;

-- ------------------------------------------------------------
-- Apenas páginas que entram no embedding de resumo
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_resumos_summary_embedding_live
ON resumos(documento, pagina_num)
WHERE drop_summary_embedding = 0;

COMMIT;
"""


# ============================================================
# Banco
# ============================================================


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ============================================================
# Contadores separados
# ============================================================

word_count_ocr = Counter()
word_count_resumo_pagina = Counter()
word_count_resumo_global = Counter()


def add_word_count(counter: Counter, texto: str) -> None:
    if not texto:
        return

    palavras = texto.split()
    for palavra in palavras:
        # normaliza para contagem estatística
        palavra = unicodedata.normalize("NFKD", palavra.lower())
        palavra = palavra.encode("ASCII", "ignore").decode("ASCII")
        palavra = palavra.strip()
        if palavra:
            counter[palavra] += 1


# ============================================================
# XML (novo formato de OCR)
# ============================================================


def _parse_blocks_with_regex(xml_text: str) -> List[Tuple[str, str]]:
    """Fallback leve para extrair blocos quando o XML estiver malformado."""
    blocks: List[Tuple[str, str]] = []
    # captura script se existir; se não, script="" vira None
    for m in re.finditer(
        r"<bloco[^>]*?(?:script=\"(?P<script>[^\"]+)\")?[^>]*>(?P<content>.*?)</bloco>",
        xml_text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        script = (m.group("script") or "").strip().lower()
        content = m.group("content") or ""
        blocks.append((script, content))
    return blocks


def extract_text_from_ocr_xml(text: str) -> Dict[str, Any]:
    """
    Extrai texto plano e metadados de páginas no novo XML.

    Regras:
    - Usa apenas blocos script={latino,misto}; se forem < 50% dos blocos, usa todos.
    - Ignora <notas> e atributos.
    - <pagina estado="vazio"> => texto vazio + marca para descarte.
    - Fallback seguro para texto plano ou XML malformado.
    """

    if not text:
        return {
            "text": "",
            "is_xml": False,
            "estado": "",
            "scripts_kept": [],
            "total_blocks": 0,
            "used_all_scripts": False,
            "parse_ok": False,
        }

    stripped = text.strip()
    if not stripped.startswith("<pagina"):
        return {
            "text": text,
            "is_xml": False,
            "estado": "",
            "scripts_kept": [],
            "total_blocks": 0,
            "used_all_scripts": False,
            "parse_ok": False,
        }

    estado = ""
    blocks: List[Tuple[str, str]] = []
    parse_ok = True

    def normalize_script(s: str) -> str:
        s = (s or "").lower().strip()
        mapping = {
            "latim": "latino",
            "latin": "latino",
            "lat": "latino",
            "mixed": "misto",
            "mix": "misto",
        }
        return mapping.get(s, s)

    try:
        root = ET.fromstring(stripped)
        estado = root.attrib.get("estado", "").lower()

        for bloco in root.findall("bloco"):
            script = normalize_script(bloco.attrib.get("script", ""))
            content = "".join(bloco.itertext())
            blocks.append((script, content))
    except Exception:
        parse_ok = False
        blocks = _parse_blocks_with_regex(stripped)
        # tenta obter estado do atributo da tag pagina via regex
        m_estado = re.search(r"<pagina[^>]*estado=\"([^\"]+)\"", stripped)
        if m_estado:
            estado = m_estado.group(1).lower()
        # normaliza scripts em fallback
        blocks = [(normalize_script(s), c) for (s, c) in blocks]

    def detect_latin_ratio(s: str) -> float:
        if not s:
            return 0.0
        latin_chars = len(RE_LATIN_CHAR.findall(s))
        total = len(s)
        return latin_chars / total if total else 0.0

    total_blocks = len(blocks)
    if estado == "vazio":
        return {
            "text": "",
            "is_xml": True,
            "estado": estado,
            "scripts_kept": [],
            "total_blocks": total_blocks,
            "used_all_scripts": False,
            "parse_ok": parse_ok,
        }

    if not blocks:
        # XML sem blocos -> trata como texto vazio porém marcado como XML
        return {
            "text": "",
            "is_xml": True,
            "estado": estado or "com_texto",
            "scripts_kept": [],
            "total_blocks": 0,
            "used_all_scripts": True,
            "parse_ok": parse_ok,
        }

    # completa scripts ausentes usando heurística de 10% de caracteres latinos
    classified_blocks: List[Tuple[str, str]] = []
    for script, content in blocks:
        s = script
        if not s:
            if detect_latin_ratio(content) >= 0.10:
                s = "misto"
        classified_blocks.append((s, content))

    blocks = classified_blocks

    # contagem de scripts para decidir inclusão
    latin_like_scripts = {"latino", "misto"}
    latin_blocks = [(s, c) for (s, c) in blocks if s in latin_like_scripts]

    use_all = len(latin_blocks) * 2 < total_blocks
    selected = blocks if use_all else latin_blocks

    scripts_kept = [s or "" for (s, _) in selected]
    text_joined = "\n\n".join(content.strip() for _, content in selected if content.strip())

    non_latin_only = bool(selected) and all(s not in latin_like_scripts for s, _ in selected)

    return {
        "text": text_joined,
        "is_xml": True,
        "estado": estado or "com_texto",
        "scripts_kept": scripts_kept,
        "total_blocks": total_blocks,
        "used_all_scripts": use_all,
        "parse_ok": parse_ok,
        "non_latin_only": non_latin_only,
    }


# ============================================================
# Regex / normalização básica
# ============================================================

# Junta palavras hifenizadas no fim da linha: ver-\nbum -> verbum
RE_HYPHEN_LINEBREAK = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)", re.UNICODE)

# Espaços / Unicode
RE_NBSP = re.compile(r"[\u00A0\u2007\u202F]")
RE_SPACES = re.compile(r"[ \t]+")
RE_MULTI_NL = re.compile(r"\n{2,}")

# Letras latinas para heurística de script ausente
RE_LATIN_CHAR = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")

# URLs
RE_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Google / boilerplate
RE_BOOKS_GOOGLE = re.compile(r"\bbooks?\s*\.?\s*google\s*\.?\s*com\b", re.IGNORECASE)
RE_GOOGLE_SHORT = re.compile(
    r"\b(?:digitized\s+by\s+google|google\s+digitized|google\s+livros|google\s+books?)\b",
    re.IGNORECASE,
)

RE_GOOGLE_DISCLAIMER = re.compile(
    r"""
    this\s+is\s+a\s+reproduction\s+of\s+a\s+library\s+book
    .*?
    ongoing\s+effort\s+to\s+preserve
    .*?
    universally\s+accessible
    \.?
    (?:.*?(?:google\s+books?|books?\s*\.?\s*google\s*\.?\s*com).*)?
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

RE_GOOGLE_LINE = re.compile(
    r"^\s*(?:digitized\s+by\s+google|google\s+books?.*|books?\s*\.?\s*google\s*\.?\s*com.*)\s*$",
    re.IGNORECASE,
)

# Preserva letras Unicode, números e pontuação leve
RE_CLEAN_CONTEXT = re.compile(r"[^\w\s\.\?\!\:\;\,']", re.UNICODE)

# Markdown / adornos LLM
RE_MD_BOLD = re.compile(r"\*\*(.*?)\*\*", re.DOTALL)
RE_MD_ITALIC_STAR = re.compile(r"\*(.*?)\*", re.DOTALL)
RE_MD_ITALIC_UNDERSCORE = re.compile(r"__(.*?)__", re.DOTALL)
RE_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
RE_BACKTICKS = re.compile(r"`+")
RE_BULLET = re.compile(r"^\s*[-•*]+\s+", re.MULTILINE)

# Prefixos artificiais do resumo_pagina
SUMMARY_PAGE_PREFIXES = [
    re.compile(r"^\s*resumo\s+nesta\s+p[aá]gina\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*resumo\s+da\s+p[aá]gina\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*na\s+p[aá]gina\s+atual\s*,?\s*", re.IGNORECASE),
    re.compile(r"^\s*nesta\s+p[aá]gina\s*,?\s*", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+cont[eé]m\s+", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+apresenta\s+", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+descreve\s+", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+exibe\s+", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+mostra\s+", re.IGNORECASE),
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+traz\s+", re.IGNORECASE),
    # fallback genérico
    re.compile(r"^\s*a\s+p[aá]gina\s+atual\s+", re.IGNORECASE),
]

# Estrutura do resumo_global
RE_AUTHOR_LINE = re.compile(r"(?mi)^\s*autor\s*:\s*(.+?)\s*$")
RE_WORK_LINE = re.compile(r"(?mi)^\s*livro/obra\s+identificada\s*:\s*(.+?)\s*$")
RE_GLOBAL_LABEL = re.compile(r"(?mi)^\s*resumo\s+at[eé]\s+o\s+momento\s*:\s*")

# Heurísticas de páginas de baixo valor
GOOGLE_NOISE_TERMS = [
    "digitized by google",
    "google books",
    "books.google.com",
    "digitalização do google books",
    "notificação de digitalização",
    "aviso de digitalização",
]

LOW_VALUE_PAGE_PATTERNS = [
    re.compile(r"\bsem\s+conte[uú]do\s+patr[ií]stico\b", re.IGNORECASE),
    re.compile(r"\bsem\s+qualquer\s+conte[uú]do\b", re.IGNORECASE),
    re.compile(r"\bsem\s+texto\s+relevante\b", re.IGNORECASE),
    re.compile(
        r"\bapenas\s+(?:a\s+)?(?:indica[cç][aã]o|notifica[cç][aã]o)\b", re.IGNORECASE
    ),
    re.compile(r"\bcomposto\s+exclusivamente\s+por\s+avisos\b", re.IGNORECASE),
]


# ============================================================
# Helpers
# ============================================================


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = RE_NBSP.sub(" ", text)
    text = unicodedata.normalize("NFC", text)
    return text


def strip_llm_markup(text: str) -> str:
    if not text:
        return ""
    text = RE_MD_BOLD.sub(r"\1", text)
    text = RE_MD_ITALIC_UNDERSCORE.sub(r"\1", text)
    text = RE_MD_ITALIC_STAR.sub(r"\1", text)
    text = RE_MD_HEADER.sub("", text)
    text = RE_BULLET.sub("", text)
    text = RE_BACKTICKS.sub("", text)
    text = text.replace("*", "")
    return text


def compact_whitespace(text: str) -> str:
    if not text:
        return ""
    text = RE_SPACES.sub(" ", text)
    text = RE_MULTI_NL.sub("\n", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ============================================================
# OCR
# ============================================================


def strip_google_boilerplate(text: str) -> str:
    """
    Remove disclaimer longo e linhas curtas de Google Books.
    Ordem importa: o disclaimer longo vem ANTES da assinatura curta.
    """
    text = RE_GOOGLE_DISCLAIMER.sub(" ", text)
    text = RE_URL.sub(" ", text)
    text = RE_BOOKS_GOOGLE.sub(" ", text)
    text = RE_GOOGLE_SHORT.sub(" ", text)

    linhas = []
    for linha in text.splitlines():
        s = linha.strip()
        if not s:
            linhas.append("")
            continue
        if RE_GOOGLE_LINE.match(s):
            continue
        linhas.append(s)

    return "\n".join(linhas)


def clean_ocr_text_optimized(texto: str) -> tuple[str, Dict[str, Any]]:
    """Limpa OCR com suporte ao novo XML.

    Retorna texto limpo + metadados da extração.
    """

    meta = extract_text_from_ocr_xml(texto)
    base_text = meta["text"]

    if not base_text:
        return "", meta

    # A. Unicode / normalização inicial
    cleaned = normalize_text(base_text)

    # B. Unir hifenização de fim de linha
    cleaned = RE_HYPHEN_LINEBREAK.sub("", cleaned)

    # C. Remover boilerplate Google
    cleaned = strip_google_boilerplate(cleaned)

    # D. Limpeza de caracteres preservando contexto leve
    cleaned = RE_CLEAN_CONTEXT.sub(" ", cleaned)

    # E. Normalização final
    cleaned = compact_whitespace(cleaned)

    meta["clean_text"] = cleaned
    return cleaned, meta


def prepare_for_diff(text: str) -> list[str]:
    """
    Limpeza mínima focada em extrair tokens latinos comparáveis.
    Não usa clean_ocr_text_optimized — ela é agressiva demais pro diff.
    """
    # normalização básica
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # une hifenização de fim de linha (ex: "interver-\nsion" → "interversion")
    text = re.sub(r"-\n([a-zA-ZÀ-öø-ÿ])", r"\1", text)

    # remove XML tags se vier do LLM
    text = re.sub(r"<[^>]+>", "", text)

    # extrai tokens latinos >= 3 chars, lowercase
    return [t.lower() for t in re.findall(r"[a-zA-ZÀ-öø-ÿ]{3,}", text)]


def classify_page_noise(
    texto_original: str,
    texto_limpo: str,
    meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Heurística simples para decidir se a página original é boa
    o suficiente para embedding/indexação lexical.
    """
    if meta and meta.get("is_xml") and meta.get("estado") == "vazio":
        return {
            "drop_original_embedding": True,
            "drop_pagefind_original": True,
            "reason": "xml_pagina_vazia",
        }

    if not texto_limpo:
        return {
            "drop_original_embedding": True,
            "drop_pagefind_original": True,
            "reason": "empty_after_clean",
        }

    tokens = re.findall(r"\b\w+\b", texto_limpo, flags=re.UNICODE)
    alpha_chars = sum(ch.isalpha() for ch in texto_limpo)
    single_char_tokens = sum(1 for t in tokens if len(t) == 1)

    has_google = bool(
        RE_GOOGLE_DISCLAIMER.search(texto_original or "")
        or RE_GOOGLE_SHORT.search(texto_original or "")
        or RE_BOOKS_GOOGLE.search(texto_original or "")
        or RE_URL.search(texto_original or "")
    )

    token_count = len(tokens)
    single_ratio = (single_char_tokens / token_count) if token_count else 1.0

    if meta and meta.get("non_latin_only"):
        # Em páginas só orientais, apenas checa mínimo bruto de tamanho
        if token_count < 3 or alpha_chars < 10:
            return {
                "drop_original_embedding": True,
                "drop_pagefind_original": True,
                "reason": "non_latin_too_short",
            }
    elif token_count < 6 or alpha_chars < 25:
        return {
            "drop_original_embedding": True,
            "drop_pagefind_original": True,
            "reason": "too_short_or_low_alpha",
        }

    if has_google and token_count < 20:
        return {
            "drop_original_embedding": True,
            "drop_pagefind_original": True,
            "reason": "google_boilerplate_page",
        }

    if single_ratio > 0.45:
        return {
            "drop_original_embedding": True,
            "drop_pagefind_original": False,
            "reason": "too_many_single_char_tokens",
        }

    return {
        "drop_original_embedding": False,
        "drop_pagefind_original": False,
        "reason": "ok",
    }


# ============================================================
# Resumo de página
# ============================================================


def clean_summary_page_for_embedding(text: str) -> str:
    """
    Limpa resumo_pagina para embedding / Pagefind.
    Remove moldura repetitiva da LLM e adornos markdown.
    """
    if not text:
        return ""

    text = normalize_text(text)
    text = strip_llm_markup(text)

    for pat in SUMMARY_PAGE_PREFIXES:
        text = pat.sub("", text, count=1)

    text = compact_whitespace(text)
    return text


# ============================================================
# Resumo global
# ============================================================


def extract_global_summary_fields(text: str) -> Dict[str, str]:
    """
    Extrai campos estruturados do resumo_global, se existirem.
    """
    if not text:
        return {"author": "", "work": "", "summary": ""}

    text_norm = normalize_text(text)

    author_match = RE_AUTHOR_LINE.search(text_norm)
    work_match = RE_WORK_LINE.search(text_norm)

    author = author_match.group(1).strip() if author_match else ""
    work = work_match.group(1).strip() if work_match else ""

    summary = RE_AUTHOR_LINE.sub("", text_norm)
    summary = RE_WORK_LINE.sub("", summary)
    summary = RE_GLOBAL_LABEL.sub("", summary)
    summary = strip_llm_markup(summary)
    summary = compact_whitespace(summary)

    return {
        "author": author,
        "work": work,
        "summary": summary,
    }


def clean_summary_global_for_search(text: str) -> str:
    """
    Limpa resumo_global para uso opcional em busca/navegação macro.
    Não recomendado como embedding principal de página.
    """
    fields = extract_global_summary_fields(text)
    summary = fields["summary"]
    summary = re.sub(r"(?i)^in[ií]cio da obra\\s*:\\s*", "", summary).strip()
    return summary


# ============================================================
# Exclusão de páginas dos similares
# ============================================================


def should_drop_page_from_similarity(
    ocr_clean: str,
    resumo_pagina_clean: str,
    page_quality: Dict[str, Any] | None = None,
    meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Decide se a página deve ficar fora de embeddings/similares.
    """
    ocr_clean = (ocr_clean or "").strip()
    resumo = (resumo_pagina_clean or "").strip().lower()

    if meta and meta.get("is_xml") and meta.get("estado") == "vazio":
        return {"drop": True, "reason": "xml_pagina_vazia"}

    if meta and meta.get("non_latin_only"):
        # tolera resumos curtos; só descarta vazio total
        if not ocr_clean and not resumo:
            return {"drop": True, "reason": "non_latin_empty"}

    if page_quality and page_quality.get("drop_original_embedding"):
        return {
            "drop": True,
            "reason": f"page_quality:{page_quality.get('reason', 'unknown')}",
        }

    if not ocr_clean and not resumo:
        return {"drop": True, "reason": "empty_both"}

    if len(ocr_clean) < 25 and len(resumo) < 60:
        return {"drop": True, "reason": "too_short"}

    noise_hits = sum(1 for term in GOOGLE_NOISE_TERMS if term in resumo)

    for pat in LOW_VALUE_PAGE_PATTERNS:
        if pat.search(resumo):
            return {"drop": True, "reason": "low_value_summary"}

    if noise_hits >= 2:
        return {"drop": True, "reason": "google_noise_summary"}

    return {"drop": False, "reason": "ok"}


# ============================================================
# Impressão / diagnóstico
# ============================================================


def print_top(counter: Counter, titulo: str, n: int = 50) -> None:
    print(f"\n{titulo}")
    for palavra, count in counter.most_common(n):
        print(f"Palavra: {palavra} | Contagem: {count}")


def process_records(con: sqlite3.Connection, limit: int = TEST_LIMIT) -> None:
    cursor = con.cursor()
    cursor.execute(
        """
        SELECT id, documento, pagina_num, pagina_texto, resumo_pagina, resumo_global
        FROM resumos
        ORDER BY id
    """
    )

    contador = 0

    for row in cursor:
        record_id = row["id"]
        documento = row["documento"]
        pagina_num = row["pagina_num"]
        texto_original = row["pagina_texto"] or ""
        resumo_pagina = row["resumo_pagina"] or ""
        resumo_global = row["resumo_global"] or ""

        texto_limpo, meta = clean_ocr_text_optimized(texto_original)
        quality = classify_page_noise(texto_original, texto_limpo, meta=meta)

        resumo_pagina_limpo = clean_summary_page_for_embedding(resumo_pagina)
        global_fields = extract_global_summary_fields(resumo_global)
        resumo_global_limpo = global_fields["summary"]

        similarity_decision = should_drop_page_from_similarity(
            ocr_clean=texto_limpo,
            resumo_pagina_clean=resumo_pagina_limpo,
            page_quality=quality,
            meta=meta,
        )

        print(f"ID {record_id} | {documento} p.{pagina_num}")
        print(f"Texto original: {texto_original[:1200]}")
        print(
            f"Texto OCR limpo: {texto_limpo[:1200]}"
            f" | XML={{meta['is_xml']}} estado={meta.get('estado','')}"
            f" blocks={meta.get('total_blocks',0)} kept={meta.get('scripts_kept', [])}"
            f" all_scripts={meta.get('used_all_scripts', False)} parse_ok={meta.get('parse_ok', False)}"
        )
        print(f"Resumo página limpo: {resumo_pagina_limpo[:500]}")
        print(f"Resumo página ORIGINAL: {resumo_pagina[:500]}")
        print(f"Resumo global limpo: {resumo_global_limpo[:500]}")
        print(f"Resumo global ORIGINAL: {resumo_global[:500]}")
        print(
            f"Campos globais extraídos: "
            f"author={global_fields['author']!r}, work={global_fields['work']!r}"
        )
        print(f"Classificação OCR: {quality}")
        print(f"Decisão similares: {similarity_decision}")
        print("\n" + "-" * 80 + "\n")

        add_word_count(word_count_ocr, texto_limpo)
        add_word_count(word_count_resumo_pagina, resumo_pagina_limpo)
        add_word_count(word_count_resumo_global, resumo_global_limpo)

        contador += 1
        if contador >= limit:
            print(f"Parando após {limit} testes para avaliação.")
            break

    print_top(word_count_ocr, "Palavras mais frequentes do OCR limpo:")
    print_top(
        word_count_resumo_pagina, "Palavras mais frequentes do resumo_pagina limpo:"
    )
    print_top(
        word_count_resumo_global, "Palavras mais frequentes do resumo_global limpo:"
    )


# ============================================================
# Main
# ============================================================


def main() -> None:
    if not DB_PATH.exists():
        print(f"Erro: Banco de dados não encontrado em {DB_PATH}")
        return

    with connect_db(DB_PATH) as con:
        print("Conectado ao banco de dados...")
        process_records(con, limit=TEST_LIMIT)
        print("Concluído.")


if __name__ == "__main__":
    main()
