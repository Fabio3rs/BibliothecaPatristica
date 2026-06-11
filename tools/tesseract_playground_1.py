#!/usr/bin/env python3
"""Pequeno playground para testar Tesseract + pré-processamento OpenCV.

Objetivos:
- aplicar erosão leve para separar linhas que estão tocando
- permitir configurar --psm e variáveis -c do Tesseract
- preview antes/depois com OpenCV e salvar imagem / texto

Uso exemplo:
  python tools/tesseract_playground.py pagina.png --psm 6 \
      --config textord_parallel_baselines=1 --erosion-iters 1 --show

Se a entrada for PDF, use --dpi para controlar a conversão (padrão 300).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import List

import cv2
import numpy as np
import pytesseract
from kraken import blla
from kraken.lib import models
from PIL import Image
import cv2
import os
import tempfile
import math


try:
    # pdf2image é opcional — usamos somente quando a entrada for PDF
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None  # type: ignore


DEFAULT_DPI = 300
DEFAULT_LANG = "lat+grc"


@dataclass
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int
    col: str = "?"

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def read_input(path: Path, dpi: int, page: int | None = None) -> np.ndarray:
    """Lê uma imagem do disco; se for PDF, converte a página (pdf2image).

    Retorna imagem BGR (OpenCV).
    """
    if path.suffix.lower() in (".pdf",):
        if convert_from_path is None:
            raise RuntimeError(
                "pdf2image não está disponível; instale pdf2image para ler PDFs"
            )
        pages = convert_from_path(str(path), dpi=dpi)
        if not pages:
            raise FileNotFoundError(f"nenhuma página extraída de {path}")
        idx = page or 1
        if idx < 1 or idx > len(pages):
            raise IndexError(f"página {idx} fora do intervalo (1..{len(pages)})")
        pil = pages[idx - 1]
        arr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        return arr

    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"Não consegui ler imagem: {path}")
    return img


def ensure_gray(img: np.ndarray) -> np.ndarray:
    return img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def apply_erode(img: np.ndarray, ksize: int, iterations: int) -> np.ndarray:
    gray = ensure_gray(img)
    kernel = np.ones((ksize, ksize), np.uint8)
    return cv2.erode(gray, kernel, iterations=iterations)


def text_contours(img: np.ndarray):
    gray = ensure_gray(img)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((5, 5), np.uint8)
    dilation = cv2.dilate(thresh, kernel, iterations=1)

    contours, _ = cv2.findContours(dilation, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # Aqui você tem sua caixa de texto aproximada
        # print((x, y, w, h))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)

    return img


def text_contours_arr(img: np.ndarray):
    gray = ensure_gray(img)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((5, 5), np.uint8)
    dilation = cv2.dilate(thresh, kernel, iterations=1)

    contours, _ = cv2.findContours(dilation, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # Aqui você tem sua caixa de texto aproximada
        # print((x, y, w, h))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)

    return contours


def fill_text_blocks(img: np.ndarray):
    # kernel = np.ones((5,5),np.float32)/25
    # img = cv2.filter2D(img,-1,kernel)
    gray = ensure_gray(img)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((4, 5), np.uint8)
    dilation = cv2.dilate(thresh, kernel, iterations=2)

    contours, _ = cv2.findContours(dilation, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # cv2.floodFill(image=img, mask=None, seedPoint=(0, 0), newVal=(255, 255, 255))
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # Aqui você tem sua caixa de texto aproximada
        # print((x, y, w, h))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), -1)

    # cv2.floodFill(image=img, mask=None, seedPoint=(0, 0), newVal=(255, 255, 255))
    return contours, img


def show_preview(before: np.ndarray, after: np.ndarray, title: str = "Preview") -> None:
    contours = text_contours(after.copy())

    # converte para BGR 3-ch se necessário
    def to_bgr(i: np.ndarray) -> np.ndarray:
        return i if len(i.shape) == 3 else cv2.cvtColor(i, cv2.COLOR_GRAY2BGR)

    b = to_bgr(before)
    a = to_bgr(after)
    c = to_bgr(contours)
    h = max(b.shape[0], a.shape[0])
    w = max(b.shape[1], a.shape[1])

    # pad
    def pad(img):
        ih, iw = img.shape[:2]
        canvas = np.zeros((h, w, 3), dtype=img.dtype)
        canvas[:ih, :iw] = img
        return canvas

    grid = cv2.hconcat([pad(b), pad(a), pad(c)])
    cv2.putText(grid, "before", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(
        grid, "after", (w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2
    )
    cv2.putText(
        grid,
        "contours",
        (2 * w + 10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
    )
    cv2.imshow(title + " (ESC para fechar)", grid)
    key = cv2.waitKey(0)
    if key == 27:
        cv2.destroyAllWindows()


def build_tesseract_config(psm: int, configs: List[str]) -> str:
    # configs are like ['k=v', 'a=b']
    # --tessdata-dir . aponta para os modelos baixados localmente (legacy+LSTM)
    # --oem 2 = Legacy + LSTM combinado, requer os traineddata completos locais
    cfg_parts = [f"--psm {psm}", "--oem 2", "--tessdata-dir ."]
    for c in configs:
        c = c.strip()
        if not c:
            continue
        # allow user to pass either 'k=v' or '-c k=v'
        if c.startswith("-c "):
            cfg_parts.append(c)
        else:
            cfg_parts.append(f"-c {c}")
    return " ".join(cfg_parts)


def run_tesseract(img: np.ndarray, lang: str, psm: int, configs: List[str]) -> str:
    cfg = build_tesseract_config(psm, configs)
    # pytesseract expects an image (PIL or numpy). We pass the array (grayscale ok).
    # Garantir que é grayscale ou BGR conforme necessário
    try:
        text = pytesseract.image_to_data(
            img, lang=lang, config=cfg, output_type=pytesseract.Output.STRING
        )
    except pytesseract.TesseractError as e:
        # fallback: tentar sem configs
        print(f"TesseractError: {e}; tentando sem configs...", file=sys.stderr)
        text = pytesseract.image_to_string(img, lang=lang)
    return text


def parse_tsv(tsv: str, conf_thr=0.1) -> List[Word]:
    words: list[Word] = []
    rows = tsv.splitlines()
    if not rows:
        return words
    header = rows[0].split("\t")
    ci = {h: i for i, h in enumerate(header)}
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) < len(header):
            continue
        text = cols[ci["text"]].strip()
        if not text:
            continue
        try:
            conf = float(cols[ci["conf"]])
        except ValueError:
            conf = -1.0
        if conf < conf_thr:
            continue
        try:
            x = int(cols[ci["left"]])
            y = int(cols[ci["top"]])
            w = int(cols[ci["width"]])
            h = int(cols[ci["height"]])
        except ValueError:
            continue
        if w <= 0 or h <= 0:
            continue
        words.append(Word(text=text, conf=conf, x=x, y=y, w=w, h=h))
    return words


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Imagem ou PDF de entrada")
    p.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="DPI para conversão de PDF (default: 300)",
    )
    p.add_argument("--page", type=int, help="Se PDF, página a extrair (1-based)")
    p.add_argument("--psm", type=int, default=6, help="PSM do Tesseract (default: 6)")
    p.add_argument(
        "--lang", default=DEFAULT_LANG, help="Idioma para Tesseract (default: lat)"
    )
    p.add_argument(
        "--config",
        action="append",
        default=[],
        help="Config Tesseract no formato key=value; pode repetir",
    )
    p.add_argument(
        "--erosion-iters",
        type=int,
        default=0,
        help="Número de iterações de erosão (0 = sem erosão)",
    )
    p.add_argument(
        "--erosion-ksize",
        type=int,
        default=2,
        help="Tamanho do kernel quadrado da erosão (p.ex. 2)",
    )
    p.add_argument(
        "--adaptive",
        action="store_true",
        help="Binarização adaptativa (CLAHE+adaptiveThreshold) em vez de Otsu global. "
        "Recomendado para scans com iluminação não uniforme ou papel amarelado. "
        "Para impressos antigos de alto contraste, Otsu (padrão) é melhor.",
    )
    p.add_argument(
        "--show", action="store_true", help="Mostra preview antes/depois com OpenCV"
    )
    p.add_argument("--save-image", help="Salvar imagem processada (após erosão)")
    p.add_argument("--save-text", help="Salvar saída OCR em arquivo")
    return p


def fusion_colliding_opencv_contours(img, contours):
    """Funde contornos próximos/colidentes de forma conservadora.

    Em vez de fazer uma dilatação ampla que pode unir caixas distantes,
    usamos uma fusão iterativa de bounding boxes: duas caixas são
    mescladas somente se se sobrepõem ou estiverem a uma distância
    horizontal/vertical pequena (threshold adaptativo). Isso evita unir
    caixas de linhas distintas que não se tocam.

    Retorna: (merged_contours, annotated_image)
    """
    # prepara imagem para desenhar (BGR)
    out_img = (
        img.copy()
        if len(img.shape) == 3
        else cv2.cvtColor(img.copy(), cv2.COLOR_GRAY2BGR)
    )

    if not contours:
        return [], out_img

    h, w = img.shape[:2]
    boxes = [cv2.boundingRect(c) for c in contours]

    # tamanhos medianos para heurísticas
    heights = [b[3] for b in boxes]
    widths = [b[2] for b in boxes]
    med_h = int(np.median(heights)) if heights else 10
    med_w = int(np.median(widths)) if widths else 10

    # detecta colunas por gaps grandes em centers_x e mescla só dentro da mesma coluna
    centers_x = [b[0] + b[2] / 2 for b in boxes]
    order_x = sorted(range(len(boxes)), key=lambda i: centers_x[i])
    sorted_cx = [centers_x[i] for i in order_x]

    # diffs entre centros X consecutivos
    diffs = [sorted_cx[i + 1] - sorted_cx[i] for i in range(len(sorted_cx) - 1)]
    if diffs:
        median_diff = float(np.median(diffs))
    else:
        median_diff = float(med_w)

    # gap threshold: considera um gap grande quando é muito maior que a mediana dos gaps
    col_gap_thresh = max(med_w * 2.5, median_diff * 3)

    # índices de splits onde diff > threshold
    splits: list[int] = []
    for i, d in enumerate(diffs):
        if d > col_gap_thresh:
            splits.append(i)

    # construir grupos de índices por coluna
    col_groups: list[list[int]] = []
    start = 0
    for s in splits:
        group_idxs = order_x[start : s + 1]
        col_groups.append(group_idxs)
        start = s + 1
    # remaining
    col_groups.append(order_x[start:])

    merged: list[tuple[int, int, int, int]] = []

    # parâmetros de fusão dentro de uma coluna
    max_gap_x = max(3, med_w // 8)
    max_vertical_overlap_neg = max(2, med_h // 6)

    for col in col_groups:
        if not col:
            continue
        # para cada coluna: agrupar por linhas (centro Y) somente com caixas da coluna
        col_centers_y = [boxes[i][1] + boxes[i][3] / 2 for i in col]
        # ordenar índices locais por cy
        local = sorted(range(len(col)), key=lambda k: col_centers_y[k])
        rows: list[list[int]] = []
        row_thresh = max(0.6 * med_h, 8)
        for li in local:
            idx = col[li]
            cy = boxes[idx][1] + boxes[idx][3] / 2
            if not rows:
                rows.append([idx])
                continue
            last = rows[-1]
            last_cys = [boxes[j][1] + boxes[j][3] / 2 for j in last]
            mean_last = sum(last_cys) / len(last_cys)
            if abs(cy - mean_last) <= row_thresh:
                last.append(idx)
            else:
                rows.append([idx])

        # dentro de cada linha local, mesclar horizontalmente semelhante ao anterior
        for row in rows:
            row_boxes = [boxes[i] for i in row]
            row_boxes.sort(key=lambda b: b[0])
            cur = row_boxes[0]
            for nb in row_boxes[1:]:
                ax, ay, aw, ah = cur
                bx, by, bw, bh = nb
                a_x2 = ax + aw
                b_x2 = bx + bw
                a_y2 = ay + ah
                b_y2 = by + bh
                vertical_overlap = min(a_y2, b_y2) - max(ay, by)
                gap_x = bx - a_x2 if bx > a_x2 else 0
                if (vertical_overlap > 0) or (
                    gap_x <= max_gap_x and vertical_overlap >= -max_vertical_overlap_neg
                ):
                    x1 = min(ax, bx)
                    y1 = min(ay, by)
                    x2 = max(a_x2, b_x2)
                    y2 = max(a_y2, b_y2)
                    cur = (x1, y1, x2 - x1, y2 - y1)
                else:
                    merged.append(cur)
                    cur = nb
            merged.append(cur)

    # desenha as caixas mescladas e cria máscara final
    mask = np.zeros((h, w), dtype=np.uint8)
    for x, y, bw, bh in merged:
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w - 1, int(x + bw))
        y1 = min(h - 1, int(y + bh))
        cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)

    # pequena dilatação para fechar artefatos minúsculos (1 px)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    mask = cv2.dilate(mask, kernel, iterations=1)

    merged_cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for mc in merged_cnts:
        rx, ry, rw_box, rh_box = cv2.boundingRect(mc)
        cv2.rectangle(out_img, (rx, ry), (rx + rw_box, ry + rh_box), (0, 255, 0), 2)

    return merged_cnts, out_img


def uniform_filter1d_np(arr: np.ndarray, size: int) -> np.ndarray:
    """Equivalente simples ao scipy.ndimage.uniform_filter1d."""
    kernel = np.ones(size) / size
    return np.convolve(arr, kernel, mode="same")


def find_band_breaks(img_bin: np.ndarray) -> list[int]:
    """
    Detecta mudanças de layout por densidade horizontal de tinta.
    Retorna lista de y onde ocorre quebra de banda.
    """
    img_bin = ensure_gray(img_bin)
    ink = (img_bin < 128).astype(np.float32)

    # Dilatar horizontalmente para fundir palavras em linhas sólidas
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (60, 1))
    dilated = cv2.dilate((ink * 255).astype(np.uint8), kernel)

    # Densidade por linha y
    density = dilated.sum(axis=1) / dilated.shape[1]
    smooth = uniform_filter1d_np(density, size=5)

    # Queda brusca de densidade = espaço entre seções
    breaks = []
    for y in range(1, len(smooth) - 1):
        if smooth[y] < smooth.max() * 0.05:  # linha quase vazia
            breaks.append(y)

    # Colapsar runs de linhas vazias em um único ponto
    merged = []
    for y in breaks:
        if not merged or y - merged[-1] > 20:
            merged.append(y)
        else:
            merged[-1] = y  # estende o break atual

    return merged


# Alternativa sem scipy.signal:
# Dividir a região central em N candidatos e pegar os N-1 menores
def find_valley_separators(smooth, n_cols_hint, margin, w):
    """Busca os n_cols_hint-1 vales mais profundos na região central."""
    center = smooth[margin : w - margin]
    # Sliding minimum com janela
    window = len(center) // (n_cols_hint * 4)
    candidates = []
    for x in range(window, len(center) - window):
        local_min = center[x] == min(center[x - window : x + window])
        if local_min:
            candidates.append((center[x], x + margin))
    candidates.sort()  # menor valor primeiro
    return [x for _, x in candidates[: n_cols_hint - 1]]


def detect_column_boundaries(img_bin, y1, y2, min_col_width=0.15):
    img_bin = ensure_gray(img_bin)
    h, w = img_bin.shape[:2]
    strip = img_bin[y1:y2, :]

    ink = (strip < 128).astype(np.float32)
    proj = ink.sum(axis=0) / (y2 - y1)

    kernel = max(20, (y2 - y1) // 15)
    smooth = uniform_filter1d_np(proj, size=kernel)

    margin = int(w * 0.05)
    smooth_inner = smooth[margin:w - margin]

    # Estatísticas da região interna
    p10 = float(np.percentile(smooth_inner, 10))  # vale típico
    p90 = float(np.percentile(smooth_inner, 90))  # pico típico
    dynamic_range = p90 - p10

    # Se a página tem pouco contraste (banda quase vazia ou quase cheia), pula
    if dynamic_range < 0.05:
        text_xs = np.where(smooth_inner > smooth_inner.max() * 0.3)[0]
        if len(text_xs) > 0:
            return [(margin + int(text_xs[0]), margin + int(text_xs[-1]))]
        return []

    smooth[:margin] = smooth.max()
    smooth[w - margin:] = smooth.max()

    from scipy.signal import find_peaks
    inverted = smooth.max() - smooth

    # Prominence relativa ao dynamic range real da banda
    prominence = dynamic_range * 0.25

    peaks, _ = find_peaks(
        inverted,
        prominence=prominence,
        width=int(w * 0.02),
        distance=int(w * 0.12),
    )

    separators = sorted(peaks)
    boundaries = [margin] + list(separators) + [w - margin]

    cols = []
    for i in range(len(boundaries) - 1):
        x1 = boundaries[i]
        x2 = boundaries[i + 1]
        if x2 - x1 > w * min_col_width:
            region = smooth[x1:x2]
            local_thr = region.max() * 0.30
            text_xs = np.where(region > local_thr)[0]
            if len(text_xs) > 0:
                cols.append((x1 + int(text_xs[0]), x1 + int(text_xs[-1])))

    return cols

def polygon_bbox(boundary):
	"""Return integer bbox (left, upper, right, lower) from polygon boundary."""
	xs = [p[0] for p in boundary]
	ys = [p[1] for p in boundary]
	left = int(math.floor(min(xs)))
	upper = int(math.floor(min(ys)))
	right = int(math.ceil(max(xs)))
	lower = int(math.ceil(max(ys)))
	return (left, upper, right, lower)


def save_line_crops(seg, src_image, out_dir=None, pad=2):
	"""Crop each detected line polygon and save to out_dir (created if None).

	Returns list of saved file paths.
	"""
	if out_dir is None:
		out_dir = tempfile.mkdtemp(prefix='pdfocr_crops_')
	os.makedirs(out_dir, exist_ok=True)

	saved = []
	for i, line in enumerate(seg.lines):
		if not getattr(line, 'boundary', None):
			continue
		bbox = polygon_bbox(line.boundary)
		# add small padding
		left = max(0, bbox[0] - pad)
		upper = max(0, bbox[1] - pad)
		right = min(src_image.width, bbox[2] + pad)
		lower = min(src_image.height, bbox[3] + pad)
		crop = src_image.crop((left, upper, right, lower))
		# create safe filename
		lid = getattr(line, 'id', f'{i}')
		fname = f'line_{i}_{lid}.png'
		path = os.path.join(out_dir, fname)
		crop.save(path)
		saved.append(path)
	return out_dir, saved


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Arquivo não encontrado: {path}", file=sys.stderr)
        sys.exit(2)

    img = read_input(path, dpi=args.dpi, page=args.page)

    processed = img
    if args.erosion_iters and args.erosion_iters > 0:
        processed = apply_erode(
            img, ksize=args.erosion_ksize, iterations=args.erosion_iters
        )

    border_size = 50

    # Converter pra grayscale
    arr = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # 1. REMOVER RUÍDO E JUNTAR LINHAS
    # Criamos um kernel largo para "derreter" as palavras em linhas horizontais
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=2)

    # 2. ENCONTRAR CONTORNOS
    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    angles = []
    for cnt in contours:
        # Ignorar ruídos pequenos e as bordas gigantescas do papel
        area = cv2.contourArea(cnt)
        if 500 < area < 50000:  # Ajuste esses valores conforme necessário
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            rw, rh = rect[1]

            # Normalização do ângulo para OpenCV 4.5+
            # minAreaRect retorna o ângulo do eixo mais curto.
            # Para linhas de texto horizontais (largura >> altura), o eixo
            # curto é vertical → ângulo fica em torno de -90°.
            # Corrigimos para obter o ângulo real da linha (próximo de 0°).
            if rw < rh:
                angle = angle + 90  # roda 90° para alinhar com o eixo longo
            # Após normalização, descarta ângulos absurdos (>10°): provavelmente
            # contornos de elementos decorativos, linhas de margem etc.
            if abs(angle) <= 10:
                angles.append(angle)

    # 3. MÉDIA DOS ÂNGULOS
    # Usamos a mediana para evitar que um contorno doido puxe o valor
    if len(angles) > 0:
        median_angle = np.median(angles)
    else:
        median_angle = 0.0  # Sem inclinação detectada

    print(f"Ângulo real detectado: {median_angle}")

    # 4. ROTACIONAR
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )

    # Binarização pós-rotação.
    # Para páginas com papel uniforme e alto contraste (impressos antigos bem
    # conservados), Otsu global preserva melhor os traços finos de itálico.
    # Para scans com iluminação não uniforme ou papel amarelado, use --adaptive.
    rotated_gray = (
        cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY)
        if len(rotated.shape) == 3
        else rotated
    )
    if args.adaptive:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        rotated_eq = clahe.apply(rotated_gray)
        thresh = cv2.adaptiveThreshold(
            rotated_eq,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=15,
            C=10,
        )
        # Remove ruído de poros do papel sem destruir caracteres pequenos
        denoise_k = np.ones((2, 2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, denoise_k)
        print("Binarização: adaptativa (CLAHE + adaptiveThreshold)")
    else:
        _, thresh = cv2.threshold(
            rotated_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        print("Binarização: Otsu global")

    textc = text_contours_arr(rotated)

    testimg = rotated.copy()
    textblocks, imgfilled = fill_text_blocks(testimg)

    imgfilled = ensure_gray(img=imgfilled)

    segmentation = blla.segment(Image.fromarray(imgfilled))

    bandbreaks = []#find_band_breaks(imgfilled)

    for line in segmentation.lines:
        bandbreaks.append(line.baseline[0][1])

    print(
        "--------------------------------------------------------------------------------------"
    )
    print(bandbreaks)

    h = imgfilled.shape[0]
    boundaries = [0] + bandbreaks + [h]

    detected_cols = []

    for i in range(len(boundaries) - 1):
        y1 = boundaries[i]
        y2 = boundaries[i + 1]
        # Pular bandas muito finas (os próprios separadores)
        if y2 - y1 < 50:
            continue
        cols = detect_column_boundaries(thresh, y1, y2)
        # armazenar como (x1, y1, x2, y2) para simplificar fusão e desenho
        for col in cols:
            x1, x2 = int(col[0]), int(col[1])
            detected_cols.append((x1, y1, x2, y2))
        print(f"banda y=[{y1}..{y2}] ({y2-y1}px) → {len(cols)} colunas: {cols}")

    print(
        "--------------------------------------------------------------------------------------"
    )

    """
    --------------------------------------------------------------------------------------
