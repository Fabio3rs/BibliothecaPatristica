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

import entropy_lib

# ============ CONFIGS PADRÃO ============
DEFAULT_DPI = 300  # 300dpi é o "doce" do Tesseract; subir só se necessário
DEFAULT_LANG = "lat"  # requer pacotes traineddata do Tesseract para latim
USE_PDFTOCAIRO = True  # geralmente mais estável / eficiente
IMAGE_FMT = "png"  # png ou jpeg (evitar PPM para não inflar memória)
MAX_THREADS_CONVERT = 12  # ajuste conforme seus núcleos
DEFAULT_LLM_MODEL = "qwen3.5:397b-cloud"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"

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
        "SELECT result FROM tesseract_cache WHERE image_hash = ? AND lang = ?",
        (h, lang),
    ).fetchone()
    return row["result"] if row else None


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
) -> List[Path]:
    """
    Converte páginas do PDF em imagens no disco e retorna a lista de caminhos.
    Usa paths_only=True para não manter PIL Images em RAM.
    Ordena pelo código numérico no final do nome do arquivo.
    """
    ensure_dir(images_dir)

    def sort_key(p: Path):
        m = re.search(r"-(\d+)\.[^.]+$", p.name)
        return int(m.group(1)) if m else 0

    # se já existem imagens, reaproveita
    existing = list_existing_images(images_dir)
    if existing:
        return [p.resolve() for p in sorted(existing, key=sort_key)]

    # senão, converte agora
    paths = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        fmt=IMAGE_FMT,
        output_folder=str(images_dir),
        paths_only=True,  # retorna só caminhos (sem PIL em memória)
        use_pdftocairo=USE_PDFTOCAIRO,  # backend pdftocairo (bom em PDFs complexos)
        thread_count=MAX_THREADS_CONVERT,
        first_page=first_page,
        last_page=last_page,
    )
    # ordena pelo código numérico no final do nome
    path_objs = sorted((Path(p) for p in paths), key=sort_key)
    return [p.resolve() for p in path_objs]


