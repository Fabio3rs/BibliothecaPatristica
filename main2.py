#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
from dataclasses import dataclass, field
import hashlib
import io
import os
import sys
from pathlib import Path
from typing import List, Optional
import json
import unicodedata
import requests
import xml.etree.ElementTree as ET
import numpy as np
import re
import regex
import cv2
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
from tools.classify_scan_kind import process as classify_scan_page
import difflib
import sqlite3
import multiprocessing as mp
from functools import partial
import time
import socket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import entropy_lib
import evaluation_db


# Monkey-patch para injetar socket options
import urllib3.connection as _uc

_orig_connect = _uc.HTTPConnection.connect


def make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(total=0),  # retry no seu próprio código, não aqui
    )

    def _connect_with_keepalive(self):
        _orig_connect(self)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)

    _uc.HTTPConnection.connect = _connect_with_keepalive
    session.mount("https://", adapter)
    return session


# ============ CONFIGS PADRÃO ============
DEFAULT_DPI = 300  # 300dpi é o "doce" do Tesseract; subir só se necessário
DEFAULT_LANG = "lat"  # requer pacotes traineddata do Tesseract para latim
USE_PDFTOCAIRO = True  # geralmente mais estável / eficiente
IMAGE_FMT = "png"  # png ou jpeg (evitar PPM para não inflar memória)
MAX_THREADS_CONVERT = 12  # ajuste conforme seus núcleos
DEFAULT_LLM_MODEL = "qwen3.5:397b-cloud"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"
# OpenAI HTTP (sem SDK)
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


# O que estiver abaixo deve ser ignorado porque é um caso perdido, não há nada legível recuperável
CASOS_PERDIDOS = [
    ("PO009", 496),
    (
        "PO009",
        497,
    ),  # PO vol 9, página 497 teste/PO009/images/e408d0d8-83d6-4af6-8456-70f12bcffb93-497.png
    # teste/PO017/images/b89d6661-ee0e-46ed-b3d2-e9907874d56a-046.png
    ("PO017", 46),
]


def avalia_caso_perdido(text_or_image_path: Path) -> bool:
    # teste/PO009/images/e408d0d8-83d6-4af6-8456-70f12bcffb93-497.png -> PO009, 497
    # averiguar se o path está na lista de casos perdidos
    for caso in CASOS_PERDIDOS:
        if (
            caso[0] in str(text_or_image_path)
            and f"-{str(caso[1])}" in text_or_image_path.stem
        ):
            return True
    return False


# Opcional: se precisar apontar para o executável do Tesseract explicitamente
# pytesseract.pytesseract.tesseract_cmd = r"/usr/bin/tesseract"

# Opcional: evitar oversubscription quando você já paraleliza por fora
# (Tesseract usa OpenMP; limitar as threads internas ajuda quando há várias páginas)
os.environ.setdefault("OMP_THREAD_LIMIT", "4")  # ajuste conforme seus núcleos


@dataclass
class LatinOverlapResult:
    overlap_ratio: float  # % de tokens do LLM que aparecem no Tesseract
    recall_ratio: float  # % de tokens do Tesseract que aparecem no LLM
    seq_ratio: float  # similaridade de sequência para os tokens comuns
    llm_only: list[str]  # tokens no LLM mas não no Tesseract — candidatos a alucinação
    tess_only: list[str]  # tokens no Tesseract mas não no LLM — possível omissão
    common: list[str]  # tokens confirmados pelos dois
    llm_token_count: int
    tess_token_count: int
    segments: list["SegmentOverlap"] = field(default_factory=list)


@dataclass
class SegmentOverlap:
    idx: int
    overlap_ratio: float
    recall_ratio: float
    seq_ratio: float
    llm_token_count: int
    tess_token_count: int


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Set a longer timeout *before* any PRAGMA that may need a write lock
    # to avoid "database is locked" when many processes open the cache together.
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")  # 30s de espera em caso de lock
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def open_tesseract_cache_db():
    return connect_db(Path("data/tesseract.db"))


