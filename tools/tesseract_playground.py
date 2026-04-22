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
import json
from pathlib import Path
import sys
from typing import List

import cv2
import numpy as np
import pytesseract

try:
    # pdf2image é opcional — usamos somente quando a entrada for PDF
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None  # type: ignore


DEFAULT_DPI = 300
DEFAULT_LANG = "lat+grc"


def read_input(path: Path, dpi: int, page: int | None = None) -> np.ndarray:
    """Lê uma imagem do disco; se for PDF, converte a página (pdf2image).

    Retorna imagem BGR (OpenCV).
    """
    if path.suffix.lower() in (".pdf",):
        if convert_from_path is None:
            raise RuntimeError("pdf2image não está disponível; instale pdf2image para ler PDFs")
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


def show_preview(before: np.ndarray, after: np.ndarray, title: str = "Preview") -> None:
    # converte para BGR 3-ch se necessário
    def to_bgr(i: np.ndarray) -> np.ndarray:
        return i if len(i.shape) == 3 else cv2.cvtColor(i, cv2.COLOR_GRAY2BGR)

    b = to_bgr(before)
    a = to_bgr(after)
    h = max(b.shape[0], a.shape[0])
    w = max(b.shape[1], a.shape[1])
    # pad
    def pad(img):
        ih, iw = img.shape[:2]
        canvas = np.zeros((h, w, 3), dtype=img.dtype)
        canvas[:ih, :iw] = img
        return canvas

    grid = cv2.hconcat([pad(b), pad(a)])
    cv2.putText(grid, "before", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(grid, "after", (w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow(title + " (ESC para fechar)", grid)
    key = cv2.waitKey(0)
    if key == 27:
        cv2.destroyAllWindows()


def build_tesseract_config(psm: int, configs: List[str]) -> str:
    # configs are like ['k=v', 'a=b']
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
        text = pytesseract.image_to_data(img, lang=lang, config=cfg, output_type=pytesseract.Output.STRING)
    except pytesseract.TesseractError as e:
        # fallback: tentar sem configs
        print(f"TesseractError: {e}; tentando sem configs...", file=sys.stderr)
        text = pytesseract.image_to_string(img, lang=lang)
    return text



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




def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Imagem ou PDF de entrada")
    p.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="DPI para conversão de PDF (default: 300)")
    p.add_argument("--page", type=int, help="Se PDF, página a extrair (1-based)")
    p.add_argument("--psm", type=int, default=6, help="PSM do Tesseract (default: 6)")
    p.add_argument("--lang", default=DEFAULT_LANG, help="Idioma para Tesseract (default: lat)")
    p.add_argument("--config", action="append", default=[], help="Config Tesseract no formato key=value; pode repetir")
    p.add_argument("--erosion-iters", type=int, default=0, help="Número de iterações de erosão (0 = sem erosão)")
    p.add_argument("--erosion-ksize", type=int, default=2, help="Tamanho do kernel quadrado da erosão (p.ex. 2)")
    p.add_argument("--show", action="store_true", help="Mostra preview antes/depois com OpenCV")
    p.add_argument("--save-image", help="Salvar imagem processada (após erosão)")
    p.add_argument("--save-text", help="Salvar saída OCR em arquivo")
    return p


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
        processed = apply_erode(img, ksize=args.erosion_ksize, iterations=args.erosion_iters)

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
        if 500 < area < 50000: # Ajuste esses valores conforme necessário
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            
            # Normalização do ângulo para OpenCV 4.5+
            if angle > 45:
                angle = angle - 90
            angles.append(angle)

    # 3. MÉDIA DOS ÂNGULOS
    # Usamos a mediana para evitar que um contorno doido puxe o valor
    if len(angles) > 0:
        median_angle = np.median(angles)
    else:
        median_angle = 0.0 # Sem inclinação detectada

    print(f"Ângulo real detectado: {median_angle}")

    # 4. ROTACIONAR
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    _, thresh = cv2.threshold(rotated, 100, 255, cv2.THRESH_BINARY)

    # Adiciona a borda branca (o valor [255, 255, 255] é o branco em BGR)
    processed = cv2.copyMakeBorder(
        thresh, 
        top=border_size, 
        bottom=border_size, 
        left=border_size, 
        right=border_size, 
        borderType=cv2.BORDER_CONSTANT, 
        value=[255, 255, 255]
    )

    if args.show:
        show_preview(img, processed)

    text = run_tesseract(processed, lang=args.lang, psm=args.psm, configs=args.config)

    if args.save_text:
        Path(args.save_text).parent.mkdir(parents=True, exist_ok=True)

        if isinstance(text, str):
            Path(args.save_text).write_text(text, encoding="utf-8")
        else:
            Path(args.save_text).write_text(json.dumps(text, ensure_ascii=False), encoding="utf-8")
        print(f"Texto salvo em {args.save_text}")
    else:
        print(text)

    if args.save_image:
        # salvar a imagem processada como PNG
        Path(args.save_image).parent.mkdir(parents=True, exist_ok=True)
        # garantir 3 canais se necessário
        out = processed if len(processed.shape) == 3 else cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
        cv2.imwrite(str(args.save_image), out)
        print(f"Imagem processada salva em {args.save_image}")


if __name__ == "__main__":
    main()