[38, 1310, 1360, 2235, 3507]
banda y=[38..1310] (1272px) → 3 colunas: [(np.int64(240), np.int64(587)), (np.int64(588), np.int64(1231)), (np.int64(1232), np.int64(2226))]
banda y=[1310..1360] (50px) → 1 colunas: [(1206, 1253)]
banda y=[1360..2235] (875px) → 3 colunas: [(np.int64(245), np.int64(896)), (np.int64(911), np.int64(1565)), (np.int64(1582), np.int64(2232))]
banda y=[2235..3507] (1272px) → 3 colunas: [(np.int64(257), np.int64(901)), (np.int64(902), np.int64(1567)), (np.int64(1568), np.int64(2242))]
--------------------------------------------------------------------------------------
    """

    # Agrupar detected_cols por posição horizontal (centro X) e
    # mesclar verticalmente dentro de cada grupo.
    # detected_cols tem itens (x1,y1,x2,y2)
    groups: list[dict] = []  # cada grupo: {'cx': float, 'boxes': [ (x1,y1,x2,y2) ]}
    for x1, y1, x2, y2 in detected_cols:
        cx = (x1 + x2) / 2
        cv2.rectangle(rotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
        placed = False
        for g in groups:
            # tolerância baseada na largura média do grupo (ou um mínimo)
            gw = np.mean([b[2] - b[0] for b in g['boxes']]) if g['boxes'] else (x2 - x1)
            tol = max(40, gw * 0.3)
            if abs(cx - g['cx']) <= tol:
                g['boxes'].append((x1, y1, x2, y2))
                # atualizar centro do grupo
                all_cx = [ (b[0] + b[2]) / 2 for b in g['boxes'] ]
                g['cx'] = sum(all_cx) / len(all_cx)
                placed = True
                break
        if not placed:
            groups.append({'cx': cx, 'boxes': [(x1, y1, x2, y2)]})

    # Agora, para cada grupo, mesclar verticalmente caixas que se tocam ou têm pequeno gap
    merged_cols: list[tuple[int,int,int,int]] = []
    for g in groups:
        boxes = sorted(g['boxes'], key=lambda b: b[1])  # ordenar por y1
        cur = None
        for b in boxes:
            bx1, by1, bx2, by2 = b
            if cur is None:
                cur = [bx1, by1, bx2, by2]
                continue
            cx1, cy1, cx2, cy2 = cur
            # se houver sobreposição vertical ou pequeno gap, mesclar
            vertical_overlap = min(cy2, by2) - max(cy1, by1)
            gap = by1 - cy2
            if vertical_overlap > -10 or gap <= 30:
                cur[0] = min(cx1, bx1)
                cur[2] = max(cx2, bx2)
                cur[3] = max(cy2, by2)
            else:
                merged_cols.append(tuple(cur))
                cur = [bx1, by1, bx2, by2]
        if cur is not None:
            merged_cols.append(tuple(cur))

    print(merged_cols, groups)

    # Desenhar contornos das colunas (x1,y1)->(x2,y2)
    for col in merged_cols:
        x1, y1, x2, y2 = map(int, col)
        # cv2.rectangle(rotated, (x1, y1), (x2, y2), (0, 255, 0), 2)

    # merged_contours, annotated_image = fusion_colliding_opencv_contours(rotated, textc)

    # Adiciona a borda branca (o valor [255, 255, 255] é o branco em BGR)
    # processed = cv2.copyMakeBorder(
    #     thresh,
    #     top=border_size,
    #     bottom=border_size,
    #     left=border_size,
    #     right=border_size,
    #     borderType=cv2.BORDER_CONSTANT,
    #     value=[255, 255, 255]
    # )

    processed = thresh

    if args.save_image:
        # salvar a imagem processada como PNG
        Path(args.save_image).parent.mkdir(parents=True, exist_ok=True)
        # garantir 3 canais se necessário
        out = (
            rotated
            if len(rotated.shape) == 3
            else cv2.cvtColor(rotated, cv2.COLOR_GRAY2BGR)
        )
        cv2.imwrite(str(args.save_image), out)
        print(f"Imagem processada salva em {args.save_image}")

    if args.show:
        show_preview(rotated, processed)

    return

    text = run_tesseract(processed, lang=args.lang, psm=args.psm, configs=args.config)

    parsed = parse_tsv(text)

    print(parsed)

    return
    if args.save_text:
        Path(args.save_text).parent.mkdir(parents=True, exist_ok=True)

        if isinstance(text, str):
            Path(args.save_text).write_text(text, encoding="utf-8")
        else:
            Path(args.save_text).write_text(
                json.dumps(text, ensure_ascii=False), encoding="utf-8"
            )
        print(f"Texto salvo em {args.save_text}")
    else:
        print(text)


if __name__ == "__main__":
    main()