PROMPT = """
Você é um especialista em paleografia e transcrição de documentos históricos e edições críticas (Patrologia Orientalis).
Sua missão é realizar uma análise visual exaustiva e transcrever cada vestígio de texto na imagem.

### ETAPA 1: ANÁLISE VISUAL OBRIGATÓRIA
Antes de gerar o XML, identifique se a página é:
- Uma capa ou página de guarda (pode estar em branco ou apenas amarelada).
- Uma página de texto denso (mesmo que degradado ou com scripts complexos como Siriaco/Grego).
- Uma página com gravuras ou tabelas.

### ETAPA 2: TRANSCRIÇÃO ESTRUTURADA (XML)
Se a página estiver REALMENTE em branco (apenas papel), use: <pagina estado="vazio" tipo="capa_ou_guarda" />
Caso contrário, siga o formato abaixo.

Valores permitidos para script: latino, grego, copta, siriaco, cirilico, ethiopico, misto, desconhecido.
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


def ollama_process_image(
    image_path,
    model: str = DEFAULT_LLM_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    current_try: int = 1,
    prompt: str = PROMPT,
):
    if current_try <= 2:
        img_b64 = optimize_image_for_cloud(image_path)
    else:
        img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
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
        print(r.text)
    r.raise_for_status()
    data = r.json()

    # print(data["message"]["content"])

    return data["message"]["content"]


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


def verify_page(
    img_path: Path, txt_dir: Path, lang: str = "fra+lat+grc+ell+syr"
) -> bool:
    """
    Verifica se a página foi processada corretamente.
    """
    page_txt_path = txt_dir / (img_path.stem + ".txt")
    if not page_txt_path.exists():
        print(f"[VERIFY] {img_path.name} — arquivo de texto não encontrado")
        return False

    txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
    if len(txt.strip()) == 0:
        print(f"[VERIFY] {img_path.name} — texto vazio")
        return False

    tesseract_db = open_tesseract_cache_db()
    init_tesseract_cache(tesseract_db)

    tesseractres = run_tesseract_cached(tesseract_db, img_path, lang=lang)
    overlap = latin_overlap_result(txt, tesseractres, name=img_path.name)

    if (
        overlap.recall_ratio > 0.2 and overlap.recall_ratio <= 0.6
    ):  # LLM detectou texto latino, mas Tesseract só pegou parte → possível degradação ou script complexo
        print(
            f"[VERIFY] {img_path.name} — possível degradação/script complexo (Overlap: {overlap.overlap_ratio:.2f}, Recall: {overlap.recall_ratio:.2f})"
        )

    if (
        overlap.recall_ratio < 0.1 and overlap.overlap_ratio < 0.1
    ):  # LLM detectou muito texto latino, mas Tesseract quase nada → provável omissão ou alucinação
        # Debug prints para identificar o texto dos dois lados e o arquivo no terminal:

        page_stats = classify_scan_page(
            img_path, max_dim=2000, border_frac=0.08, center_frac=0.5
        )

        if is_library_label(txt):
            print(f"[VERIFY] {img_path.name} — é etiqueta de biblioteca")
            return True

        if overlap.tess_token_count < 10:
            print(f"[VERIFY] {img_path.name} — Tesseract detectou poucos tokens")
            return True

        print(
            f"[VERIFY] Resultado RAW {img_path.name}; Classificação OpenCV2: {page_stats.classification}; Overlap ratio: {overlap.overlap_ratio:.2f}; Recall ratio: {overlap.recall_ratio:.2f} (LLM): {txt}; (Tesseract): {tesseractres}"
        )

        is_cover = (
            'tipo="capa_ou_guarda"' in txt or "<pagina>" in txt or 'estado="vazio"' in txt
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

            print(f"[VERIFY] {img_path.name} — ruído OCR detectado: {noise}, {score}, llm token count: {overlap.llm_token_count}")
            if is_cover and overlap.llm_token_count < 30 and noise:
                print(f"[VERIFY] {img_path.name} — provavelmente é uma capa mesmo; Ignorando página")
                return True

        return False

    return True


def llm_process_image_autoretry(
    image_path: Path,
    retries: int = 10,
    model: str = DEFAULT_LLM_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    prompt: str = PROMPT,
):
    txt = ""
    contagem_vazio = 0
    for i in range(retries):
        try:
            txt = ollama_process_image(
                image_path, model=model, url=url, current_try=i + 1, prompt=prompt
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

    if len(txt) > 0:
        return txt

    raise RuntimeError("All attempts failed: " + image_path.name)


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


def ocr_tesseract(img_path: Image, lang: str = DEFAULT_LANG) -> str:
    with Image.open(img_path) as pil_im:
        # converter para cv2 BGR, pré-processar e voltar para PIL
        im_bgr = cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)
        im_pre = preprocess_adaptative_ocr(im_bgr)
        pil_pre = Image.fromarray(im_pre)

        # OCR
        txt = pytesseract.image_to_string(
            pil_pre,
            lang=lang,
            config="--psm 3 --oem 1",  # psm 3 = layout automático, oem 1 = LSTM
        )

    return txt


def run_tesseract_cached(con: sqlite3.Connection, image_path: Path, lang: str) -> str:
    cached = get_tesseract_cached(con, image_path, lang)
    if cached is not None:
        return cached

    result = ocr_tesseract(image_path, lang=lang)

    h = hashlib.sha256(image_path.read_bytes()).hexdigest()
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
        page_txt_path = txt_dir / (img_path.stem + ".txt")
        if page_txt_path.exists():
            txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
            all_text_chunks.append(txt)
            continue

        # leitura streaming, garantindo liberação de memória
        if algorithm == "ollama":
            txt = llm_process_image_autoretry(
                img_path, model=llm_model, url=ollama_url, prompt=prompt
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
) -> str:
    start_total = time.time()

    # Obter informações do processo atual
    current_pid = os.getpid()
    process_index = get_current_process_index()
    omp_places = os.environ.get("OMP_PLACES", "not set")

    print(
        f"[{time.strftime('%H:%M:%S')}] [START] {img_path.name} (PID: {current_pid}, Index: {process_index}, OMP_PLACES: {omp_places})"
    )

    page_txt_path = txt_dir / (img_path.stem + ".txt")
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

        if len(txt.strip()) == 0:
            print(f"[{time.strftime('%H:%M:%S')}] [WARN] {img_path.name} (txt vazio)")
        else:
            print(
                f"[{time.strftime('%H:%M:%S')}] [SKIP] {img_path.name} (txt já existe)"
            )
            return txt

    if algorithm == "ollama":
        t4 = time.time()
        txt = llm_process_image_autoretry(img_path, model=llm_model, url=ollama_url)
        t5 = time.time()
        print(
            f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM OCR: {t5 - t4:.3f}s"
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
    save_all_text_path: Optional[Path] = None,
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
        choices=["tesseract", "ollama"],
        default="tesseract",
        help="Escolhe engine: tesseract (default) ou ollama.",
    )
    ap.add_argument(
        "--llm-model",
        type=str,
        default=DEFAULT_LLM_MODEL,
        help=f"Modelo usado quando --algorithm=ollama (default: {DEFAULT_LLM_MODEL}).",
    )
    ap.add_argument(
        "--ollama-url",
        type=str,
        default=DEFAULT_OLLAMA_URL,
        help=f"Endpoint Ollama (default: {DEFAULT_OLLAMA_URL}).",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help="Verifica a integridade dos textos comparando resultados .txt prontos com o Tesseract.",
    )
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF não encontrado: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    base_out = Path(args.out) / pdf_path.stem
    images_dir = base_out / "images"
    text_dir = base_out / "text"
    ensure_dir(base_out)

    print("Convertendo páginas para imagens (com cache em disco)...")
    images = pages_to_images(
        pdf_path, images_dir, dpi=args.dpi, first_page=args.first, last_page=args.last
    )
    print(f"Total de imagens: {len(images)}")

    concat_path = (base_out / "texto_extraido.txt") if args.concat else None

    if args.verify:
        print("Verificando integridade dos textos...")
        verify_all_parallel(
            images,
            text_dir,
            lang=args.lang,
            processes=args.procs,
            omp_threads_per_proc=args.omp_threads,
            chunksize=args.chunksize,
            maxtasksperchild=args.maxtasksperchild,
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
        save_all_text_path=concat_path,
    )
    print("Concluído.")


if __name__ == "__main__":
    main()
