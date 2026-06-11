#!/usr/bin/env python3
"""
Gera um arquivo de treinamento `migne.training_text` a partir dos textos
em `ocr_results.text_content` (WHERE is_current=1) do banco `data/ocr_versions.db`.

Comportamento:
- Filtra apenas linhas que aparentam ser latim/greco/mixto (função
  validar_latim_grego).
- Aplica wrap por 'unidades de caractere' (estimated_width) com padrão 80.
- Flags: --unlimited (não quebra linhas em 80), --shuffle, --seed.
- Por padrão escreve `migne.training_text` no diretório atual.

Uso:
  python scripts/migne_training_text.py --db data/ocr_versions.db --out migne.training_text

"""

from __future__ import annotations

import argparse
import html
import random
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, List
import xml.etree.ElementTree as ET


_BULLETISH = {
    "†",
    "‡",
    "•",
    "¶",
    "§",
    "℣",
    "℟",
    "☧",
    "✠",
    "☩",
    "·",
    "«",
    "»",
    "’",
    "—",
    "◊",
    "*",
}
_STRIP_PREFIXES = (
    "[ilegivel]",
    "[ilegível]",
    "[illegivel]",
    "[illegível]",
)
_PAGE_FURNITURE_RE = re.compile(
    r"^(?:\.*\s*\d+|\d+\s*\.*|\d+\s+[A-ZIVXLCDM]{1,12}\s*\.*|[A-ZIVXLCDM]{1,12}\s+\d+)\s*$"
)
_DOT_LEADER_RE = re.compile(r"^[\.\s]{6,}\d*\s*$")
_NUMERAL_PAGE_RE = re.compile(r"^(?:\d+|[IVXLCDM]+|[A-Z]{1,4})[.,;:]?\s*$")
_MANY_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_BRACKET_MARKUP_RE = re.compile(
    r"\[(?:/?(?:linha|br|i|p|span|div|img)|/?(?:linha|br|i|p|span|div|img)\s+[^\]]*|bbox=\"[^\"]*\"|[^\]]*?bbox=\"[^\"]*\"[^\]]*)\]",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"</?(?:br|p|div|span|sup|sub|i|b|em|strong|small|font|u|q|a|li|ul|ol|table|tr|td|th|blockquote|hr|code)\b[^>]*>", re.IGNORECASE)
_HTML_ENTITY_RE = re.compile(r"&(nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")
_BROKEN_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^>]*)?>")
_ANGLE_MARKUP_RE = re.compile(r"<\/?[A-Za-z][^>]{0,80}>")


def iter_grapheme_clusters(text: str):
    """Itera por grapheme clusters simples: base + combining marks anexos."""
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.category(text[j]).startswith("M"):
            j += 1
        yield text[i:j]
        i = j


def normalize_training_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = normalize_dashes(text)
    text = text.translate(_SUPERSCRIPT_TABLE)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = text.replace("&nbsp;", " ")
    text = text.replace("[br]", "\n")
    text = text.replace("\\\n", "\n")
    text = text.replace("\u200b", "")
    text = _BRACKET_MARKUP_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _BROKEN_TAG_RE.sub(" ", text)
    text = _ANGLE_MARKUP_RE.sub(" ", text)
    text = re.sub(r"\[(?:/?(?:linha|br|i|p|span|div|img)|br/)\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = normalize_punctuation(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Normalização de pontuação
# ---------------------------------------------------------------------------
# Pontuação que NÃO deve ter espaço antes (em contexto latino/grego/francês).
# Nota: `;` grego (U+037E) é mapeado para `·` pelo NFC/tesseract, não tratamos aqui.
# Aspas francesas « » são EXCLUÍDAS — tipografia francesa exige espaço interno.
_NO_SPACE_BEFORE = re.compile(
    r" +([,\.;:!?\)\]\}›])"
)
# Espaço após abertura de parênteses/colchetes — aspas francesas « ‹ EXCLUÍDAS
_NO_SPACE_AFTER_OPEN = re.compile(
    r"([\(\[\{]) +"
)
# Espaço ANTES de apóstrofo colado à palavra seguinte: "l' esprit" → "l'esprit"
# Só aplica quando o apóstrofo está entre letra e letra (não no final de linha)
_APOSTROPHE_SPACE = re.compile(
    r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]')\s+([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])"
)
# Em dash: normalizar espaços múltiplos ao redor para espaço simples.
# Consome também combining marks que estejam soltos nos espaços vizinhos
# (sequências como "texto\u0301 — \u0301texto" já eram inválidas).
_EMDASH_SPACES = re.compile(r"[\u0300-\u036F\u1DC0-\u1DFF]*\s*—\s*[\u0300-\u036F\u1DC0-\u1DFF]*")
# Vírgulas/pontos duplicados acidentais: ",," → "," mas "..." preservado
_DOUBLE_COMMA = re.compile(r",{2,}")
_DOUBLE_PERIOD = re.compile(r"\.{2,}")   # será tratado com cuidado abaixo
# Ponto-e-vírgula/dois-pontos duplicados
_DOUBLE_SEMICOLON = re.compile(r";{2,}")
_DOUBLE_COLON = re.compile(r":{2,}")


