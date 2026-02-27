#!/usr/bin/env python3
"""Compara cor predominante nas bordas/margem vs centro de páginas escaneadas.

Útil para detectar capas monocromáticas ou páginas com vinhetas/escurecimento.

Exemplo:
    python tools/color_band_compare.py teste/PO006/images/4c78809c-1ec7-47fd-a989-430be3a9acd5-724.png
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np


def read_image(path: Path, max_dim: int) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    scale = max_dim / max(h, w) if max(h, w) > max_dim else 1.0
    if scale != 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def region_coords(h: int, w: int, border_frac: float, center_frac: float) -> Tuple[slice, slice, slice, slice]:
    bw = int(min(h, w) * border_frac)
    ch = int(h * center_frac)
    cw = int(w * center_frac)
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    center_y = slice(y0, y0 + ch)
    center_x = slice(x0, x0 + cw)
    border_y = slice(None)
    border_x = slice(None)
    return border_y, border_x, center_y, center_x, bw


def delta_e(lab1: np.ndarray, lab2: np.ndarray) -> float:
    # CIE76
    diff = lab1.astype(np.float32) - lab2.astype(np.float32)
    return float(np.sqrt((diff**2).sum()))


def compare(image: np.ndarray, border_frac: float, center_frac: float) -> dict:
    h, w = image.shape[:2]
    border_y, border_x, cy, cx, bw = region_coords(h, w, border_frac, center_frac)

    # masks: border = tudo menos centro erodido por bw
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :] = 1
    mask[cy, cx] = 0
    # erode to avoid counting center in border; thickness = bw
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.erode(mask, kernel, iterations=bw // 3 if bw >= 3 else 0)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    border_lab = lab[mask == 1]
    center_lab = lab[mask == 0]

    border_mean = border_lab.mean(axis=0)
    center_mean = center_lab.mean(axis=0)
    dE = delta_e(border_mean, center_mean)

    # Histograma de matiz para avaliar variação
    border_h = hsv[:, :, 0][mask == 1]
    center_h = hsv[:, :, 0][mask == 0]
    hist_border, _ = np.histogram(border_h, bins=30, range=(0, 180), density=True)
    hist_center, _ = np.histogram(center_h, bins=30, range=(0, 180), density=True)
    hist_corr = float(np.corrcoef(hist_border, hist_center)[0, 1])

    return {
        "border_mean_lab": border_mean,
        "center_mean_lab": center_mean,
        "deltaE": dE,
        "hist_corr": hist_corr,
        "border_frac": border_frac,
        "center_frac": center_frac,
    }


def format_lab(lab_vec: np.ndarray) -> str:
    L, a, b = lab_vec
    return f"L={L:6.2f}, a={a:6.2f}, b={b:6.2f}"


def run(args: argparse.Namespace) -> None:
    for img_path in args.images:
        img = read_image(Path(img_path), args.max_dim)
        result = compare(img, args.border_frac, args.center_frac)
        print(f"\n{img_path}")
        print(f"  mean border (LAB): {format_lab(result['border_mean_lab'])}")
        print(f"  mean center (LAB): {format_lab(result['center_mean_lab'])}")
        print(f"  ΔE border-center : {result['deltaE']:.3f}")
        print(f"  Hue hist corr    : {result['hist_corr']:.3f} (1.0 = idêntico)")

        if args.show:
            h, w = img.shape[:2]
            _, _, cy, cx, bw = region_coords(h, w, args.border_frac, args.center_frac)
            vis = img.copy()
            # center rectangle
            cv2.rectangle(vis, (cx.start, cy.start), (cx.stop, cy.stop), (0, 255, 0), 2)
            # outer border guide
            cv2.rectangle(vis, (bw, bw), (w - bw, h - bw), (0, 0, 255), 2)
            cv2.imshow("border vs center", vis)
            cv2.waitKey(0)
            cv2.destroyAllWindows()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("images", nargs="+", help="Arquivos de imagem")
    p.add_argument("--border-frac", type=float, default=0.08, help="Largura da borda como fração do menor lado (default 0.08)")
    p.add_argument("--center-frac", type=float, default=0.5, help="Tamanho do centro como fração (default 0.5)")
    p.add_argument("--max-dim", type=int, default=2000, help="Maior lado para amostragem (default 2000px)")
    p.add_argument("--show", action="store_true", help="Mostra regiões destacadas")
    return p


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
