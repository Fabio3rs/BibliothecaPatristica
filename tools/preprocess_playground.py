#!/usr/bin/env python3
"""Playground pequeno para testar apenas o pré-processamento.

Uso:
  python tools/preprocess_playground.py imagem.png --show
  python tools/preprocess_playground.py imagem.png --mode sample --save out.png
  python tools/preprocess_playground.py pagina.pdf --page 1 --mode adaptive --show

Modos:
  - sample: usa a função preprocess_image de sample.py
  - adaptive: usa CLAHE + adaptiveThreshold, sem rotação
  - adaptive_soft: versão mais suave de CLAHE + adaptiveThreshold
  - otsu: usa Otsu global, sem rotação
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None  # type: ignore

try:
    from sample import preprocess_image
except Exception:
    preprocess_image = None


def read_input(path: Path, dpi: int, page: int | None = None) -> np.ndarray:
    if path.suffix.lower() == ".pdf":
        if convert_from_path is None:
            raise RuntimeError("pdf2image não está disponível")
        pages = convert_from_path(str(path), dpi=dpi)
        if not pages:
            raise FileNotFoundError(f"nenhuma página extraída de {path}")
        idx = page or 1
        if idx < 1 or idx > len(pages):
            raise IndexError(f"página {idx} fora do intervalo (1..{len(pages)})")
        return cv2.cvtColor(np.array(pages[idx - 1]), cv2.COLOR_RGB2BGR)

    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"não consegui ler imagem: {path}")
    return img


def to_bgr(img: np.ndarray) -> np.ndarray:
    return img if len(img.shape) == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def pad_to(img: np.ndarray, h: int, w: int) -> np.ndarray:
    canvas = np.zeros((h, w, 3), dtype=img.dtype)
    ih, iw = img.shape[:2]
    canvas[:ih, :iw] = to_bgr(img)
    return canvas


def show_before_after(before: np.ndarray, after: np.ndarray, title: str) -> None:
    b = to_bgr(before)
    a = to_bgr(after)
    h = max(b.shape[0], a.shape[0])
    w = max(b.shape[1], a.shape[1])
    grid = cv2.hconcat([pad_to(b, h, w), pad_to(a, h, w)])
    cv2.putText(grid, "before", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(grid, "after", (w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow(title, grid)
    key = cv2.waitKey(0)
    if key == 27:
        cv2.destroyAllWindows()


def preprocess_adaptive(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 10
    )
    kernel = np.ones((2, 2), np.uint8)
    return cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)


def preprocess_adaptive_soft(img_bgr: np.ndarray, block_size: int, c_value: int, clahe_clip: float) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # gray = cv2.medianBlur(gray, 3)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c_value
    )
    # kernel = np.ones((2, 2), np.uint8)
    # binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return binary


def preprocess_otsu(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Imagem ou PDF de entrada")
    p.add_argument("--dpi", type=int, default=300, help="DPI para PDF")
    p.add_argument("--page", type=int, help="Página do PDF (1-based)")
    p.add_argument(
        "--mode",
        choices=["sample", "adaptive", "adaptive_soft", "otsu"],
        default="adaptive_soft",
        help="Receita de pré-processamento",
    )
    p.add_argument("--block-size", type=int, default=31, help="Block size para adaptiveThreshold (ímpar)")
    p.add_argument("--c-value", type=int, default=4, help="C para adaptiveThreshold")
    p.add_argument("--clahe-clip", type=float, default=2.0, help="ClipLimit do CLAHE")
    p.add_argument("--show", action="store_true", help="Mostra antes/depois")
    p.add_argument("--save", help="Salva a imagem processada")
    return p


def main() -> None:
    args = build_parser().parse_args()
    path = Path(args.input)
    if not path.exists():
        print(f"Arquivo não encontrado: {path}", file=sys.stderr)
        raise SystemExit(2)

    img = read_input(path, dpi=args.dpi, page=args.page)

    if args.mode == "sample":
        if preprocess_image is None:
            raise RuntimeError("sample.preprocess_image não está disponível")
        processed, _ = preprocess_image(img)
    elif args.mode == "adaptive":
        processed = preprocess_adaptive(img)
    elif args.mode == "adaptive_soft":
        processed = preprocess_adaptive_soft(img, args.block_size, args.c_value, args.clahe_clip)
    else:
        processed = preprocess_otsu(img)

    if args.show:
        show_before_after(img, processed, f"preprocess ({args.mode})")

    if args.save:
        out = to_bgr(processed)
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(args.save, out)
        print(f"Imagem salva em {args.save}")
    else:
        print(f"Processamento concluído: modo={args.mode}")


if __name__ == "__main__":
    main()