def normalize_punctuation(text: str) -> str:
    """Corrige artefatos comuns de pontuação em ground truth de OCR.

    Regras aplicadas (seguras para latim/grego/francês/italiano):
    - Remove espaço antes de  , . ; : ! ? ) ] } » ›
    - Remove espaço após      ( [ { « ‹
    - Contrai espaço em  "l' esprit" → "l'esprit"  (apóstrofo elision)
    - Normaliza espaços ao redor de em dash: " —  " → " — "
    - Remove vírgulas duplicadas: ",," → ","
    - Remove pontos duplicados EXCETO sequências ≥ 3 (reticências)
    - Remove ponto-e-vírgula / dois-pontos duplicados
    """
    # espaço antes de pontuação de fechamento
    text = _NO_SPACE_BEFORE.sub(r"\1", text)
    # espaço depois de pontuação de abertura
    text = _NO_SPACE_AFTER_OPEN.sub(r"\1", text)
    # apóstrofo de elision: "l' esprit" → "l'esprit"
    text = _APOSTROPHE_SPACE.sub(r"\1\2", text)
    # em dash: " —  " → " — "
    text = _EMDASH_SPACES.sub(" — ", text)
    # vírgulas duplicadas
    text = _DOUBLE_COMMA.sub(",", text)
    # pontos duplicados: "Joan.." → "Joan." mas preserva reticências espaçadas
    # ". . ." e reticências compactas "..."
    # Estratégia: normalizar ". . ." → "..." antes, depois colapsar só pares adjacentes
    text = re.sub(r"\. (?=\. |\.$|\.\s)", ".", text)   # ". . . " → "..."
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)      # ".." → "." (não toca "...")
    # ponto-e-vírgula / dois-pontos duplicados
    text = _DOUBLE_SEMICOLON.sub(";", text)
    text = _DOUBLE_COLON.sub(":", text)
    # recolapsar espaços gerados pelas substituições
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def validar_latim_grego(texto: str) -> bool:
    # Trabalha em clusters NFC para não quebrar letras acentuadas.
    letters = []
    for cluster in iter_grapheme_clusters(texto):
        base = cluster[0]
        if base.isalpha():
            letters.append(base)
    if not letters:
        return False

    has_lat = False
    has_gr = False
    for char in letters:
        nome = unicodedata.name(char, "UNKNOWN")
        if "LATIN" in nome:
            has_lat = True
        elif "GREEK" in nome or "COPTIC" in nome:
            has_gr = True
        elif char in _BULLETISH:
            continue
        else:
            return False
    return has_lat or has_gr


def estimated_width(text: str) -> float:
    """Calcula a largura estimada do texto em 'unidades de caractere'.

    Itera por grapheme clusters (base + combining marks) para não contar
    diacríticos combinantes como unidades independentes. O peso é determinado
    pelo codepoint base do cluster: grego/politônico vale 1.35, demais 1.0.
    """
    greek_weight = 1.35
    width = 0.0
    for cluster in iter_grapheme_clusters(text):
        base = cluster[0]
        cp = ord(base)
        if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
            width += greek_weight
        else:
            width += 1.0
    return width