def init_tesseract_cache(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS tesseract_cache (
            image_hash TEXT PRIMARY KEY,
            imgpath        TEXT NOT NULL,
            lang        TEXT NOT NULL,
            result      TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """
    )
    con.commit()


def get_tesseract_cached(
    con: sqlite3.Connection, image_path: Path, lang: str
) -> str | None:
    # hash do conteúdo do arquivo, não do path — imagem movida ainda bate
    h = hashlib.sha256(image_path.read_bytes()).hexdigest()
    row = con.execute(
        "SELECT result FROM tesseract_cache WHERE image_hash = ?",
        (h,),
    ).fetchone()
    return row["result"] if row else None


def is_page_xml(text: str) -> bool:
    t = (text or "").lower().strip()
    return t.startswith("<") and t.endswith(">") and ("<pagina" in t or "</pagina" in t)


def remove_xml_tags(text: str) -> str:
    """Remove XML tags from a string."""
    return re.sub(r"<[^>]+>", "", text)


def split_latin_words(text: str) -> List[str]:
    """Extrai tokens que parecem latim/francês — ignora blocos orientais."""
    # Pega só palavras com caracteres ASCII + diacríticos latinos
    tokens = re.findall(r"[a-zA-ZÀ-öø-ÿ]{3,}", text)
    return tokens


def extract_latin_tokens(text: str) -> set[str]:
    """Extrai tokens que parecem latim/francês — ignora blocos orientais."""
    # Pega só palavras com caracteres ASCII + diacríticos latinos
    tokens = re.findall(r"[a-zA-ZÀ-öø-ÿ]{3,}", text)
    return {t.lower() for t in tokens}


def extract_latin_from_xml(xml_text: str) -> set[str]:
    """Extrai tokens latinos só dos blocos com script=latino."""
    blocks = re.findall(
        r'<bloco[^>]*script="(?:latino|misto)"[^>]*>(.*?)</bloco>', xml_text, re.DOTALL
    )
    tokens = set()
    for b in blocks:
        tokens |= extract_latin_tokens(b)
    return tokens


def latin_overlap_ratio(xml_text: str, tesseract_text: str) -> float:
    """Retorna % de tokens latinos do XML que aparecem no Tesseract."""
    xml_tokens = extract_latin_from_xml(xml_text)
    tess_tokens = extract_latin_tokens(tesseract_text)

    if not xml_tokens:
        return 1.0  # sem texto latino pra comparar, não penaliza

    matches = xml_tokens & tess_tokens
    return len(matches) / len(xml_tokens)


TOKEN_RE = regex.compile(
    r"\p{Latin}{3,}"
    r"|\p{Greek}{3,}"
    r"|\p{Coptic}{3,}"
    r"|\p{Cyrillic}{3,}"
    r"|\p{Armenian}{3,}"
    r"|\p{Hebrew}{3,}"
    r"|\p{Arabic}{3,}"
    r"|\p{Syriac}{3,}"
    r"|\p{Ethiopic}{3,}",
    regex.UNICODE,
)


def prepare_for_diff_oriental(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remover comentários XML/HTML
    text = regex.sub(r"<!--.*?-->", "", text, flags=regex.DOTALL)

    # une hifenização de fim de linha (ex: "interver-\nsion" → "interversion")
    text = regex.sub(r"-\n(\p{Letter})", r"\1", text)

    text = regex.sub(r"<notas>.*?</notas>", "", text, flags=regex.DOTALL)
    text = regex.sub(r"\p{Mn}", "", text)
    text = regex.sub(r"<[^>]+>", "", text)

    return [t.lower() for t in TOKEN_RE.findall(text)]


def clean_text_oriental(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remover comentários XML/HTML
    text = regex.sub(r"<!--.*?-->", "", text, flags=regex.DOTALL)

    # une hifenização de fim de linha (ex: "interver-\nsion" → "interversion")
    text = regex.sub(r"-\n(\p{Letter})", r"\1", text)

    text = regex.sub(r"<notas>.*?</notas>", "", text, flags=regex.DOTALL)
    text = regex.sub(r"\p{Mn}", "", text)
    text = regex.sub(r"<[^>]+>", "", text)

    return text


def strip_bidi_markers(text: str) -> str:
    # Remove LRM, RLM, e outros control characters bidirecionais
    return re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)


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

    # remove bloco de notas inteiro (conteúdo é meta-comentário, não transcrição)
    text = re.sub(r"<notas>.*?</notas>", "", text, flags=re.DOTALL)

    # remove XML tags se vier do LLM
    text = re.sub(r"<[^>]+>", "", text)

    # extrai tokens latinos >= 3 chars, lowercase
    return [t.lower() for t in re.findall(r"[a-zA-ZÀ-öø-ÿ]{3,}", text)]


def _split_segments(text: str, is_xml: bool) -> list[str]:
    """Divide texto em segmentos comparáveis (blocos ou parágrafos)."""
    if is_xml:
        # tenta extrair conteúdo de cada <bloco>...</bloco>
        blocks = re.findall(r"<bloco[^>]*>(.*?)</bloco>", text, flags=re.DOTALL)
        if blocks:
            return [b for b in blocks if b.strip()]
    # fallback: parágrafos por dupla quebra de linha
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [p for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _overlap_metrics(
    xml_tokens: list[str], tess_tokens: list[str]
) -> tuple[float, float, float, list[str], list[str], list[str]]:
    xml_set = set(xml_tokens)
    tess_set = set(tess_tokens)
    common = xml_set & tess_set

    llm_only = [t for t in xml_tokens if t not in tess_set]
    tess_only = [t for t in tess_tokens if t not in xml_set]

    if common:
        seq_ratio = difflib.SequenceMatcher(
            None,
            [t for t in xml_tokens if t in common],
            [t for t in tess_tokens if t in common],
        ).ratio()
    else:
        seq_ratio = 0.0

    overlap_ratio = len(common) / len(xml_set) if xml_set else 0.0
    recall_ratio = len(common) / len(tess_set) if tess_set else 0.0

    return overlap_ratio, recall_ratio, seq_ratio, llm_only, tess_only, sorted(common)


def preprocess_image(img_bgr: np.ndarray) -> np.ndarray:
    """Pré-processamento simples para OCR: grayscale + threshold."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # threshold suave; ajuste 160~190 conforme qualidade do scan
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    # Opcional: denoise leve
    # thresh = cv2.medianBlur(thresh, 3)
    return thresh


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def list_existing_images(images_dir: Path) -> List[Path]:
    return sorted(images_dir.glob(f"*.{IMAGE_FMT}"))


def stable_image_path(pdf_path: Path, images_dir: Path, page_num: int) -> Path:
    """Nome determinístico para as imagens, sem UUID no prefixo."""
    return images_dir / f"{pdf_path.stem}-{page_num:03d}.{IMAGE_FMT}"


def find_image_by_page(images_dir: Path, page_num: int) -> Optional[Path]:
    """
    Retorna uma imagem já existente para a página, seja com UUID ou já normalizada.
    Ex.: *-170.png corresponde à página 170.
    """
    stable_name = f"*{page_num:03d}.{IMAGE_FMT}"
    matches = sorted(images_dir.glob(stable_name))
    if matches:
        return matches[0]
    matches = sorted(images_dir.glob(f"*-{page_num}.{IMAGE_FMT}"))
    return matches[0] if matches else None


def purge_tesseract_cache(
    con: sqlite3.Connection, image_path: Path, lang: Optional[str] = None
) -> int:
    """
    Remove a entrada do cache para a imagem (opcionalmente filtrando por lang).
    Retorna o número de linhas removidas.
    """
    h = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if lang:
        cur = con.execute(
            "DELETE FROM tesseract_cache WHERE image_hash = ? AND lang = ?", (h, lang)
        )
    else:
        cur = con.execute("DELETE FROM tesseract_cache WHERE image_hash = ?", (h,))
    con.commit()
    return cur.rowcount


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


NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)


def page_number(p: Path) -> int | None:
    m = NUM_RE.search(p.name)
    if m:
        return int(m.group(1))
    # fallback: último bloco numérico antes da extensão
    m2 = re.search(r"(\d+)(?=\.[^.]+$)", p.name)
    return int(m2.group(1)) if m2 else None


def pages_to_images(
    pdf_path: Path,
    images_dir: Path,
    dpi: int = DEFAULT_DPI,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    text_dir: Optional[Path] = None,
    refresh_pages: Optional[set[int]] = None,
) -> List[Path]:
    """
    Converte páginas do PDF em imagens no disco e retorna a lista de caminhos.
    Usa paths_only=True para não manter PIL Images em RAM.
    Ordena pelo código numérico no final do nome do arquivo.
    """
    ensure_dir(images_dir)

    def sort_key(p: Path):
        num = parse_page_num_from_filename(p)
        return num if num is not None else 0

    refresh_set: set[int] = set(refresh_pages or [])

    def rename_to_stable(raw_paths: List[str]) -> List[Path]:
        """Converte nomes com UUID em prefixo determinístico e alinha txt correspondente."""
        normalized: list[Path] = []
        for raw in raw_paths:
            src = Path(raw)
            page_num = parse_page_num_from_filename(src)
            if page_num is None:
                continue

            target = stable_image_path(pdf_path, images_dir, page_num)
            # se já existe a versão estável, descartamos a duplicata
            if target.exists() and target != src:
                try:
                    src.unlink()
                except FileNotFoundError:
                    pass
            else:
                if src != target:
                    src.rename(target)

            normalized.append(target.resolve())

        return sorted(normalized, key=sort_key)

    # 1) Normaliza arquivos já existentes (remove UUID) e retorna cedo se nada a fazer
    existing = rename_to_stable([str(p) for p in list_existing_images(images_dir)])

    def filter_range(imgs: List[Path]) -> List[Path]:
        def ok(p: Path) -> bool:
            n = parse_page_num_from_filename(p)
            if n is None:
                return False
            if first_page is not None and n < first_page:
                return False
            if last_page is not None and n > last_page:
                return False
            return True

        return [p for p in imgs if ok(p)]

    if existing and not refresh_set:
        return filter_range(existing)

    # 2) Reextrai páginas específicas quando solicitado
    page_map: dict[int, Path] = {
        parse_page_num_from_filename(p): p
        for p in existing
        if parse_page_num_from_filename(p) is not None
    }

    if refresh_set:
        for page_num in sorted(refresh_set):
            # Se já existe qualquer imagem para essa página (UUID ou estável), apenas normaliza
            existing_page_img = find_image_by_page(images_dir, page_num)
            if existing_page_img:
                rename_to_stable([str(existing_page_img)])
                page_map[page_num] = stable_image_path(pdf_path, images_dir, page_num)
                continue

            raw = convert_from_path(
                str(pdf_path),
                dpi=dpi,
                fmt=IMAGE_FMT,
                output_folder=str(images_dir),
                paths_only=True,
                use_pdftocairo=USE_PDFTOCAIRO,
                thread_count=1,
                first_page=page_num,
                last_page=page_num,
            )
            refreshed = rename_to_stable(list(raw))
            if refreshed:
                page_map[page_num] = refreshed[0]

    # 3) Se ainda não há imagens (diretório limpo), processa o intervalo completo
    if not page_map:
        raw = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            fmt=IMAGE_FMT,
            output_folder=str(images_dir),
            paths_only=True,
            use_pdftocairo=USE_PDFTOCAIRO,
            thread_count=MAX_THREADS_CONVERT,
            first_page=first_page,
            last_page=last_page,
            output_file=pdf_path.stem,  # força prefixo determinístico
        )
        page_map = {
            parse_page_num_from_filename(p): p
            for p in rename_to_stable(list(raw))
            if parse_page_num_from_filename(p) is not None
        }

    return filter_range(sorted(page_map.values(), key=sort_key))


PROMPT = """
Você é um especialista em paleografia e transcrição de documentos históricos e edições críticas (Patrologia Graeca, Latina et Orientalis).
Sua missão é realizar uma análise visual exaustiva e transcrever cada vestígio de texto na imagem.

### ETAPA 1: ANÁLISE VISUAL OBRIGATÓRIA
Antes de gerar o XML, identifique se a página é:
- Uma capa ou página de guarda (pode estar em branco ou apenas amarelada).
- Uma página de texto denso (mesmo que degradado ou com scripts complexos como Siriaco/Grego).
- Uma página com gravuras ou tabelas.

### ETAPA 2: TRANSCRIÇÃO ESTRUTURADA (XML)
Se a página estiver REALMENTE em branco (apenas papel), use: <pagina estado="vazio" tipo="capa_ou_guarda" />
Caso contrário, siga o formato abaixo.

Valores permitidos para script: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Valores permitidos para tipo: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

REGRAS CRÍTICAS CONTRA OMISSÃO:
1. PROIBIÇÃO DE NEGATIVA: É terminantemente proibido ignorar blocos de texto ou afirmar que a página está em branco se houver qualquer vestígio de tinta. Se o texto estiver difícil, transcreva o que for possível; NUNCA desista de um bloco.
2. INTEGRIDADE: Cada nota de rodapé e aparato crítico deve ser mapeado. A omissão de blocos será considerada falha grave de processamento.
3. ESTADO DA PÁGINA: A tag raiz <pagina> deve conter o atributo 'estado' ("com_texto" ou "vazio").
4. BBOX: Deve ser x1,y1,x2,y2 (escala 0-1000).

Formato de saída:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    transcrição literal preservando quebras de linha
  </bloco>
  <notas>
    Explique aqui se houve scripts complexos identificados (ex: Siriaco Estrangelo).
  </notas>
</pagina>

MAIS REGRAS:
- Preserve a ordem visual (cima para baixo).
- Não traduza, não normalize, não invente texto.
- Use [ilegivel] apenas para palavras específicas, não para blocos inteiros.
- Retorne APENAS o XML.
""".strip()

PROMPT_VERIFY_VS_TESSERACT = """
Você é um especialista em paleografia e transcrição de documentos históricos e edições críticas (Patrologia Graeca, Latina et Orientalis).
Sua missão é comparar a imagem da página com o rascunho de OCR abaixo e produzir uma transcrição fiel, corrigindo erros do Tesseract e descartando qualquer trecho que não apareça na imagem.

<rascunho_ocr>
{tesseract_text}
</rascunho_ocr>

### ETAPA 1: ANÁLISE VISUAL OBRIGATÓRIA
Antes de gerar o XML, identifique se a página é:
- Uma capa ou página de guarda (pode estar em branco ou apenas amarelada).
- Uma página de texto denso (mesmo que degradado ou com scripts complexos como Siriaco/Grego).
- Uma página com gravuras ou tabelas.

### ETAPA 2: TRANSCRIÇÃO ESTRUTURADA (XML)
Se a página estiver REALMENTE em branco (apenas papel), use: <pagina estado="vazio" tipo="capa_ou_guarda" />
Caso contrário, siga o formato abaixo.

Valores permitidos para script: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Valores permitidos para tipo: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

REGRAS CRÍTICAS CONTRA OMISSÃO E ERROS DO OCR:
1. PROIBIÇÃO DE NEGATIVA: É terminantemente proibido ignorar blocos de texto ou afirmar que a página está em branco se houver qualquer vestígio de tinta. Se o texto estiver difícil, transcreva o que for possível; NUNCA desista de um bloco.
2. INTEGRIDADE: Cada nota de rodapé e aparato crítico deve ser mapeado. A omissão de blocos será considerada falha grave de processamento.
3. ESTADO DA PÁGINA: A tag raiz <pagina> deve conter o atributo 'estado' ("com_texto" ou "vazio").
4. BBOX: Deve ser x1,y1,x2,y2 (escala 0-1000).
5. USE O RASCUNHO COMO PISTA, NÃO COMO FONTE CONFIÁVEL: só aproveite palavras/trechos do <rascunho_ocr> que você confirma visualmente na imagem; corrija erros e descarte alucinações de caracteres, palavras, etc.
6. COERÊNCIA VISUAL: se o rascunho tiver linhas ausentes ou extras, siga SEMPRE o que está na imagem.
7. AVISO DE DEFEITOS: o OCR rascunho pode falhar e confundir caracteres de um idioma com outro, pode misturar dentro de um mesmo idioma, e pode não reconhecer caracteres especiais, ligaduras ou diacríticos; corrija conforme possível baseando-se na compreensão da imagem.

Formato de saída:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    transcrição literal preservando quebras de linha
  </bloco>
  <notas>
    Explique aqui se houve scripts complexos identificados (ex: Siriaco Estrangelo) ou correções relevantes feitas sobre o rascunho do Tesseract.
  </notas>
</pagina>

MAIS REGRAS:
- Preserve a ordem visual (cima para baixo).
- Não traduza, não normalize, não invente texto.
- Use [ilegivel] apenas para palavras específicas, não para blocos inteiros.
- Retorne APENAS o XML.
""".strip()

PROMPT_VERIFY_LLM_VS_TESSERACT = """
Você é um especialista em paleografia e transcrição de documentos históricos e edições críticas (Patrologia Graeca, Latina et Orientalis).
Sua missão é comparar a imagem da página com o rascunho de OCR abaixo e produzir uma transcrição fiel, corrigindo erros do Tesseract e descartando qualquer trecho que não apareça na imagem.

<rascunho_ocr>
{tesseract_text}
</rascunho_ocr>

<llm_ocr>
{llm_ocr}
</llm_ocr>

### ETAPA 1: ANÁLISE VISUAL OBRIGATÓRIA
Antes de gerar o XML, identifique se a página é:
- Uma capa ou página de guarda (pode estar em branco ou apenas amarelada).
- Uma página de texto denso (mesmo que degradado ou com scripts complexos como Siriaco/Grego).
- Uma página com gravuras ou tabelas.

### ETAPA 2: TRANSCRIÇÃO ESTRUTURADA (XML)
Se a página estiver REALMENTE em branco (apenas papel), use: <pagina estado="vazio" tipo="capa_ou_guarda" />
Caso contrário, siga o formato abaixo.

Valores permitidos para script: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Valores permitidos para tipo: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

REGRAS CRÍTICAS CONTRA OMISSÃO E ERROS DO OCR:
1. PROIBIÇÃO DE NEGATIVA: É terminantemente proibido ignorar blocos de texto ou afirmar que a página está em branco se houver qualquer vestígio de tinta. Se o texto estiver difícil, transcreva o que for possível; NUNCA desista de um bloco.
2. INTEGRIDADE: Cada nota de rodapé e aparato crítico deve ser mapeado. A omissão de blocos será considerada falha grave de processamento.
3. ESTADO DA PÁGINA: A tag raiz <pagina> deve conter o atributo 'estado' ("com_texto" ou "vazio").
4. BBOX: Deve ser x1,y1,x2,y2 (escala 0-1000).
5. USE O RASCUNHO COMO PISTA, NÃO COMO FONTE CONFIÁVEL: só aproveite palavras/trechos do <rascunho_ocr> que você confirma visualmente na imagem; corrija erros e descarte alucinações de caracteres, palavras, etc.
6. COERÊNCIA VISUAL: se o rascunho tiver linhas ausentes ou extras, siga SEMPRE o que está na imagem.
7. AVISO DE DEFEITOS: o OCR rascunho pode falhar e confundir caracteres de um idioma com outro, pode misturar dentro de um mesmo idioma, e pode não reconhecer caracteres especiais, ligaduras ou diacríticos; corrija conforme possível baseando-se na compreensão da imagem.
8. llm_ocr pode parecer mais organizado e fluente, entretanto, pode sofrer de alucinação de conteúdo e de estrutura.
9. Possíveis pistas para identificação real do texto nas línguas orientais: a maioria das páginas possui textos de anotações, críticas e em muitos casos, traduções da língua oriental para uma língua latina, pense na relação entre todos os textos.
10. O llm_ocr pode estar errado. Não use sua estrutura como referência para a ordem da página.

Formato de saída:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    transcrição literal preservando quebras de linha
  </bloco>
  <notas>
    Explique aqui se houve scripts complexos identificados (ex: Siriaco Estrangelo) ou correções relevantes feitas sobre o rascunho do Tesseract.
  </notas>
</pagina>

MAIS REGRAS:
- Preserve a ordem visual (cima para baixo).
- Não traduza, não normalize, não invente texto.
- Use [ilegivel] apenas para palavras específicas, não para blocos inteiros.
- Retorne APENAS o XML.
""".strip()


PROMPT_LLM_JUDGE = """
Você é um especialista em paleografia e transcrição de documentos históricos (Patrologia Graeca, Latina et Orientalis).
Compare a imagem com a transcrição na tag <ocr> e avalie a fidelidade.

# Critérios de avaliação

**Fidelidade** — quão bem a transcrição reflete o que está na imagem:
- alta: texto principal correto, erros mínimos ou apenas em scripts difíceis
- media: erros parciais, omissões menores, mas estrutura preservada
- baixa: erros significativos, blocos omitidos, confusão de scripts
- descartar: transcrição irreconhecível ou completamente incorreta

**Usabilidade** — se o texto é aproveitável para produção de resumos:
- alta: semântica preservada, termos principais identificáveis
- media: compreensível com esforço, perdas pontuais de sentido
- baixa: sentido comprometido por erros acumulados
- descartar: inutilizável

# Notas importantes
- Para blocos em scripts não-latinos (armênio, siríaco, grego), avalie apenas:
  (a) se o bloco está presente na transcrição
  (b) se a extensão aproximada parece compatível com a imagem
  (c) se não há confusão óbvia de script (ex: caracteres árabes no meio de armênio)
  Não avalie a correção caractere a caractere nesses scripts.
- Para blocos em francês/latim/inglês, avalie semântica e fidelidade completas.

Formato de saída esperado (retorne apenas o XML válido preenchido de acordo com o julgamento da imagem):
<avaliacao>
  <comentario>
    Comentário específico por bloco: o que está correto, o que está errado ou omitido.
  </comentario>
  <idiomas_identificados>
    <idioma>armênio</idioma>
    <!-- outros idiomas em PT-BR, cite o idioma identificado no texto, exemplo: "francês" -->
  </idiomas_identificados>
  <julgamento>
    <fidelidade>alta|media|baixa|descartar</fidelidade>
    <usabilidade>alta|media|baixa|descartar</usabilidade>
  </julgamento>
</avaliacao>

<ocr>
{llm_ocr}
</ocr>
""".strip()


def render_prompt_verify_vs_tesseract(
    tesseract_text: str, max_chars: int = 12000
) -> str:
    """Renderiza o prompt de comparação usando o rascunho do Tesseract.

    O rascunho é truncado para evitar prompts gigantes que explodem o token limit.
    """
    cleaned = tesseract_text.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n[TRUNCADO]"
    return PROMPT_VERIFY_VS_TESSERACT.format(tesseract_text=cleaned)