def is_valid_line(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if re.fullmatch(r"[\d\s\[\]\.]+", text):
        return False
    if len(text.split()) < 2:
        return False
    if re.match(r"^\d+\s+.{3,50}\s+\d+$", text):
        return False
    if _PAGE_FURNITURE_RE.fullmatch(text):
        return False
    if _DOT_LEADER_RE.fullmatch(text):
        return False
    if _NUMERAL_PAGE_RE.fullmatch(text):
        return False
    if _MANY_PUNCT_RE.fullmatch(text) and not any(ch in _BULLETISH for ch in text):
        return False
    return True


# Scripts suportados
VALID_SCRIPTS = {"latino", "grego", "latinogrego", "misto"}


def load_charfreq(charfreq_path: Path, min_count: int) -> set[str]:
    """Lê o arquivo charfreq e retorna o set de chars com contagem < min_count.

    Formato esperado: '<COUNT> <CHAR>\\n' (com ou sem espaço inicial).
    Chars com contagem abaixo do threshold são considerados 'proibidos'.
    """
    banned: set[str] = set()
    if not charfreq_path.exists():
        print(f"[aviso] charfreq não encontrado: {charfreq_path} — filtro desativado")
        return banned
    with open(charfreq_path, "rb") as f:
        for raw in f:
            line = raw.rstrip(b"\n").decode("utf-8", errors="replace").lstrip()
            if not line:
                continue
            parts = line.split(" ", 1)
            if not parts[0].isdigit():
                continue
            count = int(parts[0])
            char = parts[1] if len(parts) > 1 else " "
            # normalizar em NFC para ser consistente com o texto processado
            try:
                char = unicodedata.normalize("NFC", char)
            except Exception:
                pass
            if count < min_count:
                banned.add(char)
    return banned


def load_unicharset(path: Path) -> frozenset[str]:
    """Lê um arquivo unicharset do Tesseract e retorna o set de grapheme clusters válidos.

    O formato é:
        <total>
        <grafema> <flags> ... # <grafema> [hex bytes]
    Entradas especiais como NULL, Joined, |Broken|0|1 são ignoradas.
    """
    SKIP = {"NULL", "Joined", "|Broken|0|1"}
    valid: set[str] = set()
    if not path.exists():
        print(f"[aviso] unicharset não encontrado: {path} — verificação desativada")
        return frozenset()
    with open(path, encoding="utf-8", errors="replace") as f:
        lines_raw = f.readlines()
    # primeira linha é o total de entradas
    for line in lines_raw[1:]:
        line = line.rstrip("\n")
        if not line:
            continue
        # primeiro token separado por espaço é o grapheme cluster
        token = line.split(" ", 1)[0]
        if token in SKIP:
            continue
        try:
            token = unicodedata.normalize("NFC", token)
        except Exception:
            pass
        valid.add(token)
    return frozenset(valid)


def iter_grapheme_clusters(text: str):
    """Itera o texto por grapheme clusters (base + combining marks anexos).

    Reproduz o comportamento do `grep -P -o '\\X'`: cada cluster é formado
    por um caractere base seguido de zero ou mais caracteres combinantes
    (categoria Unicode 'M*').
    """
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.category(text[j]).startswith("M"):
            j += 1
        yield text[i:j]
        i = j


def has_banned_char(text: str, banned: set[str]) -> bool:
    """Retorna True se o texto contiver algum grapheme cluster do set proibido.

    Itera por clusters (base + combining) para ser consistente com o charfreq
    gerado via `grep -P -o '\\X'`, evitando falsos positivos/negativos ao
    comparar codepoints isolados.
    """
    for cluster in iter_grapheme_clusters(text):
        if cluster in banned:
            return True
        # fallback: checar o codepoint base isolado (para entradas de 1 char no banned)
        if len(cluster) > 1 and cluster[0] in banned:
            return True
    return False


# Categorias bidirecionais consideradas RTL
_RTL_BIDI = {"R", "AL", "RLE", "RLO", "RLI"}


def has_rtl_char(text: str) -> bool:
    """Retorna True se o texto contiver algum caractere de escrita direita-para-esquerda.

    Usa a categoria bidirecional Unicode: R (strong RTL), AL (árabe/persa),
    RLE/RLO/RLI (marcadores de embedding RTL).
    """
    return any(unicodedata.bidirectional(ch) in _RTL_BIDI for ch in text)


# Mapeamento de traços/hífens exóticos → hífen-minus ASCII (U+002D)
# Critério: visualmente indistinguíveis OU semanticamente equivalentes a
# um hífen/traço em texto patrístico latino/grego.
_DASH_NORMALIZE: dict[str, str] = {
    "\u00AD": "-",   # SOFT HYPHEN (invisível, mas conta como codepoint)
    "\u2010": "-",   # HYPHEN
    "\u2011": "-",   # NON-BREAKING HYPHEN
    "\u2012": "-",   # FIGURE DASH
    "\u2212": "-",   # MINUS SIGN
    "\u207B": "-",   # SUPERSCRIPT MINUS
    "\u208B": "-",   # SUBSCRIPT MINUS
    "\uFE58": "-",   # SMALL EM DASH
    "\uFE63": "-",   # SMALL HYPHEN-MINUS
    "\uFF0D": "-",   # FULLWIDTH HYPHEN-MINUS
    "\u2E17": "-",   # DOUBLE OBLIQUE HYPHEN
    "\u2E3A": "\u2014",  # TWO-EM DASH → em dash (—)
    "\u2E3B": "\u2014",  # THREE-EM DASH → em dash (—)
    "\u2053": "-",   # SWUNG DASH
    "\u2027": "-",   # HYPHENATION POINT
    "\u254C": "-",   # BOX DRAWINGS LIGHT DOUBLE DASH HORIZONTAL
    "\u2504": "-",   # BOX DRAWINGS LIGHT TRIPLE DASH HORIZONTAL
    "\uFE31": "\u2014",  # PRESENTATION FORM FOR VERTICAL EM DASH → em dash
    "\u058A": "-",   # ARMENIAN HYPHEN
    "\u05BE": "-",   # HEBREW PUNCTUATION MAQAF
    "\u1400": "-",   # CANADIAN SYLLABICS HYPHEN
}
_DASH_TABLE = str.maketrans(_DASH_NORMALIZE)

# Superscript and subscript digits → ASCII digits
_SUPERSCRIPT_TABLE = str.maketrans(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"  # ⁰¹²³⁴⁵⁶⁷⁸⁹
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089",  # ₀₁₂₃₄₅₆₇₈₉
    "01234567890123456789",
)


def normalize_dashes(text: str) -> str:
    """Converte traços/hífens exóticos nos equivalentes canônicos.

    - Hífens visualmente equivalentes → U+002D (HYPHEN-MINUS)
    - Traços longos raros → U+2014 (EM DASH)
    - Soft hyphen (U+00AD) → removido (substituído por hífen visível)
    """
    return text.translate(_DASH_TABLE)


def parse_bbox(bbox_str: str) -> tuple[int, int, int, int] | None:
    """Parseia 'x,y,w,h' ou 'x1,y1,x2,y2' — retorna (x, y, w, h) ou None."""
    try:
        parts = [int(v) for v in bbox_str.split(",")]
        if len(parts) == 4:
            return tuple(parts)
    except (ValueError, AttributeError):
        pass
    return None


def parse_page_xml(xml_path: Path) -> list[dict]:
    """
    Parseia um XML de página e retorna lista de blocos com:
      text, script, tipo, bbox
    Suporta os dois formatos encontrados nos XMLs do Migne.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError:
        return []

    blocks = []

    for tag in ("bloco", "cabecalho", "rodape"):
        for el in root.iter(tag):
            text = (el.text or "").strip()
            if not text:
                continue

            script = (el.get("script") or "").lower().strip()
            if script not in VALID_SCRIPTS:
                continue

            tipo = (el.get("tipo") or tag).lower().strip()

            bbox_str = el.get("bbox", "")
            bbox = parse_bbox(bbox_str)

            text = text.replace("[br]", "\n")
            text = text.replace("\\\n", "\n")
            text = text.replace("&nbsp;", " ")

            blocks.append(
                {
                    "text": unicodedata.normalize("NFC", text),
                    "script": script,
                    "tipo": tipo,
                    "bbox": bbox,
                    "source": xml_path.stem,
                }
            )

    return blocks


def parse_page_xml_from_string(xml_text: str, source: str = "db") -> list[dict]:
    """Parseia XML vindo em string (por exemplo, campo `text` no DB)."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    blocks = []
    for tag in ("bloco", "cabecalho", "rodape"):
        for el in root.iter(tag):
            text = (el.text or "").strip()
            if not text:
                continue

            script = (el.get("script") or "").lower().strip()
            if script not in VALID_SCRIPTS:
                continue

            tipo = (el.get("tipo") or tag).lower().strip()
            bbox_str = el.get("bbox", "")
            bbox = parse_bbox(bbox_str)
            text = text.replace("[br]", "\n")

            blocks.append(
                {
                    # normalizar com proteção — algumas strings no DB parecem corrompidas
                    "text": (unicodedata.normalize("NFC", text) if text else ""),
                    "script": script,
                    "tipo": tipo,
                    "bbox": bbox,
                    "source": source,
                }
            )

    return blocks


def wrap_line_by_units(line: str, max_units: int) -> List[str]:
    words = line.split()
    if not words:
        return []
    out = []
    current = []
    current_len = 0.0
    for w in words:
        w_len = estimated_width(w)
        sep = 1.0 if current else 0.0
        if current_len + sep + w_len > max_units and current:
            out.append(" ".join(current))
            current = [w]
            current_len = w_len
        else:
            current.append(w)
            current_len += sep + w_len
    if current:
        out.append(" ".join(current))
    return out


def extract_lines_from_text(text: str) -> Iterable[str]:
    # Quebra por linhas e também por parágrafos (dupla quebra)
    for raw in re.split(r"\r?\n", text):
        raw = raw.strip()
        if not raw:
            continue
        yield raw


def is_sane_line(text: str) -> bool:
    """Verifica se a linha não contém clusters anormais de diacríticos/combinantes

    - rejeita linha que comece por um caractere combinante
    - rejeita se houver muitos sinais combinantes no total
    - rejeita se houver repetição do mesmo combinante muitas vezes
    - rejeita se algum grapheme cluster (base+combining) codificado em UTF-8 exceder 30 bytes
    - rejeita sequências longas de diacríticos por regex
    """
    if not text:
        return False

    # não começar com combinante; use cluster base para não quebrar graphemes
    try:
        first_cluster = next(iter_grapheme_clusters(text))
    except StopIteration:
        return False
    except Exception:
        return False
    if unicodedata.category(first_cluster[0]).startswith("M"):
        return False

    if '�' in text:
        return False

    total_combining = 0
    max_same_combining_repeat = 0
    prev_comb = None
    same_count = 0
    n = len(text)
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            total_combining += 1
            if ch == prev_comb:
                same_count += 1
            else:
                same_count = 1
            prev_comb = ch
            if same_count > max_same_combining_repeat:
                max_same_combining_repeat = same_count
        else:
            prev_comb = None
            same_count = 0

    # limites heurísticos (ajustáveis)
    if total_combining > 15:
        return False
    if max_same_combining_repeat > 4:
        return False

    # checar cada grapheme cluster (base + combinantes) para tamanho utf-8
    for cluster in iter_grapheme_clusters(text):
        try:
            if len(cluster.encode("utf-8")) > 28:
                return False
        except Exception:
            return False

    # padrões comuns de diacríticos repetidos (combining diacritics block + modifier tone letters)
    if re.search(r"[\u0300-\u036F\u1DC0-\u1DFF]{5,}", text):
        return False

    return True


def cleanup_line(text: str) -> str | None:
    text = normalize_training_text(text)
    if not text:
        return None
    lowered = text.lower()
    if any(lowered == s or lowered.startswith(s + " ") for s in _STRIP_PREFIXES):
        return None
    if "[ilegivel]" in lowered or "[ilegível]" in lowered or "[illegivel]" in lowered:
        return None
    if "[/linha]" in lowered:
        return None
    if "�" in text:
        return None
    if re.search(r"\.{6,}", text):
        return None
    if len(text) < 3:
        return None
    if len(text.split()) == 1 and not any(ch.isalpha() or ch in _BULLETISH for ch in text):
        return None
    if not is_valid_line(text):
        return None
    if not validar_latim_grego(text):
        return None
    if not is_sane_line(text):
        return None
    return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/ocr_versions.db")
    parser.add_argument("--out", default="migne.training_text")
    parser.add_argument(
        "--max-units",
        type=int,
        default=80,
        help="Max units (estimated_width) por linha",
    )
    parser.add_argument(
        "--unlimited",
        action="store_true",
        help="Não aplica wrap: aceita linhas maiores que max-units",
    )
    parser.add_argument("--shuffle", action="store_true", help="Embaralhar saída")
    parser.add_argument("--seed", type=int, default=42, help="Semente para shuffle")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limita número de entradas lidas do DB (0 = sem limite)",
    )
    parser.add_argument(
        "--xml-dir",
        default=None,
        help="Diretório com XMLs de páginas para parsear (opcional)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Não escreve arquivo, só sumariza"
    )
    parser.add_argument(
        "--charfreq",
        default=None,
        help="Caminho para o arquivo charfreq (padrão: não filtra por frequência)",
    )
    parser.add_argument(
        "--min-char-count",
        type=int,
        default=10,
        help="Remove linhas que contenham chars com contagem < N no charfreq (padrão: 10)",
    )
    parser.add_argument(
        "--no-rtl",
        action="store_true",
        help="Remove linhas que contenham caracteres de escrita RTL (árabe, hebraico, etc.)",
    )
    parser.add_argument(
        "--min-alpha-ratio",
        type=float,
        default=0.12,
        help="Razão mínima de letras na linha após limpeza (padrão: 0.12)",
    )
    parser.add_argument(
        "--allow-list-symbols",
        action="store_true",
        help="Não rejeita linhas só por conter símbolos litúrgicos raros do corpus",
    )
    parser.add_argument(
        "--unicharset",
        default=None,
        help="Caminho para o unicharset do Tesseract; emite aviso (e rejeita a linha) "
             "se algum grapheme cluster não estiver no conjunto válido",
    )
    args = parser.parse_args()

    # Carregar set de chars proibidos (se --charfreq fornecido)
    banned_chars: set[str] = set()
    if args.charfreq:
        banned_chars = load_charfreq(Path(args.charfreq), args.min_char_count)
        print(
            f"[charfreq] {len(banned_chars)} chars proibidos "
            f"(contagem < {args.min_char_count})"
        )

    # Carregar unicharset (se --unicharset fornecido)
    valid_chars: frozenset[str] = frozenset()
    unicharset_warnings: dict[str, int] = {}  # cluster → nº de linhas rejeitadas
    if args.unicharset:
        valid_chars = load_unicharset(Path(args.unicharset))
        print(f"[unicharset] {len(valid_chars)} grafemas carregados de {args.unicharset}")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB não encontrado: {db_path}")
        return

    lines: List[str] = []
    n_rows = 0

    def accept_line(raw: str) -> str | None:
        line = cleanup_line(raw)
        if line is None:
            return None
        alpha_count = sum(ch.isalpha() for ch in line)
        ratio = alpha_count / max(1, len(line))
        if ratio < args.min_alpha_ratio:
            return None
        if banned_chars and has_banned_char(line, banned_chars):
            if not (args.allow_list_symbols and any(sym in line for sym in _BULLETISH)):
                return None
        if args.no_rtl and has_rtl_char(line):
            return None
        if valid_chars:
            bad = [
                cluster
                for cluster in iter_grapheme_clusters(line)
                if cluster != " " and cluster not in valid_chars
            ]
            if bad:
                for cluster in bad:
                    unicharset_warnings[cluster] = (
                        unicharset_warnings.get(cluster, 0) + 1
                    )
                preview = line[:60] + ("…" if len(line) > 60 else "")
                hex_bad = ", ".join(
                    f"U+{ord(c):04X} {repr(c)}"
                    for c in dict.fromkeys("".join(bad))  # únicos, em ordem
                )
                import sys
                print(
                    f"[unicharset] linha rejeitada — chars fora do unicharset: "
                    f"{hex_bad} | {repr(preview)}",
                    file=sys.stderr,
                )
                return None
        return line

    # 1) Se receber --xml-dir, percorre XMLs e extrai blocos parseáveis primeiro
    if args.xml_dir:
        xml_dir = Path(args.xml_dir)
        if not xml_dir.exists():
            print(f"XML dir não encontrado: {xml_dir}")
        else:
            # aceitar tanto .xml quanto .txt (XML embutido em .txt)
            files = list(sorted(xml_dir.rglob("*.xml"))) + list(
                sorted(xml_dir.rglob("*.txt"))
            )
            for p in files:
                if p.suffix.lower() == ".xml":
                    blocks = parse_page_xml(p)
                else:
                    # ler .txt como string contendo XML anotado
                    try:
                        blob = p.read_text(encoding="utf-8")
                    except Exception:
                        blob = p.read_text(encoding="utf-8", errors="replace")
                    blocks = parse_page_xml_from_string(blob, source=p.stem)

                for b in blocks:
                    n_rows += 1
                    text = b.get("text", "")
                    for raw in extract_lines_from_text(text):
                        raw_norm = accept_line(raw)
                        if raw_norm is None:
                            continue
                        if args.unlimited:
                            lines.append(raw_norm)
                        else:
                            pieces = wrap_line_by_units(raw_norm, args.max_units)
                            for p2 in pieces:
                                if estimated_width(p2) < 6:
                                    continue
                                if accept_line(p2) is None:
                                    continue
                                lines.append(p2)

    # 2) Em seguida, completa com textos do DB (se existir)
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        q = "SELECT * FROM ocr_results WHERE is_current = 1"
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        cursor = cur.execute(q)

        # descobrir índice da coluna 'text' ou 'text_content'
        cols = [c[0] for c in cursor.description]
        has_text_col = "text" in cols
        has_text_content = "text_content" in cols

        for row in cursor:
            n_rows += 1
            # o campo 'text' contém o XML anotado — parseá-lo
            xml_blob = row[cols.index("text_content")]
            blocks = parse_page_xml_from_string(xml_blob, source="db")
            for b in blocks:
                text = b.get("text", "")
                for raw in extract_lines_from_text(text):
                    raw_norm = accept_line(raw)
                    if raw_norm is None:
                        continue
                    if args.unlimited:
                        lines.append(raw_norm)
                    else:
                        pieces = wrap_line_by_units(raw_norm, args.max_units)
                        for p2 in pieces:
                            if estimated_width(p2) < 6:
                                continue
                            if accept_line(p2) is None:
                                continue
                            lines.append(p2)
            continue

        conn.close()

    if args.shuffle:
        random.seed(args.seed)
        random.shuffle(lines)

    out_path = Path(args.out)

    # Resumo de chars rejeitados pelo unicharset
    if unicharset_warnings:
        total_rejected = sum(unicharset_warnings.values())
        top = sorted(unicharset_warnings.items(), key=lambda x: -x[1])[:20]
        print(
            f"[unicharset] {total_rejected} linhas rejeitadas por chars fora do unicharset "
            f"({len(unicharset_warnings)} chars distintos). Top 20:",
            file=sys.stderr,
        )
        for cluster, count in top:
            hex_repr = " ".join(f"U+{ord(c):04X}" for c in cluster)
            print(f"  {count:>6}x  {repr(cluster):20s}  {hex_repr}", file=sys.stderr)

    if args.dry_run:
        print(f"Linhas extraídas: {len(lines)} (linhas lidas do DB: {n_rows})")
        if len(lines) > 20:
            print("Amostra (20):")
            for l in lines[:20]:
                print(l)
        return

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Arquivo escrito: {out_path}  ({len(lines)} linhas)")


if __name__ == "__main__":
    main()