def preprocess_adaptative_ocr(img_bgr: np.ndarray) -> np.ndarray:
    """
    Pré-processamento otimizado para auto-ocr.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # normaliza iluminação não uniforme (papel amarelado, sombras de encadernação)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # binarização adaptativa — mais robusta que threshold global
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 10
    )

    # remove ruído de papel sem destruir caracteres pequenos
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return binary


def optimize_adaptative_cloud(image_path: Path, max_size=3200) -> str:
    """
    Aplica otimizações adaptativas na imagem para melhorar o OCR.
    """
    img_bgr = cv2.imread(str(image_path))
    img_processed = preprocess_adaptative_ocr(img_bgr)
    _, img_encoded = cv2.imencode(".jpg", img_processed, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(img_encoded).decode("utf-8")


def optimize_image_for_cloud(image_path, max_size=3200):
    """
    Reduz a imagem para acelerar o processamento na nuvem e evitar erro 500 por timeout.
    """
    img = Image.open(image_path)

    # Redimensiona mantendo a proporção se for maior que o max_size
    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    # Converte para escala de cinza para reduzir os canais de cor (já que o texto é P&B/Sépia)
    img = img.convert("L")

    buffered = io.BytesIO()
    # Salva com compressão JPEG para diminuir drasticamente o payload Base64
    img.save(buffered, format="JPEG", quality=85)

    return base64.b64encode(buffered.getvalue()).decode("utf-8")


images_max_size = [1600, 1800, 3200, 3200, 3200]


def ollama_process_image(
    image_path,
    model: str = DEFAULT_LLM_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    current_try: int = 1,
    prompt: str = PROMPT,
    reprocess: bool = False,
):
    if current_try <= 3:  # and not reprocess:
        img_b64 = optimize_image_for_cloud(image_path, images_max_size[current_try - 1])
    else:
        img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")

    if current_try > 4:
        prompt += f"\nEsta é uma tentativa de recuperação, número {current_try}\n"

    print(
        f"{len(img_b64) / 1024:.2f} KB de imagem para {image_path.name}; tamanho original {Path(image_path).stat().st_size / 1024:.2f} KB (try {current_try})"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Proceda conforme instruções do system.",
                "images": [img_b64],
            },
        ],
        "stream": False,
    }

    r = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=900,
    )

    # Melhor printar antes de lançar a exception
    if r.status_code != 200:
        print(f"=== ERRO 500 ===")
        print(f"Status: {r.status_code}")
        print(f"Headers: {dict(r.headers)}")
        print(f"Body: {r.text[:2000]}")  # primeiros 2000 chars
        print(f"Imagem: {image_path.name}")
        print(f"Tamanho payload: {len(json.dumps(payload))} bytes")
        print(f"================")

        with open("errors_500.log", "a") as f:
            f.write(
                f"{image_path.name} | B64 Size {len(img_b64)} | try={current_try} | {r.status_code} | {r.text[:500]} | Headers: {dict(r.headers)}\n"
            )

    r.raise_for_status()
    data = r.json()

    # print(data["message"]["content"])

    return data["message"]["content"]


def openai_process_image(
    image_path: Path,
    model: str = DEFAULT_OPENAI_MODEL,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    api_key: str | None = None,
    current_try: int = 1,
    prompt: str = PROMPT,
    mime: str = "image/png",
    reprocess: bool = False,
):
    """
    Faz OCR via endpoint de chat da OpenAI usando apenas requests.
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não encontrado no ambiente.")

    # Usa versão comprimida nas primeiras tentativas para evitar timeouts/payloads grandes
    if current_try <= 3 and not reprocess:
        img_b64 = optimize_image_for_cloud(image_path, images_max_size[current_try - 1])
        mime = "image/jpeg"  # Compressão para JPEG nas primeiras tentativas
    else:
        # Se estamos refazendo por motivo textual, vamos mandar a imagem original
        img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")

    if current_try > 4:
        prompt += f"\nEsta é uma tentativa de recuperação, número {current_try}\n"

    print(
        f"{len(img_b64) / 1024:.2f} KB de imagem para {image_path.name}; try {current_try} (openai)"
    )

    image_url = {"url": f"data:{mime};base64,{img_b64}"}

    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Proceda conforme instruções do system.",
                },
                {"type": "image_url", "image_url": image_url},
            ],
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "top_p": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }

    timeout = 120

    if "gpt-5" not in model:
        payload["temperature"] = 0.05
    else:
        if reprocess:
            payload["reasoning_effort"] = "medium"
            timeout = 900
        else:
            payload["reasoning_effort"] = "low"

    url = base_url.rstrip("/") + "/chat/completions"

    with make_session() as session:
        r = session.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            data=json.dumps(payload),
            timeout=timeout,
        )

    if r.status_code != 200:
        print(r.text)
    r.raise_for_status()
    data = r.json()

    return data["choices"][0]["message"]["content"]


def get_physical_ink_ratio(image_path):
    """Calcula a porcentagem da página que contém 'tinta' (texto/gráficos)."""
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0

    # Usamos threshold adaptativo para ignorar o amarelado do papel da Patrologia
    binary = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )

    ink_pixels = cv2.countNonZero(binary)
    total_pixels = binary.shape[0] * binary.shape[1]
    return (ink_pixels / total_pixels) * 100


def parse_llm_xml_stats(xml_text):
    """Extrai estatísticas do XML gerado pela LLM."""
    stats = {
        "text_length": len(xml_text),
        "total_bbox_area": 0,
        "is_complete": bool(re.search(r"</pagina>", xml_text))
        or bool(re.search(r"<pagina[^>]*estado\s*=\s*['\"]vazio['\"]", xml_text))
        or bool(
            re.search(r"<pagina[^>]*tipo\s*=\s*['\"]capa_ou_guarda['\"]", xml_text)
        ),
        "has_refusal": bool(
            re.search(
                r"(não há texto|página em branco|vazio|sem conteúdo)", xml_text, re.I
            )
        ),
    }

    # Tenta somar a área de todos os bboxes (normalizados 0-1000)
    # Área total da página no sistema 1000x1000 = 1.000.000
    bboxes = re.findall(r'bbox="(\d+),(\d+),(\d+),(\d+)"', xml_text)
    for b in bboxes:
        x1, y1, x2, y2 = map(int, b)
        area = (x2 - x1) * (y2 - y1)
        stats["total_bbox_area"] += area

    stats["coverage_ratio"] = stats["total_bbox_area"] / 1_000_000
    return stats


def parse_llm_estado(xml_text: str) -> str | None:
    """Extrai o atributo estado da tag <pagina>."""
    m = re.search(r'<pagina[^>]*estado="([^"]+)"', xml_text)
    return m.group(1) if m else None


def get_clean_ink_ratio(image_path):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    # Threshold adaptativo mais rigoroso
    binary = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8
    )  # Aumente o 8 para ignorar sombras leves

    # Acha todos os contornos
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Filtra: só conta manchas que tenham pelo menos 5x5 pixels (tamanho de um ponto/caractere pequeno)
    total_ink_area = sum(cv2.contourArea(c) for c in cnts if cv2.contourArea(c) > 20)

    total_pixels = img.shape[0] * img.shape[1]
    return (total_ink_area / total_pixels) * 100


def get_calibrated_ink_ratio(image_path):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0

    # 1. Normaliza a iluminação (remove o peso do marrom/amarelo)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    img_norm = clahe.apply(img)

    # 2. Binarização Adaptativa Rigorosa
    # O valor 15 (block size) e 10 (C) ajudam a ignorar o ruído do papel
    binary = cv2.adaptiveThreshold(
        img_norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )

    # 3. Limpeza de ruído (Morfologia)
    # Remove pontinhos isolados que o papel velho costuma criar
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    ink_pixels = cv2.countNonZero(binary)
    return (ink_pixels / (img.shape[0] * img.shape[1])) * 100


def validate_ocr_result(llm_text, image_path):
    """
    Avalia se o resultado parece OK ou se precisa de RERUN.
    Retorna: (bool_ok, "motivo")
    """
    xml_stats = parse_llm_xml_stats(llm_text)
    llm_estado = parse_llm_estado(llm_text)

    # 1. Integridade básica (sempre checar primeiro)
    if llm_estado not in {"com_texto", "vazio"}:
        return False, "ESTADO_AUSENTE_OU_INVALIDO"

    if llm_estado == "com_texto" and not re.search(r"</pagina>", llm_text):
        return False, "SEM_FECHO_PAGINA"

    if not xml_stats["is_complete"]:
        return False, "XML_INCOMPLETO"

    # 2. Early Exit (Otimização): Se o XML é rico, não gastamos CPU com OpenCV
    if xml_stats["text_length"] > 500 and not xml_stats["has_refusal"]:
        return True, "OK"

    # 3. Caso de "Capa ou Guarda" explicitamente declarada pela LLM
    # Se a LLM disse que é capa, costumamos aceitar, a menos que o OpenCV detecte MUITA tinta
    is_cover = 'tipo="capa_ou_guarda"' in llm_text

    # Classificação da página (vazia/capa/texto) usando heurísticas de cor/borda
    page_stats = classify_scan_page(
        Path(image_path), max_dim=2000, border_frac=0.08, center_frac=0.5
    )

    ink_ratio = page_stats.ink_ratio

    # 4. Validação da Capa: Se for capa, toleramos ink_ratio maior sem disparar erro
    if is_cover and page_stats.classification == "capa":
        if (
            ink_ratio < 4.0
        ):  # Capas costumam ter ruído de textura, aumentamos o threshold
            return True, "CAPA_CONFIRMADA"
        else:
            return False, f"CAPA_SUSPEITA (Tinta demais: {ink_ratio:.2f}%)"

    # 4.1 Cross-check do atributo estado da LLM com a classificação visual
    if llm_estado == "vazio":
        # Se é realmente texto, precisa ter uma certa quantidade de tinta
        if (
            page_stats.classification == "texto" and ink_ratio > 0.01
        ) or ink_ratio > 0.5:
            return (
                False,
                f"LLM_DISSE_VAZIO_MAS_IMAGEM_TEM_TEXTO (ink {ink_ratio:.2f}%, cls {page_stats.classification})",
            )

    # 5. Omissão em páginas comuns
    if ink_ratio > 1.5 and xml_stats["coverage_ratio"] < 0.05:
        return False, f"OMISSAO_PROVAVEL (Tinta: {ink_ratio:.2f}%)"

    return True, "OK"


def is_library_label(raw: str) -> bool:
    text = " ".join(prepare_for_diff(raw)).strip()

    # Padrão 1: Nome de universidade (comum em etiquetas de scan)
    univ_pattern = r"(UNIVERSITY\s+OF\s+[A-Z]+|COLLEGE|LIBRARY|INSTITUTE)"

    # Padrão 2: Sequência de código de barras (grupos de números longos)
    barcode_pattern = r"(\d{1,4}\s\d{4,8}\s\d{4,8}\s\d{1,2})"

    text_upper = text.upper()

    has_univ = re.search(univ_pattern, text_upper)
    has_barcode = re.search(barcode_pattern, text_upper)

    # Se tiver ambos ou apenas o código de barras formatado na capa, é etiqueta
    return bool(has_univ or has_barcode or "etiqueta de biblioteca" in raw) and (
        len(text) < 100
    )  # e o texto não pode ser muito longo


def latin_overlap_result(
    xml_text: str, tesseract_text: str, name: str = ""
) -> LatinOverlapResult:
    xml_tokens = prepare_for_diff(
        xml_text
    )  # já faz: remove tags, une hifens, extrai tokens
    tess_tokens = prepare_for_diff(tesseract_text)

    overlap_ratio, recall_ratio, seq_ratio, llm_only, tess_only, common = (
        _overlap_metrics(xml_tokens, tess_tokens)
    )

    if overlap_ratio < 0.5:
        # Debug prints para identificar o texto dos dois lados e o arquivo no terminal:
        print(
            f"[VERIFY] Resultado {name}; Overlap ratio: {overlap_ratio:.2f} Recall ratio: {recall_ratio:.2f} (LLM): {' '.join(xml_tokens)}; (Tesseract): {' '.join(tess_tokens)}"
        )

    # Comparação por segmentos (blocos ou parágrafos) para pegar casos de um bloco bom + lixo
    xml_segments = _split_segments(xml_text, is_xml=True)
    tess_segments = _split_segments(tesseract_text, is_xml=False)
    seg_count = min(len(xml_segments), len(tess_segments))
    segment_results: list[SegmentOverlap] = []

    for idx in range(seg_count):
        x_tok = prepare_for_diff(xml_segments[idx])
        t_tok = prepare_for_diff(tess_segments[idx])
        ovr, rec, seq, _, _, _ = _overlap_metrics(x_tok, t_tok)
        segment_results.append(
            SegmentOverlap(
                idx=idx,
                overlap_ratio=ovr,
                recall_ratio=rec,
                seq_ratio=seq,
                llm_token_count=len(x_tok),
                tess_token_count=len(t_tok),
            )
        )

    return LatinOverlapResult(
        overlap_ratio=overlap_ratio,
        recall_ratio=recall_ratio,
        seq_ratio=seq_ratio,
        llm_only=llm_only,
        tess_only=tess_only,
        common=common,
        llm_token_count=len(xml_tokens),
        tess_token_count=len(tess_tokens),
        segments=segment_results,
    )


def get_tokens_discrepance(num_tokens_1: int, num_tokens_2: int) -> float:
    if num_tokens_1 == 0 and num_tokens_2 == 0:
        return 0.0
    return abs(num_tokens_1 - num_tokens_2) / max(num_tokens_1, num_tokens_2)


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


def verify_page(
    img_path: Path,
    txt_dir: Path,
    lang: str = "fra+lat+grc+ell+syr",
    expect_txt_xml: bool = True,
) -> bool:
    """
    Verifica se a página foi processada corretamente.
    """
    page_txt_path = txt_path_for_image(img_path, txt_dir)
    if not page_txt_path.exists():
        print(f"[VERIFY] {img_path.name} — arquivo de texto não encontrado")
        return False

    if avalia_caso_perdido(img_path):
        print(f"[VERIFY] {img_path.name} — caso perdido")
        return True

    txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
    if len(txt.strip()) == 0:
        print(f"[VERIFY] {img_path.name} — texto vazio")
        return False

    if expect_txt_xml:
        if is_page_xml(txt):
            print(f"[VERIFY] {img_path.name} — página XML detectada")
            return True

        print(f"[VERIFY] {img_path.name} — texto não parece ser XML")
        return False

    tesseract_db = open_tesseract_cache_db()
    init_tesseract_cache(tesseract_db)

    tesseractres = run_tesseract_cached(tesseract_db, img_path, lang=lang)

    # Vou descartar os ilegíveis para evitar conflito com o Tesseract que não insere [ilegivel]
    overlap = latin_overlap_result(
        txt.replace("[ilegivel]", ""), tesseractres, name=img_path.name
    )

    if (
        overlap.recall_ratio > 0.2 and overlap.recall_ratio <= 0.6
    ):  # LLM detectou texto latino, mas Tesseract só pegou parte → possível degradação ou script complexo
        print(
            f"[VERIFY] {img_path.name} — possível degradação/script complexo (Overlap: {overlap.overlap_ratio:.2f}, Recall: {overlap.recall_ratio:.2f})"
        )

    if txt.count("[ilegivel]") > 5:
        print(f"[VERIFY] {img_path.name} — muitos tokens ilegíveis detectados")
        return False

    # return True  # desativado de momento, quero apenas rodar de novo os com muito token ilegível
    if (
        overlap.recall_ratio < 0.2 and overlap.overlap_ratio < 0.2
    ):  # LLM detectou muito texto latino, mas Tesseract quase nada → provável omissão ou alucinação
        # Debug prints para identificar o texto dos dois lados e o arquivo no terminal:

        page_stats = classify_scan_page(
            img_path, max_dim=2000, border_frac=0.08, center_frac=0.5
        )

        if is_library_label(txt):
            print(f"[VERIFY] {img_path.name} — é etiqueta de biblioteca")
            return True

        if (
            get_tokens_discrepance(overlap.llm_token_count, overlap.tess_token_count)
            > 0.9
            and overlap.llm_token_count > overlap.tess_token_count
        ):
            print(
                f"[VERIFY] {img_path.name} — alta discrepância de tokens; possível alucinação (LLM: {overlap.llm_token_count}, Tesseract: {overlap.tess_token_count})"
            )
            return False

        if overlap.tess_token_count < 10:
            print(f"[VERIFY] {img_path.name} — Tesseract detectou poucos tokens")
            return True

        print(
            f"[VERIFY] Resultado RAW {img_path.name}; Classificação OpenCV2: {page_stats.classification}; Overlap ratio: {overlap.overlap_ratio:.2f}; Recall ratio: {overlap.recall_ratio:.2f} (LLM): {txt}; (Tesseract): {tesseractres}"
        )

        is_cover = (
            'tipo="capa_ou_guarda"' in txt
            or "<pagina>" in txt
            or 'estado="vazio"' in txt
        )  # pagina sem props

        if page_stats.classification != "texto" and is_cover:
            if is_cover or overlap.llm_token_count < 20:
                # É capa, então supomos que a LLM esteja correta e o Tesseract só puxou lixo do ruído da capa
                print(f"[VERIFY] {img_path.name} — é capa")
                return True

        if is_cover:
            noise, score = entropy_lib.detect_ocr_noise(tesseractres)

            # noise, Score: >>> entropy_lib.detect_ocr_noise(raw)
            # (True, {'entropy': 5.346547694379972, 'compression_ratio': 0.6229532598987794, 'alpha_ratio': 0.42001836547291094, 'flags': ['alpha_lt_0.45', 'entropy_gt_4.7_and_low_alpha']})

            print(
                f"[VERIFY] {img_path.name} — ruído OCR detectado: {noise}, {score}, llm token count: {overlap.llm_token_count}"
            )
            if is_cover and overlap.llm_token_count < 30 and noise:
                print(
                    f"[VERIFY] {img_path.name} — provavelmente é uma capa mesmo; Ignorando página"
                )
                return True

        return False

    return True


def _latest_eval_is_good(
    image_path: Path, eval_db_path: Optional[Path]
) -> tuple[bool, str]:
    """
    Verifica no evaluations se a última avaliação é média/alta.
    Retorna (is_good, reason).
    """
    if not eval_db_path:
        return False, ""

    try:
        if not eval_db_path.exists():
            return False, ""
        con = evaluation_db.connect_eval_db(eval_db_path)
        row = con.execute(
            """
            SELECT fidelidade, usabilidade
            FROM evaluations
            WHERE image_path = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(image_path),),
        ).fetchone()
        if not row:
            return False, ""
        fid = (row["fidelidade"] or "").lower()
        usa = (row["usabilidade"] or "").lower()
        bad = {"baixa", "descartar"}
        if fid and fid not in bad and usa and usa not in bad:
            return True, "latest_eval_ok"
    except Exception:
        return False, ""
    return False, ""


def should_call_llm_judge(
    img_path: Path,
    txt_dir: Path,
    lang: str = "fra+lat+grc+ell+syr",
    eval_db_path: Optional[Path] = None,
) -> tuple[bool, str]:
    """
    Decide se deve acionar o LLM judge.
    Retorna (needs_llm, reason).
    """
    page_txt_path = txt_path_for_image(img_path, txt_dir)
    if not page_txt_path.exists():
        return True, "txt_missing"

    txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
    if len(txt.strip()) == 0:
        return True, "txt_empty"

    if avalia_caso_perdido(img_path):
        return False, "caso_perdido"

    is_good, reason = _latest_eval_is_good(img_path, eval_db_path)
    if is_good:
        return False, reason

    # reuse verificações determinísticas
    ok = verify_page(img_path, txt_dir, lang=lang)
    return (not ok, "verify_failed" if not ok else "verify_pass")


def llm_process_image_autoretry(
    image_path: Path,
    retries: int = 10,
    provider: str = "ollama",
    model: str = DEFAULT_LLM_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str | None = None,
    prompt: str = PROMPT,
    reprocess: bool = False,
):
    # ultimo_doc_legivel = ""
    txt = ""
    contagem_vazio = 0
    for i in range(retries):
        try:
            if provider == "openai":
                txt = openai_process_image(
                    image_path,
                    model=model,
                    base_url=openai_base_url,
                    api_key=openai_api_key,
                    current_try=i + 1,
                    prompt=prompt,
                    reprocess=reprocess,
                )
            else:
                txt = ollama_process_image(
                    image_path,
                    model=model,
                    url=url,
                    current_try=i + 1,
                    prompt=prompt,
                    reprocess=reprocess,
                )

            is_ok, reason = validate_ocr_result(txt, image_path)

            if is_ok:
                return txt
            else:
                print(
                    f"⚠️ Tentativa {i+1} falhou para {image_path.name}: {reason}. Tentando novamente..."
                )

                if "LLM_DISSE_VAZIO_MAS_IMAGEM_TEM_TEXTO" in reason:
                    contagem_vazio += 1
                # Opcional: aumentar a temperatura ou mudar o prompt levemente aqui

                if contagem_vazio > 3:
                    # Disse que está vazio 3 vezes, provavelmente o validador tem algo a ser revisado, retornar o texto atual
                    return txt
        except Exception as e:
            print(f"Attempt {i + 1} failed: {e} for {image_path.name}")
            time.sleep(2 * (i + 1))  # espera um pouco antes de tentar novamente

    if len(txt) > 0:
        return txt

    raise RuntimeError("All attempts failed: " + image_path.name)


def llm_process_chat_retry(
    image_path: Path,
    retries: int = 10,
    provider: str = "ollama",
    model: str = DEFAULT_LLM_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str | None = None,
    prompt: str = PROMPT,
    reprocess: bool = False,
):
    # ultimo_doc_legivel = ""
    txt = ""
    for i in range(retries):
        try:
            if provider == "openai":
                txt = openai_process_image(
                    image_path,
                    model=model,
                    base_url=openai_base_url,
                    api_key=openai_api_key,
                    current_try=i + 1,
                    prompt=prompt,
                    reprocess=reprocess,
                )
            else:
                txt = ollama_process_image(
                    image_path,
                    model=model,
                    url=url,
                    current_try=i + 1,
                    prompt=prompt,
                    reprocess=reprocess,
                )

            return txt
        except Exception as e:
            print(f"Attempt {i + 1} failed: {e} for {image_path.name}")
            time.sleep(2 * (i + 1))  # espera um pouco antes de tentar novamente

    if len(txt) > 0:
        return txt

    raise RuntimeError("All attempts failed: " + image_path.name)


def ocr_tesseract(img_path: Image, lang: str = DEFAULT_LANG) -> str:
    """OCR com pré-processamento adaptativo."""
    with Image.open(img_path) as pil_im:
        im_bgr = cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)
        im_pre = preprocess_image(im_bgr)
        pil_pre = Image.fromarray(im_pre)

        txt = pytesseract.image_to_string(
            pil_pre,
            lang=lang,
            config="--psm 3 --oem 1",  # psm 3 = layout automático, oem 1 = LSTM
        )

    return txt


def ocr_tesseract_raw(img_path: Image, lang: str = DEFAULT_LANG) -> str:
    """OCR direto no original (sem pré-processamento)."""
    with Image.open(img_path) as pil_im:
        txt = pytesseract.image_to_string(
            pil_im,
            lang=lang,
            config="--psm 3 --oem 1",
        )
    return txt


def run_tesseract_cached(
    con: sqlite3.Connection, image_path: Path, lang: str, force: bool = False
) -> str:
    h = hashlib.sha256(image_path.read_bytes()).hexdigest()

    if force:
        con.execute(
            "DELETE FROM tesseract_cache WHERE image_hash = ? AND lang = ?", (h, lang)
        )
        con.commit()
    else:
        cached = get_tesseract_cached(con, image_path, lang)
        if cached is not None:
            if len(cached.strip()) < 100:
                # cache parece corrompido/incompleto, recalcula
                con.execute(
                    "DELETE FROM tesseract_cache WHERE image_hash = ? AND lang = ?",
                    (h, lang),
                )
                con.commit()
            else:
                return cached

    result = ocr_tesseract(image_path, lang=lang)

    # Fallback: se a saída parecer muito curta, tenta sem pré-processamento
    if len(result.strip()) < 50:
        alt = ocr_tesseract_raw(image_path, lang=lang)
        if len(alt.strip()) > len(result.strip()) * 1.5:
            print(
                f"[TESS] Fallback raw melhor para {image_path.name} (len {len(alt)} vs {len(result)})"
            )
            result = alt

    con.execute(
        "INSERT OR REPLACE INTO tesseract_cache (image_hash, lang, result, imgpath) VALUES (?, ?, ?, ?)",
        (h, lang, result, str(image_path)),
    )
    con.commit()
    return result


def ocr_images_to_text(
    images: List[Path],
    txt_dir: Path,
    lang: str = DEFAULT_LANG,
    save_all_text_path: Optional[Path] = None,
    algorithm: str = "ollama",
    llm_model: str = DEFAULT_LLM_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str | None = None,
    prompt: str = PROMPT,
) -> None:
    """
    Faz OCR página-a-página e salva um .txt por página em txt_dir.
    Opcionalmente concatena tudo em um único arquivo em save_all_text_path.
    """
    ensure_dir(txt_dir)
    all_text_chunks: List[str] = []

    for i, img_path in enumerate(images, start=1):
        print(f"[OCR] Página {i}/{len(images)}: {img_path.name}")

        # se já existe o txt desta página, reaproveita
        page_txt_path = txt_path_for_image(img_path, txt_dir)
        if page_txt_path.exists():
            txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
            all_text_chunks.append(txt)
            continue

        # leitura streaming, garantindo liberação de memória
        if algorithm in {"ollama", "openai"}:
            txt = llm_process_image_autoretry(
                img_path,
                provider=algorithm,
                model=llm_model,
                url=ollama_url,
                openai_base_url=openai_base_url,
                openai_api_key=openai_api_key,
                prompt=prompt,
            )
        else:
            txt = ocr_tesseract(img_path)

        # salva o txt da página
        page_txt_path.write_text(txt, encoding="utf-8")
        all_text_chunks.append(txt)

    # arquivo único concatenado (opcional)
    if save_all_text_path:
        save_all_text_path.write_text("\n\n".join(all_text_chunks), encoding="utf-8")


"""
Examples:  

 
setenv OMP_PLACES threads 
setenv OMP_PLACES "threads(4)" 
setenv OMP_PLACES "{0,1,2,3},{4,5,6,7},{8,9,10,11},{12,13,14,15}" 
setenv OMP_PLACES "{0:4},{4:4},{8:4},{12:4}" 
setenv OMP_PLACES "{0:4}:4:4"
"""
# 12 cores, 24 threads CPU, OMP_PLACES
places = [
    "{0,1}",
    "{2,3}",
    "{4,5}",
    "{6,7}",
    "{8,9}",
    "{10,11}",
    "{12,13}",
    "{14,15}",
    "{16,17}",
    "{18,19}",
    "{20,21}",
    "{22,23}",
]


def return_place_by_process_index(i: int) -> str:
    return places[i % len(places)]


def apply_to_env_by_process_index(i: int) -> None:
    os.environ["OMP_PLACES"] = return_place_by_process_index(i)
    print(f"Process {i} applying OMP_PLACES={os.environ['OMP_PLACES']}")


# ---- pool helpers ----
# Variável global para rastrear os processos inicializados
_process_counter_lock = mp.Lock()
_process_counter = mp.Value("i", 0)
_process_index_map = {}


def _init_omp_env(omp_threads: int = 2):
    """Inicializado no início de cada worker do Pool"""
    global _process_index_map

    # Obter PID atual
    current_pid = os.getpid()

    # Atribuir um índice sequencial para este processo
    with _process_counter_lock:
        if current_pid not in _process_index_map:
            process_index = _process_counter.value
            _process_index_map[current_pid] = process_index
            _process_counter.value += 1
        else:
            process_index = _process_index_map[current_pid]

    # Aplicar configurações de ambiente baseadas no índice do processo
    apply_to_env_by_process_index(process_index)

    # Configurações adicionais do OpenMP
    os.environ["OMP_THREAD_LIMIT"] = str(omp_threads)
    os.environ["OMP_NUM_THREADS"] = str(omp_threads)
    os.environ["OMP_DYNAMIC"] = "TRUE"
    os.environ.setdefault("OMP_PROC_BIND", "spread")

    print(f"Worker PID {current_pid} inicializado com índice {process_index}")
    print(f"OMP_PLACES={os.environ.get('OMP_PLACES', 'not set')}")
    print(f"OMP_THREAD_LIMIT={os.environ.get('OMP_THREAD_LIMIT')}")

    return process_index


def get_current_process_index() -> int:
    """Retorna o índice do processo atual baseado no PID"""
    current_pid = os.getpid()
    return _process_index_map.get(current_pid, -1)


def verify_one(
    img_path: Path, txt_dir: Path, lang: str = "fra+lat+grc+ell+syr"
) -> tuple[Path, bool]:
    """
    Verifica se uma única página foi processada corretamente.
    """

    # Obter informações do processo atual
    current_pid = os.getpid()
    process_index = get_current_process_index()
    omp_places = os.environ.get("OMP_PLACES", "not set")

    print(
        f"[{time.strftime('%H:%M:%S')}] [START] {img_path.name} (PID: {current_pid}, Index: {process_index}, OMP_PLACES: {omp_places})"
    )

    return img_path, verify_page(img_path, txt_dir, lang=lang)


def _ocr_one(
    img_path: Path,
    txt_dir: Path,
    lang: str,
    algorithm: str,
    llm_model: str,
    ollama_url: str,
    openai_base_url: str,
    openai_api_key: str | None,
    reprocess: bool = False,
) -> str:
    start_total = time.time()

    # Obter informações do processo atual
    current_pid = os.getpid()
    process_index = get_current_process_index()
    omp_places = os.environ.get("OMP_PLACES", "not set")

    print(
        f"[{time.strftime('%H:%M:%S')}] [START] {img_path.name} (PID: {current_pid}, Index: {process_index}, OMP_PLACES: {omp_places})"
    )

    txt = None
    page_txt_path = txt_path_for_image(img_path, txt_dir)
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

        # Iremos usar o original no reprocessamento
        if not reprocess:
            if len(txt.strip()) == 0:
                print(
                    f"[{time.strftime('%H:%M:%S')}] [WARN] {img_path.name} (txt vazio)"
                )
            else:
                print(
                    f"[{time.strftime('%H:%M:%S')}] [SKIP] {img_path.name} (txt já existe)"
                )
                return txt

    if algorithm in {"ollama", "openai"}:

        if reprocess:
            tesseract_db = open_tesseract_cache_db()
            init_tesseract_cache(tesseract_db)

            tesseractres = strip_bidi_markers(
                run_tesseract_cached(tesseract_db, img_path, lang=lang)
            ).strip()

            if txt is None:
                txt = ""
                print(
                    f"[{time.strftime('%H:%M:%S')}] [WARN] {img_path.name} (txt vazio)"
                )
            else:
                txt = clean_text_oriental(txt).strip()

            # PROMPT_VERIFY_LLM_VS_TESSERACT
            prompt_llm_judge = PROMPT_VERIFY_LLM_VS_TESSERACT.format(
                tesseract_text=tesseractres, llm_ocr=txt
            )

            print(f"PROMPT_VERIFY_LLM_VS_TESSERACT  {prompt_llm_judge}")

            t4 = time.time()
            txt = llm_process_image_autoretry(
                img_path,
                provider=algorithm,
                model=llm_model,
                url=ollama_url,
                openai_base_url=openai_base_url,
                openai_api_key=openai_api_key,
                prompt=prompt_llm_judge,
                reprocess=reprocess,
            )
            t5 = time.time()
            print(
                f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
            )

            print(f"PROMPT_VERIFY_LLM_VS_TESSERACT  {prompt_llm_judge}\n{txt}")

        else:
            t4 = time.time()
            txt = llm_process_image_autoretry(
                img_path,
                provider=algorithm,
                model=llm_model,
                url=ollama_url,
                openai_base_url=openai_base_url,
                openai_api_key=openai_api_key,
            )
            t5 = time.time()
            print(
                f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
            )
    else:
        # leitura
        t0 = time.time()
        with Image.open(img_path) as pil_im:
            im_bgr = cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)
        t1 = time.time()
        print(
            f"[{time.strftime('%H:%M:%S')}] {img_path.name} — leitura+conversão: {t1 - t0:.3f}s"
        )

        # pré-processamento
        t2 = time.time()
        im_pre = preprocess_image(im_bgr)
        t3 = time.time()
        print(
            f"[{time.strftime('%H:%M:%S')}] {img_path.name} — preprocessamento: {t3 - t2:.3f}s"
        )

        # OCR
        t4 = time.time()
        pil_pre = Image.fromarray(im_pre)
        txt = pytesseract.image_to_string(
            pil_pre, lang=lang
        )  # opcional: passa config se quiser
        t5 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] {img_path.name} — OCR: {t5 - t4:.3f}s")

    # salvar
    page_txt_path.write_text(txt, encoding="utf-8")

    total = time.time() - start_total
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — total: {total:.3f}s, text tamanho: {len(txt)}"
    )

    return txt


def ocr_images_to_text_parallel(
    images: List[Path],
    txt_dir: Path,
    lang: str = DEFAULT_LANG,
    processes: int = 4,
    omp_threads_per_proc: int = 2,
    chunksize: int = 2,
    maxtasksperchild: int = 1000,
    algorithm: str = "tesseract",
    llm_model: str = DEFAULT_LLM_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str | None = None,
    save_all_text_path: Optional[Path] = None,
    reprocess: bool = False,
) -> None:
    ensure_dir(txt_dir)

    # Reinicializar contadores globais para cada execução
    global _process_counter, _process_index_map
    with _process_counter_lock:
        _process_counter.value = 0
        _process_index_map.clear()

    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    with ctx.Pool(
        processes=processes,
        initializer=_init_omp_env,
        initargs=(omp_threads_per_proc,),
        maxtasksperchild=maxtasksperchild,
    ) as pool:
        worker = partial(
            _ocr_one,
            txt_dir=txt_dir,
            lang=lang,
            algorithm=algorithm,
            llm_model=llm_model,
            ollama_url=ollama_url,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            reprocess=reprocess,
        )
        # imap_unordered tende a dar melhor throughput geral
        all_text_chunks = list(pool.imap_unordered(worker, images, chunksize=chunksize))

    if save_all_text_path:
        save_all_text_path.write_text("\n\n".join(all_text_chunks), encoding="utf-8")


def verify_all_parallel(
    images: List[Path],
    txt_dir: Path,
    lang: str = "fra+lat+grc+ell+syr",
    processes: int = 4,
    omp_threads_per_proc: int = 2,
    chunksize: int = 2,
    maxtasksperchild: int = 1000,
) -> list[Path]:
    ensure_dir(txt_dir)

    # Reinicializar contadores globais para cada execução
    global _process_counter, _process_index_map
    with _process_counter_lock:
        _process_counter.value = 0
        _process_index_map.clear()

    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    with ctx.Pool(
        processes=processes,
        initializer=_init_omp_env,
        initargs=(omp_threads_per_proc,),
        maxtasksperchild=maxtasksperchild,
    ) as pool:
        worker = partial(verify_one, txt_dir=txt_dir, lang=lang)
        results = pool.imap_unordered(worker, images, chunksize=chunksize)

        failures = [img_path for img_path, is_valid in results if not is_valid]

    for img_path in failures:
        print(f"[VERIFY] {img_path.name} — falhou na verificação")

    return failures


def judge_one(
    img_path: Path,
    txt_dir: Path,
    lang: str,
    algorithm: str,
    llm_model: str,
    ollama_url: str,
    openai_base_url: str,
    openai_api_key: str | None,
    eval_db_path: Path,
    prompt_version: str,
    judge_force: bool = False,
) -> tuple[Path, bool]:
    """
    Gating determinístico + LLM judge opcional.
    """
    con = evaluation_db.connect_eval_db(eval_db_path)
    evaluation_db.init_eval_schema(con)

    needs_llm = judge_force
    decision_reason = "judge_force" if judge_force else ""

    if not needs_llm:
        needs_llm, decision_reason = should_call_llm_judge(
            img_path, txt_dir, lang=lang, eval_db_path=eval_db_path
        )

    page_txt_path = txt_path_for_image(img_path, txt_dir)
    txt_content = ""
    if page_txt_path.exists():
        txt_content = page_txt_path.read_text(encoding="utf-8", errors="ignore")

    volume_id = infer_volume_id(img_path)
    page_num = parse_page_num_from_filename(img_path)

    if not needs_llm:
        evaluation_db.record_evaluation(
            con,
            volume_id=volume_id,
            page_num=page_num,
            image_path=img_path,
            text_path=page_txt_path if page_txt_path.exists() else None,
            provider=algorithm,
            model=llm_model,
            prompt_version=prompt_version,
            decision="deterministic_pass",
            deterministic_reason=decision_reason,
            xml_raw=None,
            parse_result={},
            duration_ms=0,
            status="deterministic_pass",
        )
        print(f"[JUDGE] {img_path.name}: skip LLM ({decision_reason})")
        return img_path, True

    prompt_llm_judge = PROMPT_LLM_JUDGE.format(llm_ocr=txt_content)
    t0 = time.time()
    xml_result = llm_process_chat_retry(
        img_path,
        provider=algorithm,
        model=llm_model,
        url=ollama_url,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        prompt=prompt_llm_judge,
        reprocess=True,
    )
    t1 = time.time()

    print(f"PROMPT_LLM_JUDGE  {prompt_llm_judge}\nSAÍDA DA LLM:\n{xml_result}")

    parse_result = evaluation_db.parse_llm_judge_xml(xml_result)
    status = parse_result.get("status", "parse_error")

    evaluation_db.record_evaluation(
        con,
        volume_id=volume_id,
        page_num=page_num,
        image_path=img_path,
        text_path=page_txt_path if page_txt_path.exists() else None,
        provider=algorithm,
        model=llm_model,
        prompt_version=prompt_version,
        decision="llm_judged",
        deterministic_reason=decision_reason,
        xml_raw=xml_result,
        parse_result=parse_result,
        duration_ms=(t1 - t0) * 1000,
        status=status,
    )

    return img_path, status == "parse_ok"


def judge_all_parallel(
    images: List[Path],
    txt_dir: Path,
    eval_db_path: Path,
    lang: str = "fra+lat+grc+ell+syr",
    processes: int = 4,
    omp_threads_per_proc: int = 2,
    chunksize: int = 2,
    maxtasksperchild: int = 1000,
    algorithm: str = "ollama",
    llm_model: str = DEFAULT_LLM_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str | None = None,
    prompt_version: str = "",
    judge_force: bool = False,
) -> list[Path]:
    ensure_dir(txt_dir)

    global _process_counter, _process_index_map
    with _process_counter_lock:
        _process_counter.value = 0
        _process_index_map.clear()

    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    with ctx.Pool(
        processes=processes,
        initializer=_init_omp_env,
        initargs=(omp_threads_per_proc,),
        maxtasksperchild=maxtasksperchild,
    ) as pool:
        worker = partial(
            judge_one,
            txt_dir=txt_dir,
            lang=lang,
            algorithm=algorithm,
            llm_model=llm_model,
            ollama_url=ollama_url,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            eval_db_path=eval_db_path,
            prompt_version=prompt_version,
            judge_force=judge_force,
        )

        results = pool.imap_unordered(worker, images, chunksize=chunksize)
        failures = [img_path for img_path, ok in results if not ok]

    for img_path in failures:
        print(f"[JUDGE] {img_path.name} — falhou no parse do LLM judge")

    return failures


def main():
    import argparse

    ap = argparse.ArgumentParser(description="PDF → imagens → OCR (paralelo com Pool).")
    ap.add_argument("pdf", type=str)
    ap.add_argument("--out", type=str, default="out")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--lang", type=str, default=DEFAULT_LANG)
    ap.add_argument("--first", type=int, default=None)
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--concat", action="store_true")
    ap.add_argument("--procs", type=int, default=12, help="processos no Pool")
    ap.add_argument(
        "--omp-threads", type=int, default=2, help="threads OpenMP por processo"
    )
    ap.add_argument("--chunksize", type=int, default=2)
    ap.add_argument("--maxtasksperchild", type=int, default=1000)
    ap.add_argument(
        "--algorithm",
        choices=["tesseract", "ollama", "openai"],
        default="tesseract",
        help="Escolhe engine: tesseract (default), ollama ou openai.",
    )
    ap.add_argument(
        "--llm-model",
        type=str,
        default=DEFAULT_LLM_MODEL,
        help=f"Modelo usado quando --algorithm usa LLM (default: {DEFAULT_LLM_MODEL} para Ollama, {DEFAULT_OPENAI_MODEL} para OpenAI).",
    )
    ap.add_argument(
        "--ollama-url",
        type=str,
        default=DEFAULT_OLLAMA_URL,
        help=f"Endpoint Ollama (default: {DEFAULT_OLLAMA_URL}).",
    )
    ap.add_argument(
        "--openai-base-url",
        type=str,
        default=DEFAULT_OPENAI_BASE_URL,
        help=f"Endpoint OpenAI (default: {DEFAULT_OPENAI_BASE_URL}).",
    )
    ap.add_argument(
        "--openai-api-key",
        type=str,
        default=None,
        help="API key OpenAI (fallback: variável de ambiente OPENAI_API_KEY).",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help="Verifica a integridade dos textos comparando resultados .txt prontos com o Tesseract.",
    )
    ap.add_argument(
        "--verify-fix",
        action="store_true",
        default=False,
        help="Verifica e corrige a integridade dos textos comparando resultados .txt prontos com o Tesseract.",
    )
    ap.add_argument(
        "--verify-judge-llm",
        action="store_true",
        default=False,
        help="Executa o LLM judge com gating determinístico; chama LLM só quando necessário.",
    )
    ap.add_argument(
        "--judge-force",
        action="store_true",
        default=False,
        help="Força rodar o LLM judge em todas as páginas (ignora o gating).",
    )
    ap.add_argument(
        "--refresh-pages",
        type=str,
        default="",
        help="Lista de páginas para reextrair, separadas por vírgula (ex.: 12,45,102). Usa nomes estáveis sem UUID.",
    )
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF não encontrado: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # Flags mutuamente exclusivas
    verify_flags = [
        args.verify,
        args.verify_fix,
        args.verify_judge_llm,
    ]
    if sum(1 for f in verify_flags if f) > 1:
        print(
            "Use apenas uma das flags: --verify, --verify-fix ou --verify-judge-llm.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Ajustes de defaults para OpenAI
    if args.algorithm == "openai" and args.llm_model == DEFAULT_LLM_MODEL:
        args.llm_model = DEFAULT_OPENAI_MODEL

    if args.openai_api_key is None:
        args.openai_api_key = os.getenv("OPENAI_API_KEY")

    base_out = Path(args.out) / pdf_path.stem
    images_dir = base_out / "images"
    text_dir = base_out / "text"
    ensure_dir(base_out)

    refresh_pages = set()
    if args.refresh_pages.strip():
        try:
            refresh_pages = {
                int(x) for x in re.split(r"[,\s]+", args.refresh_pages.strip()) if x
            }
        except ValueError:
            print(
                "--refresh-pages deve conter apenas números separados por vírgula",
                file=sys.stderr,
            )
            sys.exit(1)

    print("Convertendo páginas para imagens (com cache em disco)...")
    images = pages_to_images(
        pdf_path,
        images_dir,
        dpi=args.dpi,
        first_page=args.first,
        last_page=args.last,
        text_dir=text_dir,
        refresh_pages=refresh_pages,
    )
    print(f"Total de imagens: {len(images)}")

    concat_path = (base_out / "texto_extraido.txt") if args.concat else None

    if args.verify_judge_llm:
        print("Rodando avaliação com LLM judge (gating determinístico)...")
        prompt_version = evaluation_db.compute_prompt_version(PROMPT_LLM_JUDGE)
        eval_db_path = Path("data/ocr_eval.db")

        failures = judge_all_parallel(
            images,
            text_dir,
            eval_db_path=eval_db_path,
            lang=args.lang,
            processes=args.procs,
            omp_threads_per_proc=args.omp_threads,
            chunksize=args.chunksize,
            maxtasksperchild=args.maxtasksperchild,
            algorithm=args.algorithm if len(args.algorithm) > 0 else "ollama",
            llm_model=args.llm_model,
            ollama_url=args.ollama_url,
            openai_base_url=args.openai_base_url,
            openai_api_key=args.openai_api_key,
            prompt_version=prompt_version,
            judge_force=args.judge_force,
        )

        if failures:
            print(f"Páginas com falha de parse: {[p.name for p in failures]}")
            print("Reprocessando imagens com falha...")

            algorithm = args.algorithm if len(args.algorithm) > 0 else "ollama"
            if algorithm == "openai" and args.llm_model == DEFAULT_LLM_MODEL:
                args.llm_model = DEFAULT_OPENAI_MODEL

            ocr_images_to_text_parallel(
                failures,
                text_dir,
                lang=args.lang,
                processes=args.procs,
                omp_threads_per_proc=args.omp_threads,
                chunksize=args.chunksize,
                maxtasksperchild=args.maxtasksperchild,
                algorithm=algorithm,
                llm_model=args.llm_model,
                ollama_url=args.ollama_url,
                openai_base_url=args.openai_base_url,
                openai_api_key=args.openai_api_key,
                save_all_text_path=concat_path,
                reprocess=True,
            )
        print("Avaliação concluída.")
        return

    if args.verify or args.verify_fix:
        print("Verificando integridade dos textos...")
        failures = verify_all_parallel(
            images,
            text_dir,
            lang=args.lang,
            processes=args.procs,
            omp_threads_per_proc=args.omp_threads,
            chunksize=args.chunksize,
            maxtasksperchild=args.maxtasksperchild,
        )

        if failures:
            print(f"Verificação falhou para as seguintes imagens: {failures}")

        if failures and args.verify_fix:
            print("Reprocessando imagens com falha...")

            algorithm = args.algorithm if len(args.algorithm) > 0 else "ollama"
            if algorithm == "openai" and args.llm_model == DEFAULT_LLM_MODEL:
                args.llm_model = DEFAULT_OPENAI_MODEL

            ocr_images_to_text_parallel(
                failures,
                text_dir,
                lang=args.lang,
                processes=args.procs,
                omp_threads_per_proc=args.omp_threads,
                chunksize=args.chunksize,
                maxtasksperchild=args.maxtasksperchild,
                algorithm=algorithm,
                llm_model=args.llm_model,
                ollama_url=args.ollama_url,
                openai_base_url=args.openai_base_url,
                openai_api_key=args.openai_api_key,
                save_all_text_path=concat_path,
                reprocess=True,
            )
        print("Verificação concluída.")
        return

    print("OCR paralelo (Pool)...")
    ocr_images_to_text_parallel(
        images,
        text_dir,
        lang=args.lang,
        processes=args.procs,
        omp_threads_per_proc=args.omp_threads,
        chunksize=args.chunksize,
        maxtasksperchild=args.maxtasksperchild,
        algorithm=args.algorithm,
        llm_model=args.llm_model,
        ollama_url=args.ollama_url,
        openai_base_url=args.openai_base_url,
        openai_api_key=args.openai_api_key,
        save_all_text_path=concat_path,
    )
    print("Concluído.")


if __name__ == "__main__":
    main()
